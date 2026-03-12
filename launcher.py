"""
KAPPA ROADMAP — Deployment Manager v2.1
Cross-platform (Windows + Linux) — Python 3.8+
Single-file .pyz portable packaging
"""
import subprocess, shutil, zipfile, os, sys, json, io, re
from pathlib import Path
from datetime import datetime
from collections import deque
import threading, webbrowser, urllib.request

# ── CONSTANTS ─────────────────────────────────────────────────────────────────
APP_NAME       = "kappa-roadmap"
CONTAINER_NAME = "kappa-roadmap"
DB_FILE        = "data/roadmap.json"
COMPOSE        = "docker-compose.yml"
BACKUPS_DIR    = "backups"
DESTRUCTION_LOG = ".destruction-log"
CONFIG_FILE    = ".launcher-config.json"
MAX_BACKUPS_SHOWN = 5
SPARK_BLOCKS   = " ▁▂▃▄▅▆▇█"          # 9 levels (index 0 = empty)

DEFAULT_CONFIG = {
    "poll_interval": 1000,               # ms — data collection
    "port": 3000,
    "auto_open_browser": True,
    "sparkline_width": 50,               # samples in history
}

# ── THEME ─────────────────────────────────────────────────────────────────────
BG        = "#0a0a0a"
BG_PANEL  = "#111111"
BG_DANGER = "#0f0505"
BG_TABLE  = "#080808"
FG        = "#c8c8c8"
FG_DIM    = "#555"
CYAN      = "#00FFE5"
AMBER     = "#FFB800"
GREEN     = "#00FF41"
RED       = "#FF3030"
RED_DIM   = "#7a1a1a"
MAGENTA   = "#FF00A0"

_MONO = "Courier New" if sys.platform == "win32" else "DejaVu Sans Mono"
FONT_MONO   = (_MONO, 11)
FONT_HEAD   = (_MONO, 15, "bold")
FONT_BTN    = (_MONO, 11, "bold")
FONT_SMALL  = (_MONO, 9)
FONT_STATUS = (_MONO, 10)
FONT_TREE   = (_MONO, 10)
FONT_TABLE  = (_MONO, 9)
FONT_SPARK  = (_MONO, 12)

EXCLUDE_DIRS = {"node_modules", ".git", "backups", "__pycache__", ".claude"}
EXCLUDE_EXTS = {".pyc", ".pyo"}

# ── CONFIG ────────────────────────────────────────────────────────────────────

def _config_path():
    return get_project_dir() / CONFIG_FILE

def load_config():
    p = _config_path()
    if p.exists():
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
            cfg = dict(DEFAULT_CONFIG)
            if "port" in raw:
                cfg["port"] = max(1024, min(int(raw["port"]), 65535))
            if "poll_interval" in raw:
                cfg["poll_interval"] = max(500, min(int(raw["poll_interval"]), 10000))
            if "sparkline_width" in raw:
                cfg["sparkline_width"] = max(10, min(int(raw["sparkline_width"]), 200))
            if "auto_open_browser" in raw:
                cfg["auto_open_browser"] = bool(raw["auto_open_browser"])
            return cfg
        except Exception:
            pass
    return dict(DEFAULT_CONFIG)

def save_config(cfg):
    _config_path().write_text(json.dumps(cfg, indent=2), encoding="utf-8")

# ── PATH HELPERS ──────────────────────────────────────────────────────────────

def is_running_from_pyz():
    main = Path(sys.argv[0]).resolve()
    if main.suffix == '.pyz':
        return True
    try:
        return main.is_file() and zipfile.is_zipfile(str(main))
    except Exception:
        return False

def get_pyz_path():
    return Path(sys.argv[0]).resolve()

def get_project_dir():
    if is_running_from_pyz():
        return Path(sys.argv[0]).resolve().parent
    return Path(__file__).resolve().parent

def get_backups_dir():
    d = get_project_dir() / BACKUPS_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d

def get_username():
    return os.environ.get("USERNAME") or os.environ.get("USER") or "unknown"

def truncate_path(s, max_len=40):
    if len(s) <= max_len:
        return s
    keep_start = 14
    keep_end = max(max_len - keep_start - 3, 8)
    return s[:keep_start] + "..." + s[-keep_end:]

def find_installed():
    for p in [get_project_dir(), get_project_dir() / APP_NAME]:
        if (p / COMPOSE).exists():
            return p
    return None

# ── COMMAND RUNNER ────────────────────────────────────────────────────────────

def run_cmd(cmd, cwd=None, timeout=120):
    try:
        r = subprocess.run(cmd, cwd=str(cwd) if cwd else None,
                           capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="replace")
        return r.returncode, r.stdout + r.stderr
    except FileNotFoundError:
        return 1, f"Command not found: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return 1, f"Command timed out after {timeout}s"

def run_cmd_stream(cmd, cwd, log_fn, label="", timeout=300):
    """Run a command with live streaming output and elapsed-time heartbeat.
    Uses a thread+queue approach that works on both Windows and Linux
    (selectors.select does not support pipes on Windows)."""
    import time as _time
    import queue
    log_fn(f"{label} Running: {' '.join(cmd)}", FG_DIM)
    try:
        proc = subprocess.Popen(
            cmd, cwd=str(cwd) if cwd else None,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, encoding="utf-8", errors="replace",
        )
    except FileNotFoundError:
        log_fn(f"{label} Command not found: {cmd[0]}", RED)
        return 1, f"Command not found: {cmd[0]}"

    output_lines = []
    start = _time.time()
    last_tick = start
    line_q = queue.Queue()

    def elapsed():
        s = int(_time.time() - start)
        return f"{s // 60}m {s % 60}s" if s >= 60 else f"{s}s"

    # Reader thread — blocking readline works on all platforms
    def _reader():
        try:
            for line in iter(proc.stdout.readline, ''):
                line_q.put(line)
        except Exception:
            pass
        finally:
            line_q.put(None)  # sentinel

    t = threading.Thread(target=_reader, daemon=True)
    t.start()

    try:
        while True:
            try:
                line = line_q.get(timeout=3)
            except queue.Empty:
                now = _time.time()
                if now - start > timeout:
                    proc.kill()
                    proc.wait()
                    log_fn(f"{label} Timed out after {timeout}s", RED)
                    return 1, "\n".join(output_lines)
                if now - last_tick >= 3:
                    log_fn(f"{label} ... working ({elapsed()} elapsed)", FG_DIM)
                    last_tick = now
                continue
            if line is None:
                break  # EOF sentinel
            line = line.rstrip()
            if line:
                output_lines.append(line)
                log_fn(f"{label} {line}", FG_DIM)
                last_tick = _time.time()
            if _time.time() - start > timeout:
                proc.kill()
                proc.wait()
                log_fn(f"{label} Timed out after {timeout}s", RED)
                return 1, "\n".join(output_lines)
    except Exception:
        proc.kill()
        proc.wait()
        raise

    proc.wait()
    total = elapsed()
    log_fn(f"{label} Completed in {total} (exit {proc.returncode})", FG_DIM)
    return proc.returncode, "\n".join(output_lines)

# ── DOCKER HELPERS ────────────────────────────────────────────────────────────

def check_virtualization():
    """Check if WSL2 or Hyper-V is available (Windows only). Returns (ok, detail)."""
    if sys.platform != "win32":
        return True, "Linux — native containers"
    # Check WSL2 first (preferred Docker Desktop backend)
    code, out = run_cmd(["wsl", "--status"], timeout=10)
    wsl_ok = code == 0 and "not installed" not in out.lower()
    if wsl_ok:
        return True, "WSL2 available"
    # Check if WSL can be installed (feature exists but not enabled)
    code2, out2 = run_cmd(["wsl", "--list", "--quiet"], timeout=5)
    # Check Hyper-V via WMI (doesn't need elevation)
    code3, out3 = run_cmd([
        "powershell.exe", "-NoProfile", "-Command",
        "(Get-CimInstance Win32_ComputerSystem).HypervisorPresent"
    ], timeout=10)
    hypervisor = "true" in out3.lower() if code3 == 0 else False
    if hypervisor:
        return True, "Hypervisor present (Hyper-V/WSL2 capable)"
    # Check BIOS virtualization support
    code4, out4 = run_cmd([
        "powershell.exe", "-NoProfile", "-Command",
        "(Get-CimInstance Win32_Processor).VirtualizationFirmwareEnabled"
    ], timeout=10)
    virt_bios = "true" in out4.lower() if code4 == 0 else False
    if not virt_bios:
        return False, "Virtualization disabled in BIOS — enable VT-x/AMD-V in BIOS settings"
    # Virtualization is on in BIOS but WSL2/Hyper-V not installed
    return False, "WSL2 not installed — run 'wsl --install' in an admin terminal, then reboot"

def _find_docker_exe():
    """Find docker executable — checks PATH first, then common install locations."""
    # Try bare command first (works if PATH is set)
    code, _ = run_cmd(["docker", "--version"], timeout=5)
    if code == 0:
        return "docker"
    # Windows: check standard install paths
    if sys.platform == "win32":
        candidates = [
            os.path.expandvars(r"%ProgramFiles%\Docker\Docker\resources\bin\docker.exe"),
            os.path.expandvars(r"%LOCALAPPDATA%\Docker\resources\bin\docker.exe"),
            os.path.expandvars(r"%ProgramFiles%\Docker\Docker\Docker Desktop.exe"),
        ]
        for p in candidates:
            if os.path.isfile(p) and p.endswith("docker.exe"):
                # Add to PATH for this session so all subsequent calls work
                bin_dir = os.path.dirname(p)
                if bin_dir not in os.environ.get("PATH", ""):
                    os.environ["PATH"] = bin_dir + ";" + os.environ.get("PATH", "")
                code, _ = run_cmd([p, "--version"], timeout=5)
                if code == 0:
                    return p
    return None

_DOCKER_EXE = None
def docker_exe():
    """Return path to docker executable (cached)."""
    global _DOCKER_EXE
    if _DOCKER_EXE is None:
        _DOCKER_EXE = _find_docker_exe() or "docker"
    return _DOCKER_EXE

def check_docker_installed():
    return _find_docker_exe() is not None

def check_docker_running():
    """Check if Docker daemon is actually running (not just installed)."""
    exe = docker_exe()
    code, out = run_cmd([exe, "info"], timeout=8)
    return code == 0

def ensure_wsl(log_fn):
    """Install WSL2 if missing (Windows only). Returns True if WSL2 is available."""
    if sys.platform != "win32":
        return True
    code, out = run_cmd(["wsl", "--status"], timeout=10)
    if code == 0 and "not installed" not in out.lower():
        return True
    log_fn("[WSL] WSL2 not installed. Installing (requires admin)...", AMBER)
    # --no-prompt skips all EULA/confirmation prompts
    code, out = run_cmd_stream(
        ["wsl", "--install", "--no-prompt"],
        cwd=None, log_fn=log_fn, label="[WSL]", timeout=300,
    )
    if code == 0:
        log_fn("[WSL] Installed. A reboot may be required for WSL2 to fully activate.", GREEN)
        return True
    # Try the older syntax
    code2, out2 = run_cmd_stream(
        ["wsl", "--install", "-d", "Ubuntu", "--no-prompt"],
        cwd=None, log_fn=log_fn, label="[WSL]", timeout=300,
    )
    if code2 == 0:
        log_fn("[WSL] Installed. A reboot may be required.", GREEN)
        return True
    log_fn("[WSL] Auto-install failed. Run 'wsl --install' in an admin terminal, then reboot.", RED)
    return False

