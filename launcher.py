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

FONT_MONO   = ("Courier New", 11)
FONT_HEAD   = ("Courier New", 15, "bold")
FONT_BTN    = ("Courier New", 11, "bold")
FONT_SMALL  = ("Courier New", 9)
FONT_STATUS = ("Courier New", 10)
FONT_TREE   = ("Courier New", 10)
FONT_TABLE  = ("Courier New", 9)
FONT_SPARK  = ("Courier New", 12)

EXCLUDE_DIRS = {"node_modules", ".git", "backups", "__pycache__", ".claude"}
EXCLUDE_EXTS = {".pyc", ".pyo"}

# ── CONFIG ────────────────────────────────────────────────────────────────────

def _config_path():
    return get_project_dir() / CONFIG_FILE

def load_config():
    p = _config_path()
    if p.exists():
        try:
            return {**DEFAULT_CONFIG, **json.loads(p.read_text())}
        except Exception:
            pass
    return dict(DEFAULT_CONFIG)

def save_config(cfg):
    _config_path().write_text(json.dumps(cfg, indent=2))

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
                           capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout + r.stderr
    except FileNotFoundError:
        return 1, f"Command not found: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return 1, f"Command timed out after {timeout}s"

# ── DOCKER HELPERS ────────────────────────────────────────────────────────────

def check_docker_installed():
    code, _ = run_cmd(["docker", "--version"], timeout=10)
    return code == 0

def docker_up(cwd, log_fn, cfg=None):
    cfg = cfg or DEFAULT_CONFIG
    port = cfg.get("port", 3000)
    log_fn("[DOCKER] Building & starting container ...", AMBER)
    code, out = run_cmd(["docker", "compose", "up", "-d", "--build"], cwd=cwd)
    log_fn(out.strip() or "(no output)", FG_DIM)
    if code != 0:
        log_fn("[DOCKER] Failed — is Docker running?", RED)
        return False
    log_fn(f"[DOCKER] Live at http://localhost:{port}", GREEN)
    if cfg.get("auto_open_browser", True):
        webbrowser.open(f"http://localhost:{port}")
    return True