def start_docker_desktop(log_fn):
    """Attempt to launch Docker Desktop and wait for daemon to be ready."""
    # Pre-check: virtualization must be available or Docker Desktop will just hang
    virt_ok, virt_detail = check_virtualization()
    if not virt_ok:
        log_fn(f"[DOCKER] Cannot start — {virt_detail}", RED)
        return False
    if sys.platform != "win32":
        log_fn("[DOCKER] On Linux, start the daemon with: sudo systemctl start docker", AMBER)
        return False
    # Ensure WSL2 is present (Docker Desktop backend)
    ensure_wsl(log_fn)
    desktop_paths = [
        os.path.expandvars(r"%ProgramFiles%\Docker\Docker\Docker Desktop.exe"),
        os.path.expandvars(r"%LOCALAPPDATA%\Docker\Docker Desktop.exe"),
    ]
    launched = False
    for dp in desktop_paths:
        if os.path.isfile(dp):
            log_fn("[DOCKER] Launching Docker Desktop (auto-accepting license)...", AMBER)
            try:
                # --accept-license skips the EULA popup on first run
                subprocess.Popen(
                    [dp, "--accept-license"],
                    creationflags=0x00000008,  # DETACHED_PROCESS
                )
                launched = True
                break
            except Exception as e:
                log_fn(f"[DOCKER] Failed to launch: {e}", RED)
    if not launched:
        log_fn("[DOCKER] Could not find Docker Desktop. Start it manually.", AMBER)
        return False
    # Wait for daemon
    import time as _t
    log_fn("[DOCKER] Waiting for Docker daemon to start (usually 15-45s)...", AMBER)
    for i in range(60):
        _t.sleep(2)
        elapsed = (i + 1) * 2
        if check_docker_running():
            log_fn(f"[DOCKER] Daemon ready after {elapsed}s.", GREEN)
            return True
        if elapsed % 10 == 0:
            log_fn(f"[DOCKER] Still waiting... ({elapsed}s / 120s)", AMBER)
    log_fn("[DOCKER] Daemon did not start within 120s. Start Docker Desktop manually.", RED)
    return False

_COMPOSE_CMD = None
def compose_cmd():
    """Return compose command as list. Detects V2 plugin vs V1 standalone."""
    global _COMPOSE_CMD
    if _COMPOSE_CMD is not None:
        return list(_COMPOSE_CMD)
    exe = docker_exe()
    # Try V2 plugin first (docker compose)
    code, _ = run_cmd([exe, "compose", "version"], timeout=5)
    if code == 0:
        _COMPOSE_CMD = [exe, "compose"]
        return list(_COMPOSE_CMD)
    # Fall back to V1 standalone (docker-compose)
    code, _ = run_cmd(["docker-compose", "version"], timeout=5)
    if code == 0:
        _COMPOSE_CMD = ["docker-compose"]
        return list(_COMPOSE_CMD)
    _COMPOSE_CMD = [exe, "compose"]  # default
    return list(_COMPOSE_CMD)

def docker_up(cwd, log_fn, cfg=None):
    cfg = cfg or DEFAULT_CONFIG
    port = cfg.get("port", 3000)
    # Pre-check: is Docker daemon running?
    if not check_docker_running():
        log_fn("[DOCKER] Daemon not running — attempting to start Docker Desktop...", AMBER)
        if not start_docker_desktop(log_fn):
            log_fn("[DOCKER] Cannot proceed without Docker daemon. Start Docker Desktop and retry.", RED)
            return False
    log_fn("[DOCKER] Building & starting container ...", AMBER)
    code, out = run_cmd_stream(
        compose_cmd() + ["up", "-d", "--build"],
        cwd=cwd, log_fn=log_fn, label="[DOCKER]", timeout=600,
    )
    if code != 0:
        log_fn("[DOCKER] Build/start failed. Check the logs above for details.", RED)
        return False
    log_fn(f"[DOCKER] Live at http://localhost:{port}", GREEN)
    if cfg.get("auto_open_browser", True):
        webbrowser.open(f"http://localhost:{port}")
    return True

def docker_down(cwd, log_fn):
    log_fn("[DOCKER] Stopping container ...", AMBER)
    code, out = run_cmd_stream(
        compose_cmd() + ["down"],
        cwd=cwd, log_fn=log_fn, label="[DOCKER]", timeout=120,
    )
    if code != 0:
        log_fn("[DOCKER] Stop may have failed — check manually.", RED)
    return code == 0

def _parse_size(s):
    """Parse '45.2MiB' or '1.5kB' into float bytes."""
    m = re.match(r'([\d.]+)\s*(\w+)', s.strip())
    if not m:
        return 0.0
    val = float(m.group(1))
    unit = m.group(2).upper()
    mult = {"B": 1, "KB": 1024, "KIB": 1024, "MB": 1024**2, "MIB": 1024**2,
            "GB": 1024**3, "GIB": 1024**3}
    return val * mult.get(unit, 1)

def docker_stats():
    """Get container CPU/RAM/NET/PIDs. Returns dict or None."""
    code, out = run_cmd(
        [docker_exe(), "stats", CONTAINER_NAME, "--no-stream",
         "--format", "{{.CPUPerc}}|{{.MemUsage}}|{{.NetIO}}|{{.PIDs}}"],
        timeout=8,
    )
    if code != 0 or not out.strip():
        return None
    try:
        parts = out.strip().split('|')
        cpu_str = parts[0].strip()
        mem_str = parts[1].strip()
        net_str = parts[2].strip() if len(parts) > 2 else ""
        pids    = parts[3].strip() if len(parts) > 3 else "0"

        cpu_pct = float(cpu_str.replace('%', ''))
        mem_parts = mem_str.split('/')
        mem_used = mem_parts[0].strip()
        mem_total = mem_parts[1].strip() if len(mem_parts) > 1 else ""
        mem_val = _parse_size(mem_used)
        mem_total_val = _parse_size(mem_total) if mem_total else 512 * 1024**2
        mem_pct = (mem_val / mem_total_val) * 100 if mem_total_val > 0 else 0

        # NET I/O: "1.5kB / 2.3MB"
        net_parts = net_str.split('/')
        net_rx = _parse_size(net_parts[0]) if net_parts else 0
        net_tx = _parse_size(net_parts[1]) if len(net_parts) > 1 else 0

        return {
            "cpu": cpu_str, "cpu_pct": min(cpu_pct, 100),
            "mem": mem_used, "mem_pct": min(mem_pct, 100),
            "net_rx": net_rx, "net_tx": net_tx, "net_str": net_str,
            "pids": pids,
        }
    except Exception:
        return None

def _refresh_path_windows():
    """Refresh PATH from registry so newly-installed programs are found."""
    if sys.platform != "win32":
        return
    try:
        import winreg
        # System PATH
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment") as key:
            sys_path = winreg.QueryValueEx(key, "Path")[0]
        # User PATH
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment") as key:
            usr_path = winreg.QueryValueEx(key, "Path")[0]
        os.environ["PATH"] = sys_path + ";" + usr_path
    except Exception:
        pass

def _wait_for_docker(log_fn, timeout=120):
    """After install, wait for docker to become available. Returns True if found."""
    import time as _t
    log_fn(f"[DOCKER] Waiting for Docker to become available (up to {timeout}s)...", AMBER)
    start = _t.time()
    while _t.time() - start < timeout:
        elapsed = int(_t.time() - start)
        if sys.platform == "win32":
            _refresh_path_windows()
        if check_docker_installed():
            log_fn(f"[DOCKER] Docker found after {elapsed}s.", GREEN)
            # Now check if daemon is actually running
            if check_docker_running():
                log_fn("[DOCKER] Docker daemon is running.", GREEN)
                return True
            log_fn(f"[DOCKER] Docker installed but daemon not ready yet ({elapsed}s)...", AMBER)
        else:
            if elapsed % 10 == 0 and elapsed > 0:
                log_fn(f"[DOCKER] Still waiting... ({elapsed}s / {timeout}s)", AMBER)
        _t.sleep(3)
    log_fn(f"[DOCKER] Docker not available after {timeout}s.", RED)
    log_fn("[DOCKER] Start Docker Desktop manually, then press [S] to refresh.", AMBER)
    return False

def install_docker(log_fn):
    if sys.platform == "win32":
        # Check if already installed but just not in PATH or not started
        _refresh_path_windows()
        if check_docker_installed():
            log_fn("[DOCKER] Docker is installed but daemon may not be running.", AMBER)
            return start_docker_desktop(log_fn)

        # Step 1: Ensure WSL2 is available (Docker Desktop requires it)
        log_fn("[DOCKER] Checking WSL2 dependency...", AMBER)
        ensure_wsl(log_fn)

        # Step 2: Install Docker Desktop via winget (all prompts suppressed)
        log_fn("[DOCKER] Installing Docker Desktop via winget — this may take several minutes ...", AMBER)
        code, out = run_cmd_stream([
            "winget", "install", "Docker.DockerDesktop",
            "--silent",
            "--accept-package-agreements",
            "--accept-source-agreements",
            "--disable-interactivity",
        ], cwd=None, log_fn=log_fn, label="[INSTALL]", timeout=600)
        if code == 0:
            log_fn("[DOCKER] Installation complete.", GREEN)
            _refresh_path_windows()
            return start_docker_desktop(log_fn)
        log_fn("[DOCKER] winget failed. Opening download page ...", AMBER)
        webbrowser.open("https://docs.docker.com/desktop/install/windows-install/")
        log_fn("[DOCKER] Install Docker Desktop, start it, then press [S] to refresh.", AMBER)
        return False
    else:
        log_fn("[DOCKER] Installing Docker via get.docker.com — this may take several minutes ...", AMBER)
        code, out = run_cmd_stream(
            ["bash", "-c", "curl -fsSL https://get.docker.com | sh"],
            cwd=None, log_fn=log_fn, label="[INSTALL]", timeout=600,
        )
        if code == 0:
            user = get_username()
            run_cmd(["sudo", "usermod", "-aG", "docker", user], timeout=10)
            log_fn(f"[DOCKER] Installed! Added {user} to docker group.", GREEN)
            log_fn("[DOCKER] Log out & back in, then retry.", AMBER)
            return True
        log_fn("[DOCKER] Auto-install failed.", RED)
        log_fn("[DOCKER] Try: curl -fsSL https://get.docker.com | sh", AMBER)
        return False

def check_app_online(port=3000):
    try:
        resp = urllib.request.urlopen(f"http://localhost:{port}/api/tasks", timeout=2)
        resp.close()
        return True
    except Exception:
        return False

def fetch_tasks(port=3000):
    """Fetch full task list from API. Returns list or None."""
    try:
        with urllib.request.urlopen(f"http://localhost:{port}/api/tasks", timeout=2) as resp:
            return json.loads(resp.read())
    except Exception:
        return None

# ── SPARKLINE HELPERS ─────────────────────────────────────────────────────────

def sparkline_str(history, width=50):
    """Convert deque of 0-100 values to block-char sparkline string."""
    if not history:
        return SPARK_BLOCKS[0] * width
    data = list(history)
    # Pad left if shorter than width
    if len(data) < width:
        data = [0.0] * (width - len(data)) + data
    else:
        data = data[-width:]
    out = []
    for v in data:
        idx = int(max(0, min(v, 100)) / 100 * (len(SPARK_BLOCKS) - 1))
        out.append(SPARK_BLOCKS[idx])
    return "".join(out)

def format_rate(bytes_per_sec):
    """Format bytes/sec into human readable string."""
    if bytes_per_sec < 1024:
        return f"{bytes_per_sec:.0f}B/s"
    elif bytes_per_sec < 1024**2:
        return f"{bytes_per_sec/1024:.1f}K/s"
    else:
        return f"{bytes_per_sec/1024**2:.1f}M/s"

# ── BACKUP / PACKAGING ────────────────────────────────────────────────────────

def get_launcher_source():
    if is_running_from_pyz():
        with zipfile.ZipFile(str(get_pyz_path()), 'r') as z:
            return z.read('__main__.py')
    return Path(__file__).read_bytes()

def list_backups():
    backups = []
    bdir = get_backups_dir()
    for f in bdir.glob("*.pyz"):
        try:
            st = f.stat()
            backups.append({"path": f, "mtime": datetime.fromtimestamp(st.st_mtime),
                            "size": st.st_size})
        except Exception:
            pass
    if is_running_from_pyz():
        pyz = get_pyz_path()
        if not any(b["path"].resolve() == pyz for b in backups):
            try:
                st = pyz.stat()
                backups.append({"path": pyz, "mtime": datetime.fromtimestamp(st.st_mtime),
                                "size": st.st_size, "embedded": True})
            except Exception:
                pass
    backups.sort(key=lambda b: b["mtime"], reverse=True)
    return backups

def create_pyz(src_dir, log_fn):
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = get_backups_dir() / f"kappa-roadmap-{ts}.pyz"
    log_fn(f"[PACK] Creating {dest.name} ...", AMBER)
    launcher_src = get_launcher_source()
    # Count files first for progress
    files = []
    for f in src_dir.rglob("*"):
        if not f.is_file():
            continue
        rel = f.relative_to(src_dir)
        if any(part in EXCLUDE_DIRS for part in rel.parts):
            continue
        if rel.suffix in EXCLUDE_EXTS or rel.name == "launcher.py":
            continue
        files.append((f, rel))
    total = len(files)
    log_fn(f"[PACK] Packing {total} files ...", FG_DIM)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr("__main__.py", launcher_src)
        for i, (f, rel) in enumerate(files, 1):
            z.writestr(f"app/{rel.as_posix()}", f.read_bytes())
            if i % 20 == 0 or i == total:
                pct = int(i / total * 100)
                log_fn(f"[PACK] {i}/{total} files ({pct}%)", FG_DIM)
    with open(dest, "wb") as fh:
        fh.write(b"#!/usr/bin/env python3\n")
        fh.write(buf.getvalue())
    size_mb = dest.stat().st_size / (1024 * 1024)
    log_fn(f"[PACK] Done -> {dest.name} ({size_mb:.1f} MB)", GREEN)
    write_bootstrap_scripts(dest.parent, log_fn)
    return dest

def write_bootstrap_scripts(target_dir, log_fn):
    (target_dir / "launch.bat").write_text(
        '@echo off\r\n'
        'where python >nul 2>&1 && goto :run\r\n'
        'echo [*] Python not found. Installing via winget...\r\n'
        'winget install Python.Python.3.12 --silent --accept-package-agreements '
        '--accept-source-agreements >nul 2>&1\r\n'
        'if errorlevel 1 (echo [!] Auto-install failed. Get Python from https://python.org & pause & exit /b 1)\r\n'
        'set "PATH=%LOCALAPPDATA%\\Programs\\Python\\Python312;'
        '%LOCALAPPDATA%\\Programs\\Python\\Python312\\Scripts;%PATH%"\r\n'
        ':run\r\n'
        'for %%f in ("%~dp0kappa-roadmap-*.pyz") do set "PYZ=%%f"\r\n'
        'if not defined PYZ (echo [!] No .pyz found. & pause & exit /b 1)\r\n'
        'python "%PYZ%"\r\n', encoding="utf-8")
    (target_dir / "launch.sh").write_text(
        '#!/usr/bin/env bash\n'
        'set -e\n'
        'if ! command -v python3 &>/dev/null; then\n'
        '  echo "[*] Python 3 not found. Installing..."\n'
        '  if command -v apt-get &>/dev/null; then sudo apt-get update -qq && sudo apt-get install -y python3 python3-tk\n'
        '  elif command -v dnf &>/dev/null; then sudo dnf install -y python3 python3-tkinter\n'
        '  elif command -v pacman &>/dev/null; then sudo pacman -Sy --noconfirm python tk\n'
        '  else echo "[!] Install Python 3 manually."; exit 1; fi\n'
        'fi\n'
        'DIR="$(cd "$(dirname "$0")" && pwd)"\n'
        'PYZ=$(ls -1t "$DIR"/kappa-roadmap-*.pyz 2>/dev/null | head -1)\n'
        '[ -z "$PYZ" ] && { echo "[!] No .pyz found."; exit 1; }\n'
        'python3 "$PYZ"\n', encoding="utf-8")
    log_fn("[PACK] Bootstrap: launch.bat + launch.sh", GREEN)

def extract_from_pyz(pyz_path, target, log_fn):
    log_fn(f"[UNPACK] Extracting to {target} ...", CYAN)
    target.mkdir(parents=True, exist_ok=True)
    resolved_target = target.resolve()
    with zipfile.ZipFile(str(pyz_path), 'r') as z:
        app_entries = [i for i in z.infolist()
                       if i.filename.startswith("app/") and len(i.filename) > 4]
        total = len(app_entries)
        if total == 0:
            log_fn("[UNPACK] WARNING: Archive contains no app files.", RED)
            return False
        log_fn(f"[UNPACK] {total} files to extract ...", FG_DIM)
        for idx, info in enumerate(app_entries, 1):
            rel = info.filename[4:]
            dest_path = (target / rel).resolve()
            # Zip Slip protection — block path traversal
            if not str(dest_path).startswith(str(resolved_target)):
                log_fn(f"[UNPACK] BLOCKED unsafe path: {info.filename}", RED)
                continue
            if info.filename.endswith('/'):
                dest_path.mkdir(parents=True, exist_ok=True)
            else:
                dest_path.parent.mkdir(parents=True, exist_ok=True)
                with z.open(info) as sf, open(dest_path, 'wb') as df:
                    df.write(sf.read())
            if idx % 20 == 0 or idx == total:
                pct = int(idx / total * 100)
                log_fn(f"[UNPACK] {idx}/{total} files ({pct}%)", FG_DIM)
    log_fn("[UNPACK] Done.", GREEN)
    return True

def export_snapshot(cwd, log_fn):
    src = cwd / DB_FILE
    if not src.exists():
        log_fn("[DB] No database found.", FG_DIM)
        return None
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = get_backups_dir() / f"snapshot-{ts}.json"
    shutil.copy2(src, dest)
    log_fn(f"[DB] Snapshot -> {dest.name}", GREEN)
    return dest

# ── DESTRUCTION LOG ───────────────────────────────────────────────────────────

def log_destruction(username, timestamp):
    with open(get_backups_dir() / DESTRUCTION_LOG, 'a', encoding="utf-8") as f:
        f.write(f"{username} // {timestamp}\n")