def docker_down(cwd, log_fn):
    log_fn("[DOCKER] Stopping container ...", AMBER)
    code, out = run_cmd(["docker", "compose", "down"], cwd=cwd)
    log_fn(out.strip() or "(no output)", FG_DIM)
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
        ["docker", "stats", CONTAINER_NAME, "--no-stream",
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

def install_docker(log_fn):
    if sys.platform == "win32":
        log_fn("[DOCKER] Attempting install via winget ...", AMBER)
        code, out = run_cmd([
            "winget", "install", "Docker.DockerDesktop",
            "--silent", "--accept-package-agreements",
            "--accept-source-agreements",
        ], timeout=300)
        if code == 0:
            log_fn("[DOCKER] Installed! Start Docker Desktop, then retry.", GREEN)
            return True
        log_fn("[DOCKER] winget failed. Opening download page ...", AMBER)
        webbrowser.open("https://docs.docker.com/desktop/install/windows-install/")
        log_fn("[DOCKER] Install Docker Desktop, then retry.", AMBER)
        return False
    else:
        log_fn("[DOCKER] Attempting install via get.docker.com ...", AMBER)
        code, out = run_cmd(
            ["bash", "-c", "curl -fsSL https://get.docker.com | sh"], timeout=300)
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
        urllib.request.urlopen(f"http://localhost:{port}/api/tasks", timeout=2)
        return True
    except Exception:
        return False

def fetch_tasks(port=3000):
    """Fetch full task list from API. Returns list or None."""
    try:
        resp = urllib.request.urlopen(f"http://localhost:{port}/api/tasks", timeout=2)
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
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr("__main__.py", launcher_src)
        for f in src_dir.rglob("*"):
            if not f.is_file():
                continue
            rel = f.relative_to(src_dir)
            if any(part in EXCLUDE_DIRS for part in rel.parts):
                continue
            if rel.suffix in EXCLUDE_EXTS or rel.name == "launcher.py":
                continue
            z.writestr(f"app/{rel.as_posix()}", f.read_bytes())
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
    log_fn(f"[UNPACK] -> {target}", CYAN)
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(str(pyz_path), 'r') as z:
        for info in z.infolist():
            if not info.filename.startswith("app/") or len(info.filename) <= 4:
                continue
            rel = info.filename[4:]
            dest_path = target / rel
            if info.filename.endswith('/'):
                dest_path.mkdir(parents=True, exist_ok=True)
            else:
                dest_path.parent.mkdir(parents=True, exist_ok=True)
                with z.open(info) as sf, open(dest_path, 'wb') as df:
                    df.write(sf.read())
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
    with open(get_backups_dir() / DESTRUCTION_LOG, 'a') as f:
        f.write(f"{username} // {timestamp}\n")

def read_last_destruction():
    p = get_backups_dir() / DESTRUCTION_LOG
    if not p.exists():
        return None
    try:
        lines = [l.strip() for l in p.read_text().splitlines() if l.strip()]
        return lines[-1] if lines else None
    except Exception:
        return None

# ══════════════════════════════════════════════════════════════════════════════
#  TERMINAL MODE (--terminal) — no Tkinter, no display server required
# ══════════════════════════════════════════════════════════════════════════════

class TerminalUI:
    """Pure ANSI terminal interface — works on headless servers, SSH, etc."""

    # ANSI color codes
    RST     = "\033[0m"
    BOLD    = "\033[1m"
    DIM     = "\033[2m"
    T_CYAN  = "\033[96m"
    T_GREEN = "\033[92m"
    T_AMBER = "\033[93m"
    T_RED   = "\033[91m"
    T_MAG   = "\033[95m"
    T_GRAY  = "\033[90m"
    T_WHITE = "\033[97m"
    T_BG    = "\033[40m"

    STATUS_COLORS = {
        "done": "\033[92m", "in-progress": "\033[96m", "active": "\033[96m",
        "planned": "\033[93m", "blocked": "\033[91m",
    }

    def __init__(self):
        self.cfg = load_config()
        self.running = True
        self.poll_interval = self.cfg.get("poll_interval", 1000) / 1000.0
        self.port = self.cfg.get("port", 3000)
        self.spark_width = self.cfg.get("sparkline_width", 50)
        self.cpu_hist = deque(maxlen=self.spark_width)
        self.ram_hist = deque(maxlen=self.spark_width)
        self.net_hist = deque(maxlen=self.spark_width)
        self.prev_net = None
        self.prev_net_time = None
        self.last_stats = None
        self.last_tasks = None
        self.last_online = False
        self.log_lines = []
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
        # Force UTF-8 output so box-drawing chars and sparklines work
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    def _clear(self):
        print("\033[2J\033[H", end="")

    def _get_term_width(self):
        try:
            return os.get_terminal_size().columns
        except Exception:
            return 80

    def _log(self, msg, color=""):
        self.log_lines.append((msg, color))
        if len(self.log_lines) > 8:
            self.log_lines = self.log_lines[-8:]

    def _hline(self, w):
        return f"{self.T_GRAY}{chr(0x2500) * w}{self.RST}"

    def _render(self):
        self._clear()
        w = min(self._get_term_width(), 90)
        C, G, A, R, D, M, W, RST = (self.T_CYAN, self.T_GREEN, self.T_AMBER,
                                      self.T_RED, self.DIM, self.T_MAG, self.T_WHITE, self.RST)

        # Header
        print(f"{C}{self.BOLD}  KAPPA COMPUTER SYSTEMS{RST}")
        print(f"{D}  DEPLOYMENT MANAGER // v2.1 \u2014 TERMINAL MODE{RST}")
        print(self._hline(w))

        # Status bar
        online = self.last_online
        s = self.last_stats
        app_tag = f"{G}\u25cf ONLINE{RST}" if online else f"{R}\u25cf OFFLINE{RST}"
        cpu_tag = f"{s['cpu']}" if s else "\u2014"
        mem_tag = f"{s['mem']}" if s else "\u2014"
        net_tag = f"{s['net_str']}" if s else "\u2014"
        pids_tag = f"{s['pids']}" if s else "\u2014"
        tasks_n = len(self.last_tasks) if self.last_tasks else "\u2014"
        print(f"  APP {app_tag}  {D}|{RST}  CPU {A}{cpu_tag}{RST}  {D}|{RST}  RAM {C}{mem_tag}{RST}  {D}|{RST}  NET {M}{net_tag}{RST}  {D}|{RST}  PID {W}{pids_tag}{RST}  {D}|{RST}  TASKS {G}{tasks_n}{RST}")
        print(self._hline(w))

        # Task list
        tasks = self.last_tasks or []
        print(f"  {C}{self.BOLD}TASKS{RST}")
        if tasks:
            print(f"  {D}{'ID':<5} {'STATUS':<14} {'TITLE':<50}{RST}")
            for t in tasks[:15]:
                tid = str(t.get("id", ""))[:4]
                status = t.get("status", "unknown")
                title = t.get("title", "\u2014")[:48]
                sc = self.STATUS_COLORS.get(status, D)
                print(f"  {D}{tid:<5}{RST} {sc}{status:<14}{RST} {W}{title}{RST}")
            if len(tasks) > 15:
                print(f"  {D}... and {len(tasks) - 15} more{RST}")
        else:
            print(f"  {D}(no tasks / app offline){RST}")
        print(self._hline(w))

        # Sparklines
        sw = min(self.spark_width, w - 16)
        cpu_spark = sparkline_str(self.cpu_hist, sw)
        ram_spark = sparkline_str(self.ram_hist, sw)
        net_spark = sparkline_str(self.net_hist, sw)
        cpu_val = f"{self.last_stats['cpu_pct']:5.1f}%" if self.last_stats else "  \u2014  "
        ram_val = f"{self.last_stats['mem_pct']:5.1f}%" if self.last_stats else "  \u2014  "
        if self.net_hist:
            net_val = format_rate(self.net_hist[-1] / 100 * 1024**2)
        else:
            net_val = "  \u2014  "
        print(f"  {A}CPU {cpu_val}{RST}  {G}{cpu_spark}{RST}")
        print(f"  {C}RAM {ram_val}{RST}  {C}{ram_spark}{RST}")
        print(f"  {M}NET {net_val:>7}{RST}  {M}{net_spark}{RST}")
        print(self._hline(w))

        # Log
        if self.log_lines:
            for msg, color in self.log_lines[-6:]:
                c = color or D
                safe = msg.encode("ascii", "replace").decode()
                print(f"  {c}{safe}{RST}")
            print(self._hline(w))

        # Destruction line
        last_d = read_last_destruction()
        if last_d:
            print(f"  {R}{D}LAST DESTRUCTION: {last_d}{RST}")
            print(self._hline(w))

        # Menu
        print(f"  {W}{self.BOLD}COMMANDS:{RST}  "
              f"{G}[D]{RST}eploy  "
              f"{G}[E]{RST}xport  "
              f"{A}[C]{RST}lose Shop  "
              f"{R}[X]{RST} Destroy  "
              f"{C}[S]{RST}tatus  "
              f"{D}[Q]{RST}uit")
        print(f"  {D}> ", end="", flush=True)

    def _poll(self):
        stats = docker_stats()
        self.last_stats = stats
        self.last_online = check_app_online(self.port)
        if stats:
            self.cpu_hist.append(stats["cpu_pct"])
            self.ram_hist.append(stats["mem_pct"])
            now = __import__("time").time()
            net_total = stats["net_rx"] + stats["net_tx"]
            if self.prev_net is not None and self.prev_net_time is not None:
                dt = now - self.prev_net_time
                if dt > 0:
                    rate = (net_total - self.prev_net) / dt
                    rate_pct = min(rate / (1024**2) * 100, 100)
                    self.net_hist.append(max(rate_pct, 0))
            self.prev_net = net_total
            self.prev_net_time = now

    def _poll_tasks(self):
        tasks = fetch_tasks(self.port)
        if tasks is not None:
            self.last_tasks = tasks

    def _get_key(self, timeout=None):
        if sys.platform == "win32":
            import msvcrt
            import time as _time
            deadline = _time.time() + (timeout or self.poll_interval)
            while _time.time() < deadline:
                if msvcrt.kbhit():
                    return msvcrt.getwch()
                _time.sleep(0.05)
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
        print(self.RST, end="", flush=True)
        return input(prompt)

    def _confirm(self, msg):
        resp = self._input(f"\n  {self.T_AMBER}{msg} [y/N]: {self.RST}")
        return resp.strip().lower() in ("y", "yes", "yeah son")

    def _do_deploy(self):
        cwd = find_installed() or get_project_dir()
        backups = list_backups()
        if not backups:
            self._log("[DEPLOY] No backups found.", self.T_RED)
            return
        print(f"\n  {self.T_CYAN}Available backups:{self.RST}")
        for i, b in enumerate(backups[:5]):
            dt = b["mtime"].strftime("%Y-%m-%d %I:%M %p")
            sz = f"{b['size']/1024:.0f}KB"
            tag = " (embedded)" if b.get("embedded") else ""
            print(f"    {self.T_GREEN}[{i+1}]{self.RST} {dt}  {sz}{tag}")
        choice = self._input(f"\n  Select backup [1-{min(len(backups),5)}] or Enter to cancel: ")
        try:
            idx = int(choice.strip()) - 1
            if idx < 0 or idx >= min(len(backups), 5):
                return
        except (ValueError, IndexError):
            return
        dest_choice = self._input(f"  Deploy to [{self.T_GREEN}H{self.RST}]ere ({cwd.name}) or enter a path: ")
        dest = cwd if not dest_choice.strip() or dest_choice.strip().lower() == "h" else Path(dest_choice.strip())
        self._log(f"[DEPLOY] Extracting to {dest} ...", self.T_AMBER)
        extract_from_pyz(backups[idx]["path"], dest, lambda m, c=None: self._log(m, c or self.T_GREEN))
        if not check_docker_installed():
            self._log("[DOCKER] Docker not installed.", self.T_RED)
            if self._confirm("Attempt to install Docker?"):
                install_docker(lambda m, c=None: self._log(m, c or self.T_AMBER))
            return
        docker_up(dest, lambda m, c=None: self._log(m, c or self.T_GREEN), self.cfg)

    def _do_export(self):
        cwd = find_installed() or get_project_dir()
        export_snapshot(cwd, lambda m, c=None: self._log(m, c or self.T_GREEN))

    def _do_close_shop(self):
        if not self._confirm("Close shop \u2014 stop app and create portable .pyz?"):
            return
        cwd = find_installed() or get_project_dir()
        export_snapshot(cwd, lambda m, c=None: self._log(m, c or self.T_GREEN))
        docker_down(cwd, lambda m, c=None: self._log(m, c or self.T_AMBER))
        create_pyz(cwd, lambda m, c=None: self._log(m, c or self.T_GREEN))

    def _do_destroy(self):
        print(f"\n  {self.T_RED}{self.BOLD}  U FO REAL!?{self.RST}")
        resp = self._input(f"  Type '{self.T_RED}yeah son{self.RST}' to confirm: ")
        if resp.strip().lower() != "yeah son":
            self._log("[DESTROY] Cancelled \u2014 hell naw.", self.T_AMBER)
            return
        cwd = find_installed() or get_project_dir()
        who = get_username()
        ts = datetime.now().strftime("%d-%m-%Y at %I-%M-%S %p")
        src = cwd / DB_FILE
        if src.exists():
            fname = f"{who} destroyed everything on {ts}.json"
            shutil.copy2(src, get_backups_dir() / fname)
            self._log(f"[DB] Safety export -> {fname}", self.T_GREEN)
        docker_down(cwd, lambda m, c=None: self._log(m, c or self.T_AMBER))
        if (cwd / COMPOSE).exists():
            try:
                for item in cwd.iterdir():
                    if item.name in {BACKUPS_DIR, "launcher.py"}:
                        continue
                    if item.is_dir():
                        shutil.rmtree(item)
                    else:
                        item.unlink()
                self._log(f"[RM] Deleted app files in {cwd.name}", self.T_RED)
            except Exception as e:
                self._log(f"[RM] Error: {e}", self.T_RED)
        log_destruction(who, ts)
        self._log("[DESTROY] Done. Backups preserved.", self.T_AMBER)

    def run(self):
        self._log("[*] Terminal mode started. Polling...", self.T_CYAN)
        task_counter = 0
        task_interval = 5
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
                self._log("[*] Refreshed.", self.T_CYAN)
        self._clear()
        print(f"{self.T_CYAN}  Bye.{self.RST}")


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
    if sys.argv[1] == "--pack":
        _cli_pack()
        sys.exit(0)
    elif sys.argv[1] in ("--terminal", "--tui", "-t"):
        TerminalUI().run()
        sys.exit(0)


# ══════════════════════════════════════════════════════════════════════════════
#  GUI (Tkinter) — only reaches here for GUI mode
# ══════════════════════════════════════════════════════════════════════════════
import tkinter as tk
from tkinter import filedialog

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
        b = tk.Button(parent, text=label, font=("Courier New", 11, w), bg=bg_n, fg=color,
                      activebackground=bg_h, activeforeground=color, relief="flat",
                      cursor="hand2", anchor="w", padx=12, pady=5, bd=0, command=cmd)
        b.pack(fill="x", pady=1)
        b.bind("<Enter>", lambda e, _b=b: _b.config(bg=bg_h))
        b.bind("<Leave>", lambda e, _b=b: _b.config(bg=bg_n))
        if tip:
            Tooltip(b, tip)
        return b

    def _log(self, msg, color=None):
        tag_map = {CYAN: "cyan", AMBER: "amber", GREEN: "green",
                   RED: "red", FG_DIM: "dim", FG: "fg"}
        self.log_box.configure(state="normal")
        self.log_box.insert("end", msg.strip() + "\n", tag_map.get(color, "fg"))
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _run_threaded(self, fn):
        threading.Thread(target=fn, daemon=True).start()

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
            if check_docker_installed():
                self._log("[DOCKER] Verified.", GREEN)
                return True
            self._log("[DOCKER] Installed but not in PATH. Restart this app.", AMBER)
            return False
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
        def _go():
            cwd = find_installed()
            if not cwd:
                self._log("[ERROR] No installed app found.", RED)
                return
            export_snapshot(cwd, self._log)
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