def read_last_destruction():
    p = get_backups_dir() / DESTRUCTION_LOG
    if not p.exists():
        return None
    try:
        lines = [l.strip() for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
        return lines[-1] if lines else None
    except Exception:
        return None

# ══════════════════════════════════════════════════════════════════════════════
#  TERMINAL MODE (--terminal) — no Tkinter, no display server required
# ══════════════════════════════════════════════════════════════════════════════

class TerminalUI:
    """btop++-inspired terminal dashboard for Kappa Roadmap deployment."""

    import time as _time_mod  # class-level import avoids __import__ hack

    # ── ANSI ──────────────────────────────────────────────────────────────
    RST   = "\033[0m"
    BOLD  = "\033[1m"
    DIM   = "\033[2m"
    ITAL  = "\033[3m"
    ULINE = "\033[4m"

    # 256-color shortcuts  \033[38;5;Nm
    @staticmethod
    def _fg(n): return f"\033[38;5;{n}m"

    # Named palette (btop-inspired)
    C_CYAN    = "\033[38;5;81m"    # bright teal
    C_GREEN   = "\033[38;5;77m"    # soft green
    C_AMBER   = "\033[38;5;214m"   # warm amber
    C_RED     = "\033[38;5;196m"   # bright red
    C_MAG     = "\033[38;5;170m"   # magenta/pink
    C_BLUE    = "\033[38;5;69m"    # steel blue
    C_WHITE   = "\033[38;5;255m"   # bright white
    C_GRAY    = "\033[38;5;242m"   # mid gray
    C_DGRAY   = "\033[38;5;236m"   # dark gray (borders)
    C_LGRAY   = "\033[38;5;249m"   # light gray (text)
    C_ORANGE  = "\033[38;5;208m"   # orange accent
    C_PURPLE  = "\033[38;5;141m"   # lavender

    # Box-drawing (rounded corners like btop)
    TL = "\u256d"; TR = "\u256e"; BL = "\u2570"; BR = "\u256f"
    H  = "\u2500"; V  = "\u2502"
    TJ = "\u252c"; BJ = "\u2534"; LJ = "\u251c"; RJ = "\u2524"; CJ = "\u253c"

    # Graph blocks (high-res: 8 levels)
    BLOCKS = " \u2581\u2582\u2583\u2584\u2585\u2586\u2587\u2588"

    # Braille dots for ultra-fine sparklines (2-dot vertical resolution)
    # Braille: lower dot = +0x40, upper dot = +0x01 (per column)
    # We use a simplified 4-level encoding per column
    BRAILLE_BASE = 0x2800

    # Status icons
    ICON_ON  = "\u25cf"   # ●
    ICON_OFF = "\u25cb"   # ○
    ICON_ARR = "\u25b8"   # ▸
    ICON_DOT = "\u2022"   # •

    # Gradient colors for sparklines (green → yellow → red)
    SPARK_GRAD = [77, 77, 78, 114, 150, 186, 222, 214, 208, 196]

    STATUS_COLORS = {
        "done": "\033[38;5;77m", "completed": "\033[38;5;77m",
        "in-progress": "\033[38;5;81m", "active": "\033[38;5;81m",
        "planned": "\033[38;5;214m", "pending": "\033[38;5;214m",
        "blocked": "\033[38;5;196m", "cancelled": "\033[38;5;196m",
    }
    STATUS_ICONS = {
        "done": "\u2713", "completed": "\u2713",
        "in-progress": "\u25b8", "active": "\u25b8",
        "planned": "\u25cb", "pending": "\u25cb",
        "blocked": "\u2718", "cancelled": "\u2718",
    }

    # System states — tells the user exactly where things stand
    STATE_NO_VIRT       = "no_virt"
    STATE_NO_DOCKER     = "no_docker"
    STATE_DOCKER_OFF    = "docker_off"
    STATE_NO_PROJECT    = "no_project"
    STATE_NO_CONTAINER  = "no_container"
    STATE_STARTING      = "starting"
    STATE_ONLINE        = "online"

    STATE_LABELS = {
        "no_virt":      ("Virtualization not available",  ""),
        "no_docker":    ("Docker not installed",         "Press [D] to deploy — installer will guide you"),
        "docker_off":   ("Docker daemon not running",    "Start Docker Desktop, then press [S] to refresh"),
        "no_project":   ("No project files found",       "Press [D] to deploy from a backup"),
        "no_container":  ("Container not running",        "Press [D] to deploy, or docker compose up manually"),
        "starting":     ("Container starting up...",     "Waiting for app to respond — this takes 10-30s"),
        "online":       ("",                              ""),
    }

    def __init__(self):
        self.cfg = load_config()
        self.running = True
        self.poll_interval = self.cfg.get("poll_interval", 1000) / 1000.0
        self.port = self.cfg.get("port", 3000)
        self.spark_width = 40
        self.cpu_hist = deque(maxlen=self.spark_width)
        self.ram_hist = deque(maxlen=self.spark_width)
        self.net_hist = deque(maxlen=self.spark_width)
        self.prev_net = None
        self.prev_net_time = None
        self.last_stats = None
        self.last_tasks = None
        self.last_online = False
        self.log_lines = []
        self._start_time = self._time_mod.time()
        self._system_state = self.STATE_STARTING
        self._state_since = self._time_mod.time()
        self._enable_ansi()

    def _enable_ansi(self):
        if sys.platform == "win32":
            try:
                import ctypes
                k32 = ctypes.windll.kernel32
                h = k32.GetStdHandle(-11)
                mode = ctypes.c_ulong()
                k32.GetConsoleMode(h, ctypes.byref(mode))
                k32.SetConsoleMode(h, mode.value | 0x0004)
            except Exception:
                pass
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
        # Hide cursor
        print("\033[?25l", end="", flush=True)

    def _show_cursor(self):
        print("\033[?25h", end="", flush=True)

    def _clear(self):
        print("\033[2J\033[H", end="")

    def _term_size(self):
        try:
            c, r = os.get_terminal_size()
            return max(c, 60), max(r, 20)
        except Exception:
            return 80, 24

    def _log(self, msg, color=""):
        c = color or self.C_GRAY
        try:
            print(f"  {c}{msg}{self.RST}", flush=True)
        except Exception:
            pass
        self.log_lines.append((msg, color))
        if len(self.log_lines) > 12:
            self.log_lines = self.log_lines[-12:]

    # ── BOX DRAWING HELPERS ───────────────────────────────────────────────

    def _box_top(self, w, title="", color=""):
        c = color or self.C_DGRAY
        tc = self.C_CYAN if title else ""
        inner = w - 2
        if title:
            t = f" {tc}{self.BOLD}{title}{self.RST}{c} "
            pad = inner - len(title) - 2
            return f"{c}{self.TL}{self.H}{t}{self.H * max(pad, 0)}{self.TR}{self.RST}"
        return f"{c}{self.TL}{self.H * inner}{self.TR}{self.RST}"

    def _box_mid(self, w, title="", color=""):
        c = color or self.C_DGRAY
        tc = self.C_GRAY
        inner = w - 2
        if title:
            t = f" {tc}{title}{self.RST}{c} "
            pad = inner - len(title) - 2
            return f"{c}{self.LJ}{self.H}{t}{self.H * max(pad, 0)}{self.RJ}{self.RST}"
        return f"{c}{self.LJ}{self.H * inner}{self.RJ}{self.RST}"

    def _box_bot(self, w, color=""):
        c = color or self.C_DGRAY
        return f"{c}{self.BL}{self.H * (w - 2)}{self.BR}{self.RST}"

    def _box_row(self, content, w, color=""):
        c = color or self.C_DGRAY
        # Strip ANSI to compute visible length
        visible = re.sub(r'\033\[[0-9;]*m', '', content)
        pad = max(w - 2 - len(visible), 0)
        return f"{c}{self.V}{self.RST}{content}{' ' * pad}{c}{self.V}{self.RST}"

    # ── SPARKLINE WITH GRADIENT COLOR ─────────────────────────────────────

    def _gradient_spark(self, history, width, grad=None):
        """Render a sparkline with per-bar gradient colors."""
        grad = grad or self.SPARK_GRAD
        data = list(history)
        if len(data) < width:
            data = [0.0] * (width - len(data)) + data
        else:
            data = data[-width:]
        out = []
        for v in data:
            v = max(0, min(v, 100))
            bidx = int(v / 100 * (len(self.BLOCKS) - 1))
            cidx = int(v / 100 * (len(grad) - 1))
            out.append(f"\033[38;5;{grad[cidx]}m{self.BLOCKS[bidx]}")
        return "".join(out) + self.RST

    # ── PROGRESS BAR ──────────────────────────────────────────────────────

    def _bar(self, pct, width, filled_color, empty_color=""):
        ec = empty_color or self.C_DGRAY
        filled = int(pct / 100 * width)
        empty = width - filled
        return f"{filled_color}{'\u2588' * filled}{ec}{'\u2500' * empty}{self.RST}"

    # ── UPTIME STRING ─────────────────────────────────────────────────────

    def _uptime(self):
        s = int(self._time_mod.time() - self._start_time)
        if s < 60:
            return f"{s}s"
        if s < 3600:
            return f"{s // 60}m{s % 60:02d}s"
        return f"{s // 3600}h{(s % 3600) // 60:02d}m"

    # ══════════════════════════════════════════════════════════════════════
    #  MAIN RENDER
    # ══════════════════════════════════════════════════════════════════════

    def _render(self):
        self._clear()
        W, H = self._term_size()
        w = min(W, 100)
        s = self.last_stats
        online = self.last_online
        tasks = self.last_tasks or []
        now_str = datetime.now().strftime("%H:%M:%S")

        o = []  # output buffer — build then print once (less flicker)

        # ── HEADER BAR ────────────────────────────────────────────────────
        title = "KAPPA COMPUTER SYSTEMS"
        sub = "DEPLOYMENT MANAGER"
        ver = "v2.1"
        right = f"{self.C_GRAY}{now_str}  {self.C_DGRAY}\u2502  {self.C_GRAY}up {self._uptime()}"
        # Title line
        o.append(f" {self.C_CYAN}{self.BOLD}{title}{self.RST}  "
                 f"{self.C_DGRAY}{sub} {self.C_GRAY}{ver}  "
                 f"{' ' * max(w - len(title) - len(sub) - len(ver) - len(now_str) - 22, 0)}"
                 f"{right}{self.RST}")
        o.append(f" {self.C_CYAN}{self.H * (w - 2)}{self.RST}")

        # ── STATUS PANEL ─────────────────────────────────────────────────
        o.append(self._box_top(w, "status"))
        state = self._system_state
        elapsed = self._state_elapsed()

        if online:
            app_s = f" {self.C_GREEN}{self.BOLD}{self.ICON_ON} ONLINE{self.RST}"
        elif state == self.STATE_STARTING:
            # Animated spinner for "starting" state
            spin = ["\u280b", "\u2819", "\u2838", "\u2834", "\u2826", "\u2807"][elapsed % 6]
            app_s = f" {self.C_AMBER}{spin} STARTING{self.RST} {self._fg(242)}({elapsed}s){self.RST}"
        elif state == self.STATE_NO_DOCKER:
            app_s = f" {self.C_RED}{self.ICON_OFF} NO DOCKER{self.RST}"
        elif state == self.STATE_DOCKER_OFF:
            app_s = f" {self.C_RED}{self.ICON_OFF} DOCKER STOPPED{self.RST}"
        elif state == self.STATE_NO_PROJECT:
            app_s = f" {self.C_AMBER}{self.ICON_OFF} NO PROJECT{self.RST}"
        elif state == self.STATE_NO_CONTAINER:
            app_s = f" {self.C_RED}{self.ICON_OFF} NOT DEPLOYED{self.RST}"
        elif state == self.STATE_NO_VIRT:
            app_s = f" {self.C_RED}{self.BOLD}\u2718 NO VIRTUALIZATION{self.RST}"
        else:
            app_s = f" {self.C_RED}{self.ICON_OFF} OFFLINE{self.RST}"

        cpu_s = f"{self.C_AMBER}{s['cpu']}{self.RST}" if s else f"{self.C_DGRAY}--{self.RST}"
        mem_s = f"{self.C_CYAN}{s['mem']}{self.RST}" if s else f"{self.C_DGRAY}--{self.RST}"
        net_s = f"{self.C_MAG}{s['net_str']}{self.RST}" if s else f"{self.C_DGRAY}--{self.RST}"
        pid_s = f"{self.C_LGRAY}{s['pids']}{self.RST}" if s else f"{self.C_DGRAY}-{self.RST}"
        task_n = f"{self.C_GREEN}{len(tasks)}{self.RST}" if tasks else f"{self.C_DGRAY}0{self.RST}"

        stat_line = (f"{app_s}  {self.C_DGRAY}\u2502{self.RST}  "
                     f"{self.C_GRAY}cpu {cpu_s}  {self.C_DGRAY}\u2502{self.RST}  "
                     f"{self.C_GRAY}mem {mem_s}  {self.C_DGRAY}\u2502{self.RST}  "
                     f"{self.C_GRAY}net {net_s}  {self.C_DGRAY}\u2502{self.RST}  "
                     f"{self.C_GRAY}pid {pid_s}  {self.C_DGRAY}\u2502{self.RST}  "
                     f"{self.C_GRAY}tasks {task_n}")
        o.append(self._box_row(f" {stat_line}", w))

        # Contextual guidance line — tells the user what's happening and what to do
        if state != self.STATE_ONLINE:
            label, hint = self.STATE_LABELS.get(state, ("Unknown state", ""))
            color = self.C_AMBER
            if state == self.STATE_STARTING:
                label = f"Container starting up... ({elapsed}s)"
                if elapsed > 30:
                    hint = "Taking longer than usual — check [D]ocker logs"
            elif state == self.STATE_NO_VIRT:
                color = self.C_RED
                virt_detail = getattr(self, '_virt_detail', 'Unknown')
                hint = virt_detail
            o.append(self._box_row(
                f"  {color}{self.ICON_ARR} {label}{self.RST}  "
                f"{self._fg(242)}{hint}{self.RST}", w))

        # Mini progress bars for CPU and RAM
        if s:
            cpu_pct = s['cpu_pct']
            mem_pct = s['mem_pct']
            bar_w = max(w - 30, 20)
            cpu_bar = self._bar(cpu_pct, bar_w,
                                self.C_GREEN if cpu_pct < 50 else self.C_AMBER if cpu_pct < 80 else self.C_RED)
            mem_bar = self._bar(mem_pct, bar_w,
                                self.C_CYAN if mem_pct < 60 else self.C_AMBER if mem_pct < 85 else self.C_RED)
            o.append(self._box_row(f"  {self.C_GRAY}cpu {self._fg(242)}{cpu_pct:5.1f}%{self.RST}  {cpu_bar}", w))
            o.append(self._box_row(f"  {self.C_GRAY}mem {self._fg(242)}{mem_pct:5.1f}%{self.RST}  {mem_bar}", w))
        o.append(self._box_bot(w))

        # ── GRAPHS PANEL ─────────────────────────────────────────────────
        gw = min(self.spark_width, w - 14)
        o.append(self._box_top(w, "graphs"))
        # CPU sparkline
        cpu_val = f"{s['cpu_pct']:5.1f}%" if s else "   -- "
        cpu_spark = self._gradient_spark(self.cpu_hist, gw,
                                         [77, 77, 114, 150, 186, 222, 214, 208, 202, 196])
        o.append(self._box_row(f"  {self.C_GREEN}cpu{self.RST} {self._fg(242)}{cpu_val}{self.RST} {cpu_spark}", w))
        # RAM sparkline
        ram_val = f"{s['mem_pct']:5.1f}%" if s else "   -- "
        ram_spark = self._gradient_spark(self.ram_hist, gw,
                                         [81, 81, 75, 111, 147, 183, 219, 213, 207, 201])
        o.append(self._box_row(f"  {self.C_CYAN}mem{self.RST} {self._fg(242)}{ram_val}{self.RST} {ram_spark}", w))
        # NET sparkline
        if self.net_hist:
            nv = format_rate(self.net_hist[-1] / 100 * 1024**2)
        else:
            nv = "   -- "
        net_spark = self._gradient_spark(self.net_hist, gw,
                                         [170, 170, 171, 177, 183, 189, 225, 219, 213, 207])
        o.append(self._box_row(f"  {self.C_MAG}net{self.RST} {self._fg(242)}{nv:>7}{self.RST} {net_spark}", w))
        o.append(self._box_bot(w))

        # ── TASKS PANEL ──────────────────────────────────────────────────
        max_tasks = max(H - 22, 4)  # adaptive to terminal height
        o.append(self._box_top(w, f"tasks ({len(tasks)})"))
        if tasks:
            # Header
            hdr = (f"  {self._fg(242)}{'#':<4} "
                   f"{'STATUS':<12} "
                   f"{'TITLE':<{w - 24}}")
            o.append(self._box_row(hdr, w))
            o.append(self._box_mid(w))
            for t in tasks[:max_tasks]:
                tid = str(t.get("id", ""))[:3]
                status = t.get("status", "unknown")
                title = t.get("title", "")[:w - 26]
                sc = self.STATUS_COLORS.get(status, self.C_GRAY)
                icon = self.STATUS_ICONS.get(status, self.ICON_DOT)
                row = (f"  {self._fg(242)}{tid:<4}{self.RST} "
                       f"{sc}{icon} {status:<10}{self.RST} "
                       f"{self.C_LGRAY}{title}{self.RST}")
                o.append(self._box_row(row, w))
            if len(tasks) > max_tasks:
                more = f"  {self._fg(242)}{self.ITAL}... {len(tasks) - max_tasks} more{self.RST}"
                o.append(self._box_row(more, w))
        else:
            # Context-aware empty state
            if state == self.STATE_ONLINE:
                empty_msg = "No tasks yet — add some in the admin panel"
            elif state == self.STATE_STARTING:
                empty_msg = f"App starting... tasks will appear shortly ({elapsed}s)"
            elif state == self.STATE_NO_CONTAINER:
                empty_msg = "No deployment — press [D] to deploy"
            elif state == self.STATE_NO_VIRT:
                vd = getattr(self, '_virt_detail', '')
                if "WSL2" in vd:
                    empty_msg = "Run 'wsl --install' in admin terminal, then reboot"
                elif "BIOS" in vd:
                    empty_msg = "Enable VT-x/AMD-V in BIOS, then reboot"
                else:
                    empty_msg = "Virtualization required for Docker"
            elif state == self.STATE_NO_DOCKER:
                empty_msg = "Docker required — press [D] for install guide"
            elif state == self.STATE_DOCKER_OFF:
                empty_msg = "Start Docker Desktop, then press [S] to refresh"
            elif state == self.STATE_NO_PROJECT:
                empty_msg = "No project files — press [D] to deploy from backup"
            else:
                empty_msg = "Connecting..."
            o.append(self._box_row(f"  {self._fg(242)}{empty_msg}{self.RST}", w))
        o.append(self._box_bot(w))

        # ── LOG PANEL ────────────────────────────────────────────────────
        if self.log_lines:
            o.append(self._box_top(w, "log"))
            for msg, color in self.log_lines[-5:]:
                c = color or self.C_GRAY
                # Truncate to fit box
                vis = msg[:w - 6]
                o.append(self._box_row(f"  {c}{vis}{self.RST}", w))
            o.append(self._box_bot(w))

        # ── DESTRUCTION LINE ─────────────────────────────────────────────
        last_d = read_last_destruction()
        if last_d:
            o.append(f" {self._fg(52)}\u2718 LAST DESTRUCTION: {last_d}{self.RST}")

        # ── COMMAND BAR ──────────────────────────────────────────────────
        o.append("")
        o.append(
            f" {self.C_DGRAY}{self.V}{self.RST} "
            f"{self.C_GREEN}{self.BOLD}D{self.RST}{self._fg(242)}eploy "
            f"{self.C_GREEN}{self.BOLD}E{self.RST}{self._fg(242)}xport "
            f"{self.C_AMBER}{self.BOLD}C{self.RST}{self._fg(242)}lose "
            f"{self.C_RED}{self.BOLD}X{self.RST}{self._fg(242)} Destroy "
            f"{self.C_CYAN}{self.BOLD}S{self.RST}{self._fg(242)}ync "
            f"{self.C_GRAY}{self.BOLD}H{self.RST}{self._fg(242)}elp "
            f"{self.C_DGRAY}{self.BOLD}Q{self.RST}{self._fg(242)}uit"
            f"{self.RST}"
        )

        # Print everything at once
        print("\n".join(o), flush=True)

    # ── SYSTEM DIAGNOSTICS ──────────────────────────────────────────────

    def _diagnose(self):
        """Determine current system state — called every poll cycle."""
        old_state = self._system_state
        # Cache virtualization check (expensive, won't change at runtime)
        if not hasattr(self, '_virt_ok'):
            self._virt_ok, self._virt_detail = check_virtualization()
        if not self._virt_ok:
            self._system_state = self.STATE_NO_VIRT
        elif not check_docker_installed():
            self._system_state = self.STATE_NO_DOCKER
        elif self.last_online:
            self._system_state = self.STATE_ONLINE
        elif self.last_stats:
            # Container running but app not responding yet
            self._system_state = self.STATE_STARTING
        else:
            # No stats — is docker daemon running?
            code, out = run_cmd([docker_exe(), "info"], timeout=5)
            if code != 0:
                self._system_state = self.STATE_DOCKER_OFF
            elif find_installed() is None:
                self._system_state = self.STATE_NO_PROJECT
            else:
                # Project exists, docker running, but no container
                self._system_state = self.STATE_NO_CONTAINER
        if self._system_state != old_state:
            self._state_since = self._time_mod.time()

    def _state_elapsed(self):
        """Seconds since entering current state."""
        return int(self._time_mod.time() - self._state_since)

    # ── POLLING ───────────────────────────────────────────────────────────

    def _poll(self):
        stats = docker_stats()
        self.last_stats = stats
        self.last_online = check_app_online(self.port)
        if stats:
            self.cpu_hist.append(stats["cpu_pct"])
            self.ram_hist.append(stats["mem_pct"])
            now = self._time_mod.time()
            net_total = stats["net_rx"] + stats["net_tx"]
            if self.prev_net is not None and self.prev_net_time is not None:
                dt = now - self.prev_net_time
                if dt > 0:
                    rate = (net_total - self.prev_net) / dt
                    rate_pct = min(rate / (1024**2) * 100, 100)
                    self.net_hist.append(max(rate_pct, 0))
            self.prev_net = net_total
            self.prev_net_time = now
        self._diagnose()

    def _poll_tasks(self):
        tasks = fetch_tasks(self.port)
        if tasks is not None:
            self.last_tasks = tasks

    # ── INPUT ─────────────────────────────────────────────────────────────

    def _get_key(self, timeout=None):
        if sys.platform == "win32":
            import msvcrt
            deadline = self._time_mod.time() + (timeout or self.poll_interval)
            while self._time_mod.time() < deadline:
                if msvcrt.kbhit():
                    return msvcrt.getwch()
                self._time_mod.sleep(0.05)
            return None
        else:
            import select as _select, termios, tty
            fd = sys.stdin.fileno()
            old = termios.tcgetattr(fd)
            try:
                tty.setcbreak(fd)
                rlist, _, _ = _select.select([sys.stdin], [], [], timeout or self.poll_interval)
                if rlist:
                    return sys.stdin.read(1)
                return None
            finally:
                termios.tcsetattr(fd, termios.TCSADRAIN, old)

    def _input(self, prompt):
        self._show_cursor()
        print(self.RST, end="", flush=True)
        try:
            return input(prompt)
        finally:
            print("\033[?25l", end="", flush=True)  # hide cursor again

    def _confirm(self, msg):
        resp = self._input(f"\n {self.C_AMBER}{msg} [y/N]: {self.RST}")
        return resp.strip().lower() in ("y", "yes", "yeah son")

    # ── ACTIONS ───────────────────────────────────────────────────────────

    def _do_deploy(self):
        cwd = find_installed() or get_project_dir()
        backups = list_backups()
        if not backups:
            self._log("No backups found. Use [C]lose Shop to create one, or --pack.", self.C_RED)
            return
        print(f"\n {self.C_CYAN}{self.BOLD}Available backups:{self.RST}")
        for i, b in enumerate(backups[:5]):
            dt = b["mtime"].strftime("%Y-%m-%d %I:%M %p")
            sz = f"{b['size']/1024:.0f}KB"
            tag = f" {self._fg(242)}(embedded)" if b.get("embedded") else ""
            marker = f"{self.C_GREEN}{self.ICON_ARR}" if i == 0 else f"{self.C_DGRAY} "
            print(f"  {marker} {self.C_WHITE}[{i+1}]{self.RST} {self._fg(242)}{dt}  {sz}{tag}{self.RST}")
        choice = self._input(f"\n {self._fg(242)}Select [1-{min(len(backups),5)}] or Enter to cancel: {self.RST}")
        try:
            idx = int(choice.strip()) - 1
            if idx < 0 or idx >= min(len(backups), 5):
                return
        except (ValueError, IndexError):
            return
        dest_choice = self._input(
            f" Deploy to [{self.C_GREEN}H{self.RST}]ere ({cwd.name}) or enter a path: ")
        dest = cwd if not dest_choice.strip() or dest_choice.strip().lower() == "h" else Path(dest_choice.strip())
        self._log(f"Extracting to {dest} ...", self.C_AMBER)
        extract_from_pyz(backups[idx]["path"], dest, lambda m, c=None: self._log(m, c or self.C_GREEN))
        if not check_docker_installed():
            self._log("Docker not installed.", self.C_RED)
            if self._confirm("Attempt to install Docker?"):
                ok = install_docker(lambda m, c=None: self._log(m, c or self.C_AMBER))
                if not ok:
                    self._log("Docker install incomplete. Start Docker Desktop, then press [D] again.", self.C_AMBER)
                    return
                self._log("Docker ready — starting deployment...", self.C_GREEN)
            else:
                self._log("Docker required. Install it and press [D] when ready.", self.C_AMBER)
                return
        docker_up(dest, lambda m, c=None: self._log(m, c or self.C_GREEN), self.cfg)

    def _do_export(self):
        cwd = find_installed() or get_project_dir()
        export_snapshot(cwd, lambda m, c=None: self._log(m, c or self.C_GREEN))

    def _do_close_shop(self):
        if not self._confirm("Close shop \u2014 stop app and create portable .pyz?"):
            return
        cwd = find_installed() or get_project_dir()
        snap = export_snapshot(cwd, lambda m, c=None: self._log(m, c or self.C_GREEN))
        if snap is None:
            self._log("WARNING: No database exported. Container data may be lost.", self.C_AMBER)
        docker_down(cwd, lambda m, c=None: self._log(m, c or self.C_AMBER))
        create_pyz(cwd, lambda m, c=None: self._log(m, c or self.C_GREEN))

    def _do_destroy(self):
        print(f"\n {self.C_RED}{self.BOLD}  \u2718 U FO REAL!?{self.RST}")
        print(f" {self._fg(242)}  This exports your DB, tears down the container,")
        print(f" {self._fg(242)}  and deletes all app files. Cannot be undone.{self.RST}")
        resp = self._input(f" {self.C_RED}Type 'yeah son' to confirm: {self.RST}")
        if resp.strip().lower() != "yeah son":
            self._log("Cancelled.", self.C_AMBER)
            return
        cwd = find_installed() or get_project_dir()
        who = get_username()
        ts = datetime.now().strftime("%d-%m-%Y at %I-%M-%S %p")
        src = cwd / DB_FILE
        if src.exists():
            fname = f"{who} destroyed everything on {ts}.json"
            shutil.copy2(src, get_backups_dir() / fname)
            self._log(f"Safety export -> {fname}", self.C_GREEN)
        docker_down(cwd, lambda m, c=None: self._log(m, c or self.C_AMBER))
        if (cwd / COMPOSE).exists():
            try:
                for item in cwd.iterdir():
                    if item.name in {BACKUPS_DIR, "launcher.py"}:
                        continue
                    if item.is_dir():
                        shutil.rmtree(item)
                    else:
                        item.unlink()
                self._log(f"Deleted app files in {cwd.name}", self.C_RED)
            except Exception as e:
                self._log(f"Error: {e}", self.C_RED)
        log_destruction(who, ts)
        self._log("Done. Backups preserved.", self.C_AMBER)

    def _show_help(self):
        print(f"\n{self._box_top(50, 'help')}")
        cmds = [
            ("D", "Deploy", "Extract .pyz + start Docker", self.C_GREEN),
            ("E", "Export", "Snapshot database to backups/", self.C_GREEN),
            ("C", "Close", "Export + stop + pack .pyz", self.C_AMBER),
            ("X", "Destroy", "Safety export + full teardown", self.C_RED),
            ("S", "Sync", "Force refresh all data", self.C_CYAN),
            ("H", "Help", "This screen", self.C_GRAY),
            ("Q", "Quit", "Exit the dashboard", self.C_GRAY),
        ]
        for key, name, desc, color in cmds:
            print(self._box_row(
                f"  {color}{self.BOLD}{key}{self.RST}  {self.C_LGRAY}{name:<9}{self.RST} {self._fg(242)}{desc}", 50))
        print(self._box_bot(50))
        self._input(f"\n {self._fg(242)}Press Enter...{self.RST}")

    # ── MAIN LOOP ─────────────────────────────────────────────────────────

    def _preflight(self):
        """Startup health-check — visible checklist so the user knows exactly what's happening."""
        # Virtualization check first (Windows-specific blocker)
        virt_ok, virt_detail = check_virtualization()
        checks = [
            ("Virtualization support", lambda: virt_ok),
            ("Docker installed", check_docker_installed),
            ("Docker daemon running", check_docker_running),
            ("Project files found", lambda: find_installed() is not None),
            ("Container responding", lambda: docker_stats() is not None),
            ("App online", lambda: check_app_online(self.port)),
        ]
        print(f"\n {self.C_CYAN}{self.BOLD}Preflight Check{self.RST}\n")
        all_ok = True
        for label, check_fn in checks:
            print(f"  {self._fg(242)}{self.ICON_ARR} {label}...{self.RST}", end="", flush=True)
            try:
                ok = check_fn()
            except Exception:
                ok = False
            if ok:
                detail = ""
                if label == "Virtualization support":
                    detail = f" {self._fg(242)}({virt_detail}){self.RST}"
                print(f"\r  {self.C_GREEN}\u2713 {label}{self.RST}{detail}          ")
            else:
                print(f"\r  {self.C_RED}\u2718 {label}{self.RST}          ")
                all_ok = False
                # Don't check downstream items if upstream failed
                if label == "Virtualization support":
                    print(f"  {self.C_RED}  {self.ICON_ARR} {virt_detail}{self.RST}")
                    if "WSL2 not installed" in virt_detail:
                        print(f"  {self.C_AMBER}  Fix: open an admin terminal and run:{self.RST}")
                        print(f"  {self.C_WHITE}  wsl --install{self.RST}")
                        print(f"  {self._fg(242)}  Then reboot and re-run this launcher.{self.RST}")
                    elif "BIOS" in virt_detail:
                        print(f"  {self.C_AMBER}  Fix: reboot into BIOS/UEFI and enable VT-x or AMD-V{self.RST}")
                        print(f"  {self._fg(242)}  Usually under CPU Configuration or Advanced settings.{self.RST}")
                    self._log("Cannot run Docker without virtualization.", self.C_RED)
                    break
                elif label == "Docker installed":
                    self._log("Docker not found — press [D] to deploy (will offer install)", self.C_AMBER)
                    break
                elif label == "Docker daemon running":
                    self._log("Docker daemon not running — start Docker Desktop, then press [S]", self.C_AMBER)
                    self._log("Or press [D] to deploy (will auto-start Docker)", self.C_AMBER)
                    break
                elif label == "Project files found":
                    self._log("No project files — press [D] to deploy from backup", self.C_AMBER)
                    break
                # Container/app not running is OK — just informational
        if all_ok:
            self._log("All systems go.", self.C_GREEN)
        print(f"\n  {self._fg(242)}Entering dashboard in 2s...{self.RST}", flush=True)
        self._time_mod.sleep(2)

    def run(self):
        if not sys.stdin.isatty():
            print("Error: Terminal mode requires an interactive terminal.")
            print("Use --help for options.")
            sys.exit(1)
        self._preflight()
        self._log("Dashboard started.", self.C_CYAN)
        task_counter = 0
        task_interval = 5
        try:
            while self.running:
                self._poll()
                task_counter += 1
                if task_counter >= task_interval:
                    self._poll_tasks()
                    task_counter = 0
                self._render()
                key = self._get_key(timeout=self.poll_interval)
                if key is None:
                    continue
                k = key.lower()
                if k == "q":
                    self.running = False
                elif k == "d":
                    self._do_deploy()
                elif k == "e":
                    self._do_export()
                elif k == "c":
                    self._do_close_shop()
                elif k == "x":
                    self._do_destroy()
                elif k == "s":
                    self._poll()
                    self._poll_tasks()
                    self._log("Refreshed.", self.C_CYAN)
                elif k in ("h", "?"):
                    self._show_help()
        except KeyboardInterrupt:
            pass
        finally:
            self._show_cursor()
        self._clear()
        print(f" {self.C_CYAN}Bye.{self.RST}")


# ── CLI PACK (no GUI) ───────────────────────────────────────────────────────

def _cli_pack():
    def log_print(msg, _color=None):
        print(msg.encode("ascii", "replace").decode())
    src = Path(__file__).resolve().parent
    if not (src / COMPOSE).exists():
        print(f"[ERROR] No {COMPOSE} found in {src}")
        sys.exit(1)
    create_pyz(src, log_print)


# ── EARLY EXIT for non-GUI modes (before tkinter import) ────────────────────

if __name__ == "__main__" and len(sys.argv) > 1:
    if sys.argv[1] in ("--help", "-h"):
        print("KAPPA ROADMAP — Deployment Manager v2.1")
        print()
        print("Usage: python launcher.py [option]")
        print()
        print("  (no args)     Launch GUI (requires display / Tkinter)")
        print("  --terminal    Launch terminal UI (headless/SSH safe)")
        print("  --tui, -t     Same as --terminal")
        print("  --pack        Create portable .pyz package (non-interactive)")
        print("  --help, -h    Show this help")
        sys.exit(0)
    elif sys.argv[1] == "--pack":
        _cli_pack()
        sys.exit(0)
    elif sys.argv[1] in ("--terminal", "--tui", "-t"):
        TerminalUI().run()
        sys.exit(0)


# ══════════════════════════════════════════════════════════════════════════════
#  GUI (Tkinter) — only reaches here for GUI mode
# ══════════════════════════════════════════════════════════════════════════════
try:
    import tkinter as tk
    from tkinter import filedialog
except (ImportError, ModuleNotFoundError):
    print("ERROR: Tkinter not available. Use --terminal for headless mode.")
    print("       On Linux: apt install python3-tk")
    print("       Run: python launcher.py --help")
    sys.exit(1)
except Exception as e:
    if "no display" in str(e).lower() or "DISPLAY" in str(e):
        print("ERROR: No display found. Use --terminal for headless mode.")
        print("       Run: python launcher.py --help")
        sys.exit(1)
    raise

class Tooltip:
    def __init__(self, widget, text):
        self.widget, self.text, self.tw = widget, text, None
        widget.bind("<Enter>", self._show)
        widget.bind("<Leave>", self._hide)

    def _show(self, _=None):
        x = self.widget.winfo_rootx() + self.widget.winfo_width() + 8
        y = self.widget.winfo_rooty() + 6
        self.tw = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        tw.configure(bg=BG_PANEL)
        border = tk.Frame(tw, bg=CYAN, padx=1, pady=1)
        border.pack()
        inner = tk.Frame(border, bg=BG_PANEL, padx=10, pady=7)
        inner.pack()
        tk.Label(inner, text=self.text, font=FONT_SMALL, bg=BG_PANEL,
                 fg=FG, justify="left", wraplength=300).pack()

    def _hide(self, _=None):
        if self.tw:
            self.tw.destroy()
            self.tw = None


class StatusBar(tk.Frame):
    """Compact status indicators: APP | CPU | RAM | NET | PIDs | TASKS"""
    def __init__(self, parent):
        super().__init__(parent, bg=BG_PANEL, padx=12, pady=5)
        self._app_dot = tk.Label(self, text="●", font=FONT_STATUS, bg=BG_PANEL, fg=FG_DIM)
        self._app_dot.pack(side="left")
        tk.Label(self, text=" APP ", font=FONT_SMALL, bg=BG_PANEL, fg=FG_DIM).pack(side="left")
        self._app_val = tk.Label(self, text="--", font=FONT_STATUS, bg=BG_PANEL, fg=FG_DIM)
        self._app_val.pack(side="left")
        self._sep()
        self._cpu_val = self._metric("CPU")
        self._sep()
        self._ram_val = self._metric("RAM")
        self._sep()
        self._net_val = self._metric("NET")
        self._sep()
        self._pids_val = self._metric("PID")
        self._sep()
        self._tasks_val = self._metric("TASKS")

    def _sep(self):
        tk.Label(self, text="│", font=FONT_SMALL, bg=BG_PANEL, fg="#333").pack(side="left", padx=6)

    def _metric(self, label):
        tk.Label(self, text=f"{label} ", font=FONT_SMALL, bg=BG_PANEL, fg=FG_DIM).pack(side="left")
        v = tk.Label(self, text="--", font=FONT_SMALL, bg=BG_PANEL, fg=FG_DIM)
        v.pack(side="left")
        return v

    def update(self, online, stats, task_count):
        if online:
            self._app_dot.config(fg=GREEN)
            self._app_val.config(text="ONLINE", fg=GREEN)
        else:
            self._app_dot.config(fg=RED_DIM)
            self._app_val.config(text="OFFLINE", fg=RED_DIM)
        if stats:
            self._cpu_val.config(text=stats["cpu"], fg=CYAN)
            self._ram_val.config(text=stats["mem"], fg=AMBER)
            self._net_val.config(text=stats.get("net_str", "--"), fg=MAGENTA)
            self._pids_val.config(text=stats.get("pids", "--"), fg=FG)
        else:
            for v in (self._cpu_val, self._ram_val, self._net_val, self._pids_val):
                v.config(text="--", fg=FG_DIM)
        if task_count is not None:
            self._tasks_val.config(text=str(task_count), fg=GREEN)
        else:
            self._tasks_val.config(text="--", fg=FG_DIM)


class TaskList(tk.Frame):
    """btop-style scrollable task table."""
    def __init__(self, parent):
        super().__init__(parent, bg=BG)
        # Header
        hdr_frm = tk.Frame(self, bg=BG)
        hdr_frm.pack(fill="x")
        self._hdr_lbl = tk.Label(hdr_frm, text="TASK REGISTRY (0 tasks)",
                                 font=FONT_TREE, bg=BG, fg=CYAN, anchor="w")
        self._hdr_lbl.pack(side="left")

        # Summary badges
        self._summary = tk.Label(hdr_frm, text="", font=FONT_SMALL, bg=BG, fg=FG_DIM)
        self._summary.pack(side="right")

        # Column header
        cols = f"{'CAT':<8}{'ENTITY':<16}{'TASK':<30}{'ETT':<10}{'STATUS':<12}"
        tk.Label(self, text=cols, font=FONT_TABLE, bg=BG_PANEL, fg=FG_DIM,
                 anchor="w", padx=6, pady=2).pack(fill="x")

        # Table body (Text widget for colored rows)
        self._text = tk.Text(self, height=8, bg=BG_TABLE, fg=FG, font=FONT_TABLE,
                             relief="flat", state="disabled", padx=6, pady=2,
                             wrap="none", cursor="arrow")
        self._text.pack(fill="x")
        # Row color tags
        for tag, color in [("need", MAGENTA), ("should", CYAN), ("nice", GREEN),
                           ("prog", CYAN), ("plan", AMBER), ("comp", GREEN),
                           ("blk", RED), ("dim", FG_DIM)]:
            self._text.tag_config(tag, foreground=color)

    def update(self, tasks):
        if tasks is None:
            return
        self._hdr_lbl.config(text=f"TASK REGISTRY ({len(tasks)} tasks)")

        # Summary counts
        counts = {"IN PROGRESS": 0, "PLANNED": 0, "COMPLETE": 0, "BLOCKED": 0}
        for t in tasks:
            s = t.get("status", "")
            if s in counts:
                counts[s] += 1
        parts = [f"PROG:{counts['IN PROGRESS']}", f"PLAN:{counts['PLANNED']}",
                 f"DONE:{counts['COMPLETE']}", f"BLK:{counts['BLOCKED']}"]
        self._summary.config(text="  ".join(parts))

        # Render rows
        self._text.configure(state="normal")
        self._text.delete("1.0", "end")
        for t in tasks:
            cat_short = {"Need to Have": "NEED", "Should Have": "SHOULD",
                         "Nice to Have": "NICE"}.get(t.get("cat", ""), "?")
            cat_tag = {"NEED": "need", "SHOULD": "should", "NICE": "nice"}.get(cat_short, "dim")
            entity = (t.get("entity", "")[:14] + "..") if len(t.get("entity", "")) > 16 else t.get("entity", "")
            task_str = (t.get("task", "")[:28] + "..") if len(t.get("task", "")) > 30 else t.get("task", "")
            ett = t.get("ett", "TBD") or "TBD"
            status = t.get("status", "PLANNED")
            st_icon = {"IN PROGRESS": "●", "COMPLETE": "✓", "BLOCKED": "✕"}.get(status, "○")
            st_tag = {"IN PROGRESS": "prog", "COMPLETE": "comp", "BLOCKED": "blk"}.get(status, "plan")

            # Write each field with its own tag
            self._text.insert("end", f"{cat_short:<8}", cat_tag)
            self._text.insert("end", f"{entity:<16}", "dim")
            self._text.insert("end", f"{task_str:<30}")
            self._text.insert("end", f"{ett:<10}", "dim")
            self._text.insert("end", f"{st_icon} {status:<10}", st_tag)
            self._text.insert("end", "\n")
        self._text.configure(state="disabled")


class SparklinePanel(tk.Frame):
    """Rolling sparkline graphs for CPU, RAM, NET I/O."""
    def __init__(self, parent, width=50):
        super().__init__(parent, bg=BG_PANEL, padx=10, pady=4)
        self._width = width
        self._cpu_hist = deque(maxlen=width)
        self._ram_hist = deque(maxlen=width)
        self._net_hist = deque(maxlen=width)
        self._prev_net = None

        # CPU sparkline
        row1 = tk.Frame(self, bg=BG_PANEL)
        row1.pack(fill="x")
        tk.Label(row1, text="CPU ", font=FONT_SMALL, bg=BG_PANEL, fg=CYAN, width=4,
                 anchor="e").pack(side="left")
        self._cpu_spark = tk.Label(row1, text="", font=FONT_SPARK, bg=BG_PANEL, fg=CYAN,
                                   anchor="w")
        self._cpu_spark.pack(side="left", fill="x", expand=True)
        self._cpu_pct = tk.Label(row1, text="", font=FONT_SMALL, bg=BG_PANEL, fg=CYAN,
                                 width=6, anchor="e")
        self._cpu_pct.pack(side="right")

        # RAM sparkline
        row2 = tk.Frame(self, bg=BG_PANEL)
        row2.pack(fill="x")
        tk.Label(row2, text="RAM ", font=FONT_SMALL, bg=BG_PANEL, fg=AMBER, width=4,
                 anchor="e").pack(side="left")
        self._ram_spark = tk.Label(row2, text="", font=FONT_SPARK, bg=BG_PANEL, fg=AMBER,
                                   anchor="w")
        self._ram_spark.pack(side="left", fill="x", expand=True)
        self._ram_pct = tk.Label(row2, text="", font=FONT_SMALL, bg=BG_PANEL, fg=AMBER,
                                 width=6, anchor="e")
        self._ram_pct.pack(side="right")

        # NET I/O sparkline (combined rx+tx rate)
        row3 = tk.Frame(self, bg=BG_PANEL)
        row3.pack(fill="x")
        tk.Label(row3, text="NET ", font=FONT_SMALL, bg=BG_PANEL, fg=MAGENTA, width=4,
                 anchor="e").pack(side="left")
        self._net_spark = tk.Label(row3, text="", font=FONT_SPARK, bg=BG_PANEL, fg=MAGENTA,
                                   anchor="w")
        self._net_spark.pack(side="left", fill="x", expand=True)
        self._net_rate = tk.Label(row3, text="", font=FONT_SMALL, bg=BG_PANEL, fg=MAGENTA,
                                  width=10, anchor="e")
        self._net_rate.pack(side="right")

    def push(self, stats):
        """Push new stats sample and redraw."""
        if stats:
            self._cpu_hist.append(stats["cpu_pct"])
            self._ram_hist.append(stats["mem_pct"])

            # Calculate NET I/O rate from cumulative values
            cur_net = stats.get("net_rx", 0) + stats.get("net_tx", 0)
            if self._prev_net is not None:
                delta = max(cur_net - self._prev_net, 0)
                # Normalize: cap at some reasonable max for sparkline (10MB/s = 100%)
                net_pct = min((delta / (10 * 1024 * 1024)) * 100, 100)
                self._net_hist.append(net_pct)
                self._net_rate.config(text=format_rate(delta))
            else:
                self._net_hist.append(0)
                self._net_rate.config(text="--")
            self._prev_net = cur_net

            self._cpu_pct.config(text=stats["cpu"])
            self._ram_pct.config(text=stats["mem"])
        else:
            self._cpu_hist.append(0)
            self._ram_hist.append(0)
            self._net_hist.append(0)
            self._cpu_pct.config(text="--")
            self._ram_pct.config(text="--")
            self._net_rate.config(text="--")

        self._cpu_spark.config(text=sparkline_str(self._cpu_hist, self._width))
        self._ram_spark.config(text=sparkline_str(self._ram_hist, self._width))
        self._net_spark.config(text=sparkline_str(self._net_hist, self._width))


class BackupTree(tk.Frame):
    def __init__(self, parent, on_change=None):
        super().__init__(parent, bg=BG)
        self._expanded = True
        self._on_change = on_change
        self._selected = tk.IntVar(value=0)
        self._backups = []
        self._header = tk.Label(self, text="▼ BACKUPS (0)", font=FONT_TREE,
                                bg=BG, fg=CYAN, cursor="hand2", anchor="w")
        self._header.pack(fill="x", padx=4)
        self._header.bind("<Button-1>", self._toggle)
        self._container = tk.Frame(self, bg=BG)
        self._container.pack(fill="x", padx=8)
        self.refresh()

    def _toggle(self, _=None):
        self._expanded = not self._expanded
        if self._expanded:
            self._container.pack(fill="x", padx=8)
        else:
            self._container.pack_forget()
        self._update_header()

    def _update_header(self):
        arrow = "▼" if self._expanded else "▶"
        self._header.config(text=f"{arrow} BACKUPS ({len(self._backups)})")

    def refresh(self):
        self._backups = list_backups()
        self._selected.set(0)
        for w in self._container.winfo_children():
            w.destroy()
        if not self._backups:
            tk.Label(self._container, text="  (no backups)", font=FONT_SMALL,
                     bg=BG, fg=FG_DIM).pack(anchor="w")
            self._update_header()
            return
        for i, b in enumerate(self._backups[:MAX_BACKUPS_SHOWN]):
            dt = b["mtime"].strftime("%Y-%m-%d  %I:%M %p")
            sz = f"{b['size']/(1024*1024):.1f}MB"
            marker = "●" if i == 0 else "○"
            tag = "  <- latest" if i == 0 else ""
            emb = "  (this pkg)" if b.get("embedded") else ""
            tk.Radiobutton(
                self._container, text=f"  {marker} {dt}  {sz}{tag}{emb}",
                variable=self._selected, value=i, font=FONT_SMALL, bg=BG,
                fg=FG if i == 0 else FG_DIM, selectcolor=BG_PANEL,
                activebackground=BG, activeforeground=CYAN, indicatoron=0,
                relief="flat", bd=0, anchor="w", padx=4, pady=1, cursor="hand2",
                command=self._on_change if self._on_change else lambda: None,
            ).pack(fill="x")
        extra = len(self._backups) - MAX_BACKUPS_SHOWN
        if extra > 0:
            tk.Label(self._container, text=f"     +{extra} more", font=FONT_SMALL,
                     bg=BG, fg=FG_DIM).pack(anchor="w")
        self._update_header()

    def get_selected(self):
        idx = self._selected.get()
        return self._backups[idx]["path"] if 0 <= idx < len(self._backups) else None

    def has_backups(self):
        return len(self._backups) > 0


class OptionsDialog(tk.Toplevel):
    """In-app options panel."""
    def __init__(self, parent, cfg, on_save):
        super().__init__(parent)
        self.title("OPTIONS")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.grab_set()
        parent.eval(f'tk::PlaceWindow {str(self)} center')
        self._cfg = dict(cfg)
        self._on_save = on_save

        tk.Frame(self, bg=CYAN, height=2).pack(fill="x")
        tk.Label(self, text="OPTIONS", font=FONT_BTN, bg=BG, fg=CYAN).pack(pady=(10, 8))

        body = tk.Frame(self, bg=BG, padx=20)
        body.pack(fill="x")

        # Poll interval
        tk.Label(body, text="POLL INTERVAL", font=FONT_SMALL, bg=BG, fg=FG_DIM,
                 anchor="w").pack(fill="x")
        poll_frm = tk.Frame(body, bg=BG)
        poll_frm.pack(fill="x", pady=(0, 8))
        self._poll_var = tk.IntVar(value=cfg.get("poll_interval", 1000))
        for ms, label in [(500, "500ms"), (1000, "1s"), (2000, "2s"), (5000, "5s")]:
            tk.Radiobutton(poll_frm, text=label, variable=self._poll_var, value=ms,
                           font=FONT_SMALL, bg=BG, fg=CYAN, selectcolor=BG_PANEL,
                           activebackground=BG, activeforeground=CYAN,
                           indicatoron=0, relief="flat", padx=8, pady=3, cursor="hand2",
                           ).pack(side="left", padx=2)

        # Port
        tk.Label(body, text="PORT", font=FONT_SMALL, bg=BG, fg=FG_DIM,
                 anchor="w").pack(fill="x")
        self._port_var = tk.StringVar(value=str(cfg.get("port", 3000)))
        tk.Entry(body, textvariable=self._port_var, font=FONT_MONO, bg=BG_PANEL,
                 fg=CYAN, insertbackground=CYAN, relief="flat", width=8
                 ).pack(anchor="w", pady=(0, 8))

        # Auto browser
        self._browser_var = tk.BooleanVar(value=cfg.get("auto_open_browser", True))
        tk.Checkbutton(body, text="Auto-open browser on deploy", variable=self._browser_var,
                       font=FONT_SMALL, bg=BG, fg=FG, selectcolor=BG_PANEL,
                       activebackground=BG, activeforeground=CYAN,
                       ).pack(anchor="w", pady=(0, 8))

        # Sparkline width
        tk.Label(body, text="SPARKLINE HISTORY (samples)", font=FONT_SMALL,
                 bg=BG, fg=FG_DIM, anchor="w").pack(fill="x")
        self._spark_var = tk.StringVar(value=str(cfg.get("sparkline_width", 50)))
        tk.Entry(body, textvariable=self._spark_var, font=FONT_MONO, bg=BG_PANEL,
                 fg=CYAN, insertbackground=CYAN, relief="flat", width=8
                 ).pack(anchor="w", pady=(0, 12))

        # Buttons
        tk.Frame(self, bg="#222", height=1).pack(fill="x", padx=20)
        btn_row = tk.Frame(self, bg=BG)
        btn_row.pack(pady=12, padx=20, fill="x")
        tk.Button(btn_row, text="SAVE", font=FONT_BTN, bg=BG_PANEL, fg=GREEN,
                  activebackground="#0a1a0a", activeforeground=GREEN, relief="flat",
                  padx=20, pady=6, cursor="hand2", command=self._save
                  ).pack(side="left", expand=True, fill="x", padx=(0, 4))
        tk.Button(btn_row, text="CANCEL", font=FONT_BTN, bg=BG_PANEL, fg=FG_DIM,
                  activebackground="#1a1a1a", activeforeground=FG, relief="flat",
                  padx=20, pady=6, cursor="hand2", command=self.destroy
                  ).pack(side="right", expand=True, fill="x")

    def _save(self):
        try:
            port = int(self._port_var.get())
        except ValueError:
            port = 3000
        try:
            spark_w = int(self._spark_var.get())
        except ValueError:
            spark_w = 50
        self._cfg.update({
            "poll_interval": self._poll_var.get(),
            "port": port,
            "auto_open_browser": self._browser_var.get(),
            "sparkline_width": max(10, min(spark_w, 200)),
        })
        save_config(self._cfg)
        self._on_save(self._cfg)
        self.destroy()


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN APPLICATION
# ══════════════════════════════════════════════════════════════════════════════

class KappaLauncher(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("KAPPA ROADMAP // DEPLOYMENT MANAGER")
        self.configure(bg=BG)
        self.resizable(False, False)
        self._cfg = load_config()
        self._polling = True
        self._status_busy = False
        self._task_data = None
        self._action_btns = []
        self._build_ui()
        self.eval('tk::PlaceWindow . center')
        self._poll_status()
        self._poll_tasks()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _on_close(self):
        self._polling = False
        self.destroy()

    # ── BUILD UI ──────────────────────────────────────────────────────────────
    def _build_ui(self):
        self._build_header()
        self._build_status_bar()
        self._build_task_list()
        self._build_sparklines()
        self._build_backup_tree()
        self._build_action_bar()
        self._build_danger_zone()
        self._build_log_box()
        self._build_destruction_line()

    def _build_header(self):
        tk.Frame(self, bg=CYAN, height=2).pack(fill="x")
        hdr = tk.Frame(self, bg=BG, pady=6)
        hdr.pack(fill="x", padx=20)
        tk.Label(hdr, text="KAPPA COMPUTER SYSTEMS", font=FONT_HEAD,
                 bg=BG, fg=CYAN).pack(side="left")
        # Options button
        opt_btn = tk.Button(hdr, text="[OPTIONS]", font=FONT_SMALL, bg=BG, fg=FG_DIM,
                            activebackground="#1c1c1c", activeforeground=CYAN,
                            relief="flat", bd=0, cursor="hand2",
                            command=self._open_options)
        opt_btn.pack(side="right", padx=(0, 4))
        opt_btn.bind("<Enter>", lambda e: opt_btn.config(fg=CYAN))
        opt_btn.bind("<Leave>", lambda e: opt_btn.config(fg=FG_DIM))

        tk.Label(self, text="DEPLOYMENT MANAGER  //  v2.1", font=FONT_SMALL,
                 bg=BG, fg=FG_DIM, anchor="w").pack(fill="x", padx=20)
        tk.Frame(self, bg=CYAN, height=1).pack(fill="x", padx=20, pady=(2, 4))

    def _build_status_bar(self):
        outer = tk.Frame(self, bg="#1a1a1a", padx=1, pady=1)
        outer.pack(fill="x", padx=20, pady=(0, 4))
        self._status_bar = StatusBar(outer)
        self._status_bar.pack(fill="x")

    def _build_task_list(self):
        tk.Frame(self, bg="#1a1a1a", height=1).pack(fill="x", padx=20)
        self._task_list = TaskList(self)
        self._task_list.pack(fill="x", padx=20, pady=(4, 2))

    def _build_sparklines(self):
        tk.Frame(self, bg="#1a1a1a", height=1).pack(fill="x", padx=20, pady=(2, 0))
        outer = tk.Frame(self, bg="#1a1a1a", padx=1, pady=1)
        outer.pack(fill="x", padx=20, pady=(2, 4))
        self._sparklines = SparklinePanel(outer, width=self._cfg.get("sparkline_width", 50))
        self._sparklines.pack(fill="x")

    def _build_backup_tree(self):
        tk.Frame(self, bg="#1a1a1a", height=1).pack(fill="x", padx=20)
        self._backup_tree = BackupTree(self)
        self._backup_tree.pack(fill="x", padx=20, pady=(4, 2))
        tk.Frame(self, bg="#1a1a1a", height=1).pack(fill="x", padx=20)

    def _build_action_bar(self):
        """Compact action buttons: Deploy + Export on one row."""
        frm = tk.Frame(self, bg=BG)
        frm.pack(fill="x", padx=20, pady=(4, 2))

        trunc = truncate_path(str(get_project_dir()), 30)
        self._make_btn(frm, f"DEPLOY >>> [ HERE ({trunc}) ]", CYAN, self._do_deploy_here,
                       tip="Deploy selected backup to current directory.")
        self._make_btn(frm, "DEPLOY >>> [ SOMEWHERE ELSE... ]", CYAN, self._do_deploy_pick,
                       tip="Pick a folder, then deploy there.")
        self._make_btn(frm, "EXPORT LIVE SNAPSHOT", GREEN, self._do_export,
                       tip="Snapshot roadmap.json to backups/.")

    def _build_danger_zone(self):
        tk.Frame(self, bg="#222", height=1).pack(fill="x", padx=20, pady=(6, 0))
        tk.Label(self, text="DANGER ZONE", font=FONT_SMALL, bg=BG, fg=RED).pack(anchor="w", padx=20)
        outer = tk.Frame(self, bg=RED_DIM, padx=1, pady=1)
        outer.pack(fill="x", padx=20, pady=(2, 6))
        inner = tk.Frame(outer, bg=BG_DANGER, padx=8, pady=6)
        inner.pack(fill="x")
        self._make_btn(inner, "CLOSE SHOP & PACK UP", RED, self._do_close_shop, danger=True,
                       tip="Export DB, stop Docker, create portable .pyz.")
        tk.Frame(inner, bg="#3a0a0a", height=1).pack(fill="x", pady=(4, 0))
        self._make_btn(inner, "~~ DESTROY EVERYTHING ~~", RED, self._do_destroy,
                       danger=True, bold=True,
                       tip="Export DB, tear down, delete all app files.")

    def _build_log_box(self):
        tk.Frame(self, bg="#222", height=1).pack(fill="x", padx=20)
        self.log_box = tk.Text(self, height=6, bg="#050505", fg=FG_DIM, font=FONT_SMALL,
                               relief="flat", insertbackground=CYAN, state="disabled",
                               padx=6, pady=4, wrap="word")
        self.log_box.pack(fill="x", padx=20)
        for tag, c in [("cyan", CYAN), ("amber", AMBER), ("green", GREEN),
                       ("red", RED), ("dim", FG_DIM), ("fg", FG)]:
            self.log_box.tag_config(tag, foreground=c)
        if not check_docker_installed():
            self._log("[WARN] Docker not found -- will auto-install on first deploy.", AMBER)
        else:
            self._log("System ready. Docker detected.", GREEN)
        if is_running_from_pyz():
            self._log(f"[PYZ] Running from: {get_pyz_path().name}", CYAN)

    def _build_destruction_line(self):
        tk.Frame(self, bg=RED_DIM, height=1).pack(fill="x", padx=20, pady=(3, 0))
        last = read_last_destruction()
        text = f"LAST DESTRUCTION: {last}" if last else "LAST DESTRUCTION: none on record"
        self._dest_lbl = tk.Label(self, text=text, font=FONT_SMALL, bg=BG,
                                  fg=RED_DIM if last else FG_DIM, anchor="w", padx=4)
        self._dest_lbl.pack(fill="x", padx=20, pady=(2, 6))

    # ── HELPERS ───────────────────────────────────────────────────────────────
    def _make_btn(self, parent, label, color, cmd, *, danger=False, bold=False, tip=None):
        w = "bold" if bold else "normal"
        bg_n = BG_DANGER if danger else BG
        bg_h = "#1a0808" if danger else "#1c1c1c"
        b = tk.Button(parent, text=label, font=FONT_BTN if not bold else (FONT_BTN[0], FONT_BTN[1], "bold"),
                      bg=bg_n, fg=color, activebackground=bg_h, activeforeground=color,
                      relief="flat", cursor="hand2", anchor="w", padx=12, pady=5, bd=0,
                      command=cmd, disabledforeground="#444")
        b.pack(fill="x", pady=1)
        b.bind("<Enter>", lambda e, _b=b: _b.config(bg=bg_h) if _b["state"] != "disabled" else None)
        b.bind("<Leave>", lambda e, _b=b: _b.config(bg=bg_n) if _b["state"] != "disabled" else None)
        if tip:
            Tooltip(b, tip)
        self._action_btns.append(b)
        return b

    def _log(self, msg, color=None):
        """Thread-safe log — routes widget mutation to the main thread."""
        self.after(0, self._log_impl, msg, color)

    def _log_impl(self, msg, color=None):
        tag_map = {CYAN: "cyan", AMBER: "amber", GREEN: "green",
                   RED: "red", FG_DIM: "dim", FG: "fg"}
        self.log_box.configure(state="normal")
        self.log_box.insert("end", msg.strip() + "\n", tag_map.get(color, "fg"))
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _set_buttons_enabled(self, enabled):
        state = "normal" if enabled else "disabled"
        for btn in self._action_btns:
            try:
                btn.configure(state=state)
            except Exception:
                pass

    def _run_threaded(self, fn):
        """Run fn in a daemon thread. Disables action buttons while running."""
        self._set_buttons_enabled(False)
        def _wrapper():
            try:
                fn()
            except Exception as e:
                self._log(f"[ERROR] {e}", RED)
            finally:
                self.after(0, self._set_buttons_enabled, True)
        threading.Thread(target=_wrapper, daemon=True).start()

    def _open_options(self):
        OptionsDialog(self, self._cfg, self._apply_config)

    def _apply_config(self, cfg):
        self._cfg = cfg
        self._log(f"[CONFIG] Saved. Poll: {cfg['poll_interval']}ms, Port: {cfg['port']}", GREEN)

    # ── POLLING ───────────────────────────────────────────────────────────────
    def _poll_status(self):
        if not self._polling:
            return
        if not self._status_busy:
            self._status_busy = True
            threading.Thread(target=self._fetch_status, daemon=True).start()
        self.after(self._cfg.get("poll_interval", 1000), self._poll_status)

    def _fetch_status(self):
        try:
            port = self._cfg.get("port", 3000)
            online = check_app_online(port)
            stats = docker_stats()
            count = len(self._task_data) if self._task_data else None
            if self._polling:
                self.after(0, self._apply_status, online, stats, count)
        finally:
            self._status_busy = False

    def _apply_status(self, online, stats, count):
        self._status_bar.update(online, stats, count)
        self._sparklines.push(stats)

    def _poll_tasks(self):
        """Fetch task list every 5 seconds (independent of stats polling)."""
        if not self._polling:
            return
        threading.Thread(target=self._fetch_tasks, daemon=True).start()
        self.after(5000, self._poll_tasks)

    def _fetch_tasks(self):
        port = self._cfg.get("port", 3000)
        data = fetch_tasks(port)
        if data is not None:
            self._task_data = data
            if self._polling:
                self.after(0, self._task_list.update, data)

    # ── ACTIONS ───────────────────────────────────────────────────────────────
    def _ensure_docker(self):
        if check_docker_installed():
            return True
        self._log("[DOCKER] Not installed. Attempting auto-install ...", AMBER)
        if install_docker(self._log):
            # install_docker now handles PATH refresh and waiting
            self._log("[DOCKER] Ready.", GREEN)
            return True
        self._log("[DOCKER] Install incomplete. Start Docker Desktop and retry.", AMBER)
        return False

    def _do_deploy_here(self):
        def _go():
            sel = self._backup_tree.get_selected()
            if not sel:
                self._log("[ERROR] No backup selected.", RED)
                return
            if not self._ensure_docker():
                return
            target = find_installed() or get_project_dir() / APP_NAME
            extract_from_pyz(sel, target, self._log)
            docker_up(target, self._log, self._cfg)
            self.after(0, self._backup_tree.refresh)
        self._run_threaded(_go)

    def _do_deploy_pick(self):
        dest = filedialog.askdirectory(title="Choose deployment directory")
        if not dest:
            return
        def _go():
            sel = self._backup_tree.get_selected()
            if not sel:
                self._log("[ERROR] No backup selected.", RED)
                return
            if not self._ensure_docker():
                return
            target = Path(dest) / APP_NAME
            extract_from_pyz(sel, target, self._log)
            docker_up(target, self._log, self._cfg)
        self._run_threaded(_go)

    def _do_export(self):
        def _go():
            cwd = find_installed()
            if not cwd:
                self._log("[ERROR] No installed app found.", RED)
                return
            export_snapshot(cwd, self._log)
        self._run_threaded(_go)

    def _do_close_shop(self):
        from tkinter import messagebox
        if not messagebox.askokcancel(
            "Close Shop",
            "This will stop the running app, export the database,\n"
            "and create a portable .pyz archive.\n\nContinue?",
            parent=self,
        ):
            return
        def _go():
            cwd = find_installed()
            if not cwd:
                self._log("[ERROR] No installed app found.", RED)
                return
            snap = export_snapshot(cwd, self._log)
            if snap is None:
                self._log("[WARN] No database exported. Container data may be lost.", AMBER)
            docker_down(cwd, self._log)
            create_pyz(cwd, self._log)
            self._log("[DONE] Packed and ready to move.", GREEN)
            self.after(0, self._backup_tree.refresh)
        self._run_threaded(_go)

    def _do_destroy(self):
        dlg = tk.Toplevel(self)
        dlg.title("U FO REAL!?")
        dlg.configure(bg=BG)
        dlg.resizable(False, False)
        dlg.grab_set()
        self.eval(f'tk::PlaceWindow {str(dlg)} center')
        tk.Frame(dlg, bg=RED, height=3).pack(fill="x")
        tk.Label(dlg, text="U FO REAL!?", font=("Courier New", 20, "bold"),
                 bg=BG, fg=RED).pack(pady=(16, 4))
        tk.Label(dlg, text="This will export your database, tear down\nthe container, "
                 "and delete all app files.\nThis cannot be undone.",
                 font=FONT_SMALL, bg=BG, fg=FG, justify="center").pack(pady=(0, 16))
        tk.Frame(dlg, bg="#222", height=1).pack(fill="x", padx=20)
        row = tk.Frame(dlg, bg=BG)
        row.pack(pady=14, padx=20, fill="x")

        def _yeah():
            dlg.destroy()
            self._run_threaded(self._destroy_for_real)

        tk.Button(row, text="yeah son", font=FONT_BTN, bg=RED_DIM, fg=RED,
                  activebackground="#2a0000", activeforeground=RED, relief="flat",
                  padx=20, pady=8, cursor="hand2", command=_yeah
                  ).pack(side="left", expand=True, fill="x", padx=(0, 6))
        tk.Button(row, text="hell naw", font=FONT_BTN, bg=BG_PANEL, fg=GREEN,
                  activebackground="#0a1a0a", activeforeground=GREEN, relief="flat",
                  padx=20, pady=8, cursor="hand2", command=dlg.destroy
                  ).pack(side="right", expand=True, fill="x")

    def _destroy_for_real(self):
        cwd = find_installed() or get_project_dir()
        self._log("[DESTROY] Initiating destruction sequence ...", RED)
        who = get_username()
        ts = datetime.now().strftime("%d-%m-%Y at %I-%M-%S %p")
        src = cwd / DB_FILE
        if src.exists():
            fname = f"{who} destroyed everything on {ts}.json"
            shutil.copy2(src, get_backups_dir() / fname)
            self._log(f"[DB] Safety export -> {fname}", GREEN)
        else:
            self._log("[DB] No database found.", FG_DIM)
        docker_down(cwd, self._log)
        if (cwd / COMPOSE).exists():
            try:
                for item in cwd.iterdir():
                    if item.name in {BACKUPS_DIR, "launcher.py"}:
                        continue
                    if item.is_dir():
                        shutil.rmtree(item)
                    else:
                        item.unlink()
                self._log(f"[RM] Deleted app files in {cwd.name}", RED)
            except Exception as e:
                self._log(f"[RM] Error: {e}", RED)
        log_destruction(who, ts)
        self.after(0, self._refresh_destruction_line)
        self._log("[DESTROY] Done. Backups preserved.", AMBER)

    def _refresh_destruction_line(self):
        last = read_last_destruction()
        if last:
            self._dest_lbl.config(text=f"LAST DESTRUCTION: {last}", fg=RED_DIM)



# ══════════════════════════════════════════════════════════════════════════════
#  GUI ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    app = KappaLauncher()
    app.mainloop()
