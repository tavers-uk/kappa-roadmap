"""
KAPPA ROADMAP — Deployment Manager v2.0
Cross-platform (Windows + Linux) — Python 3.8+
Single-file .pyz portable packaging
"""
import tkinter as tk
from tkinter import filedialog
import subprocess, shutil, zipfile, os, sys, json, io, re
from pathlib import Path
from datetime import datetime
import threading, webbrowser, urllib.request

# ── CONSTANTS ─────────────────────────────────────────────────────────────────
APP_NAME       = "kappa-roadmap"
CONTAINER_NAME = "kappa-roadmap"
DB_FILE        = "data/roadmap.json"
COMPOSE        = "docker-compose.yml"
PORT           = 3000
BACKUPS_DIR    = "backups"
DESTRUCTION_LOG = ".destruction-log"
POLL_INTERVAL  = 5000          # ms
MAX_BACKUPS_SHOWN = 5

# ── THEME ─────────────────────────────────────────────────────────────────────
BG        = "#0a0a0a"
BG_PANEL  = "#111111"
BG_DANGER = "#0f0505"
FG        = "#c8c8c8"
FG_DIM    = "#555"
CYAN      = "#00FFE5"
AMBER     = "#FFB800"
GREEN     = "#00FF41"
RED       = "#FF3030"
RED_DIM   = "#7a1a1a"

FONT_MONO   = ("Courier New", 10)
FONT_HEAD   = ("Courier New", 13, "bold")
FONT_BTN    = ("Courier New", 10, "bold")
FONT_TINY   = ("Courier New", 8)
FONT_STATUS = ("Courier New", 9)
FONT_TREE   = ("Courier New", 9)

EXCLUDE_DIRS = {"node_modules", ".git", "backups", "__pycache__", ".claude"}
EXCLUDE_EXTS = {".pyc", ".pyo"}

# ── PATH HELPERS ──────────────────────────────────────────────────────────────

def is_running_from_pyz():
    """Check if we're running inside a .pyz zipapp."""
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
    """Directory where the launcher (or .pyz) lives."""
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
    """C:\\Users\\shiva\\long\\path → C:\\Users\\shi...\\kappa-roadmap"""
    if len(s) <= max_len:
        return s
    keep_start = 14
    keep_end = max_len - keep_start - 3
    if keep_end < 8:
        keep_end = 8
    return s[:keep_start] + "..." + s[-keep_end:]

def find_installed():
    """Find a directory with docker-compose.yml near the launcher."""
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

def docker_up(cwd, log_fn):
    log_fn("[DOCKER] Building & starting container ...", AMBER)
    code, out = run_cmd(["docker", "compose", "up", "-d", "--build"], cwd=cwd)
    log_fn(out.strip() or "(no output)", FG_DIM)
    if code != 0:
        log_fn("[DOCKER] Failed — is Docker running?", RED)
        return False
    log_fn(f"[DOCKER] Live at http://localhost:{PORT}", GREEN)
    webbrowser.open(f"http://localhost:{PORT}")
    return True

def docker_down(cwd, log_fn):
    log_fn("[DOCKER] Stopping container ...", AMBER)
    code, out = run_cmd(["docker", "compose", "down"], cwd=cwd)
    log_fn(out.strip() or "(no output)", FG_DIM)
    return code == 0

def docker_stats():
    """Get container CPU/RAM. Returns dict or None."""
    code, out = run_cmd(
        ["docker", "stats", CONTAINER_NAME, "--no-stream",
         "--format", "{{.CPUPerc}}|{{.MemUsage}}"],
        timeout=8
    )
    if code != 0 or not out.strip():
        return None
    try:
        parts = out.strip().split('|')
        cpu_str = parts[0].strip()
        mem_str = parts[1].strip()
        cpu_pct = float(cpu_str.replace('%', ''))
        mem_parts = mem_str.split('/')
        mem_used = mem_parts[0].strip()
        mem_total = mem_parts[1].strip() if len(mem_parts) > 1 else ""
        mem_val = float(re.search(r'[\d.]+', mem_used).group())
        mem_total_val = float(re.search(r'[\d.]+', mem_total).group()) if mem_total else 512
        mem_pct = (mem_val / mem_total_val) * 100 if mem_total_val > 0 else 0
        return {
            "cpu": cpu_str, "cpu_pct": min(cpu_pct, 100),
            "mem": mem_used, "mem_pct": min(mem_pct, 100),
        }
    except Exception:
        return None

def install_docker(log_fn):
    """Auto-install Docker. Returns True on success."""
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
            ["bash", "-c", "curl -fsSL https://get.docker.com | sh"],
            timeout=300,
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

def check_app_online():
    try:
        urllib.request.urlopen(f"http://localhost:{PORT}/api/tasks", timeout=2)
        return True
    except Exception:
        return False

def get_task_count():
    try:
        resp = urllib.request.urlopen(f"http://localhost:{PORT}/api/tasks", timeout=2)
        return len(json.loads(resp.read()))
    except Exception:
        return None

# ── BACKUP / PACKAGING ────────────────────────────────────────────────────────

def get_launcher_source():
    """Get our own source code for embedding into .pyz files."""
    if is_running_from_pyz():
        with zipfile.ZipFile(str(get_pyz_path()), 'r') as z:
            return z.read('__main__.py')
    return Path(__file__).read_bytes()

def list_backups():
    """Scan backups/ for .pyz files, return sorted newest-first."""
    backups = []
    bdir = get_backups_dir()
    for f in bdir.glob("*.pyz"):
        try:
            st = f.stat()
            backups.append({
                "path": f, "mtime": datetime.fromtimestamp(st.st_mtime),
                "size": st.st_size,
            })
        except Exception:
            pass
    # If running from .pyz and it's not already in backups/
    if is_running_from_pyz():
        pyz = get_pyz_path()
        if not any(b["path"].resolve() == pyz for b in backups):
            try:
                st = pyz.stat()
                backups.append({
                    "path": pyz, "mtime": datetime.fromtimestamp(st.st_mtime),
                    "size": st.st_size, "embedded": True,
                })
            except Exception:
                pass
    backups.sort(key=lambda b: b["mtime"], reverse=True)
    return backups

def create_pyz(src_dir, log_fn):
    """Pack the project into a portable .pyz file in backups/."""
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
            if rel.suffix in EXCLUDE_EXTS:
                continue
            if rel.name == "launcher.py":
                continue
            z.writestr(f"app/{rel.as_posix()}", f.read_bytes())

    with open(dest, "wb") as fh:
        fh.write(b"#!/usr/bin/env python3\n")
        fh.write(buf.getvalue())

    size_mb = dest.stat().st_size / (1024 * 1024)
    log_fn(f"[PACK] Done → {dest.name} ({size_mb:.1f} MB)", GREEN)
    write_bootstrap_scripts(dest.parent, log_fn)
    return dest

def write_bootstrap_scripts(target_dir, log_fn):
    """Generate launch.bat and launch.sh alongside .pyz files."""
    bat = target_dir / "launch.bat"
    bat.write_text(
        '@echo off\r\n'
        'where python >nul 2>&1 && goto :run\r\n'
        'echo [*] Python not found. Installing via winget...\r\n'
        'winget install Python.Python.3.12 --silent --accept-package-agreements '
        '--accept-source-agreements >nul 2>&1\r\n'
        'if errorlevel 1 (\r\n'
        '    echo [!] Auto-install failed. Get Python from https://python.org\r\n'
        '    pause\r\n'
        '    exit /b 1\r\n'
        ')\r\n'
        'echo [*] Python installed. Refreshing PATH...\r\n'
        'set "PATH=%LOCALAPPDATA%\\Programs\\Python\\Python312;'
        '%LOCALAPPDATA%\\Programs\\Python\\Python312\\Scripts;%PATH%"\r\n'
        ':run\r\n'
        'for %%f in ("%~dp0kappa-roadmap-*.pyz") do set "PYZ=%%f"\r\n'
        'if not defined PYZ (\r\n'
        '    echo [!] No .pyz file found next to this script.\r\n'
        '    pause\r\n'
        '    exit /b 1\r\n'
        ')\r\n'
        'echo [*] Launching %PYZ%...\r\n'
        'python "%PYZ%"\r\n',
        encoding="utf-8",
    )
    sh = target_dir / "launch.sh"
    sh.write_text(
        '#!/usr/bin/env bash\n'
        'set -e\n'
        'if ! command -v python3 &>/dev/null; then\n'
        '    echo "[*] Python 3 not found. Installing..."\n'
        '    if command -v apt-get &>/dev/null; then\n'
        '        sudo apt-get update -qq && sudo apt-get install -y python3 python3-tk\n'
        '    elif command -v dnf &>/dev/null; then\n'
        '        sudo dnf install -y python3 python3-tkinter\n'
        '    elif command -v pacman &>/dev/null; then\n'
        '        sudo pacman -Sy --noconfirm python tk\n'
        '    else\n'
        '        echo "[!] Could not auto-install. Please install Python 3 manually."\n'
        '        exit 1\n'
        '    fi\n'
        'fi\n'
        'DIR="$(cd "$(dirname "$0")" && pwd)"\n'
        'PYZ=$(ls -1t "$DIR"/kappa-roadmap-*.pyz 2>/dev/null | head -1)\n'
        '[ -z "$PYZ" ] && { echo "[!] No .pyz file found."; exit 1; }\n'
        'echo "[*] Launching $PYZ..."\n'
        'python3 "$PYZ"\n',
        encoding="utf-8",
    )
    log_fn("[PACK] Bootstrap scripts: launch.bat + launch.sh", GREEN)

def extract_from_pyz(pyz_path, target, log_fn):
    """Extract app/ contents from a .pyz to target directory."""
    log_fn(f"[UNPACK] → {target}", CYAN)
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(str(pyz_path), 'r') as z:
        for info in z.infolist():
            if not info.filename.startswith("app/") or len(info.filename) <= 4:
                continue
            rel = info.filename[4:]          # strip "app/"
            dest_path = target / rel
            if info.filename.endswith('/'):
                dest_path.mkdir(parents=True, exist_ok=True)
            else:
                dest_path.parent.mkdir(parents=True, exist_ok=True)
                with z.open(info) as src_f, open(dest_path, 'wb') as dst_f:
                    dst_f.write(src_f.read())
    log_fn("[UNPACK] Done.", GREEN)
    return True

def export_snapshot(cwd, log_fn):
    """Copy roadmap.json to backups/ as a timestamped snapshot."""
    src = cwd / DB_FILE
    if not src.exists():
        log_fn("[DB] No database found — skipping snapshot.", FG_DIM)
        return None
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = get_backups_dir() / f"snapshot-{ts}.json"
    shutil.copy2(src, dest)
    log_fn(f"[DB] Snapshot → {dest.name}", GREEN)
    return dest

# ── DESTRUCTION LOG ───────────────────────────────────────────────────────────

def log_destruction(username, timestamp):
    log_file = get_backups_dir() / DESTRUCTION_LOG
    with open(log_file, 'a') as f:
        f.write(f"{username} // {timestamp}\n")

def read_last_destruction():
    log_file = get_backups_dir() / DESTRUCTION_LOG
    if not log_file.exists():
        return None
    try:
        lines = [l.strip() for l in log_file.read_text().splitlines() if l.strip()]
        return lines[-1] if lines else None
    except Exception:
        return None

# ══════════════════════════════════════════════════════════════════════════════
#  UI COMPONENTS
# ══════════════════════════════════════════════════════════════════════════════

class Tooltip:
    """Hover tooltip for any widget."""
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
        tk.Label(inner, text=self.text, font=FONT_TINY, bg=BG_PANEL,
                 fg=FG, justify="left", wraplength=280).pack()

    def _hide(self, _=None):
        if self.tw:
            self.tw.destroy()
            self.tw = None


class StatusBar(tk.Frame):
    """Sparkline-style status indicators: APP │ CPU │ RAM │ TASKS"""
    def __init__(self, parent):
        super().__init__(parent, bg=BG_PANEL, padx=12, pady=5)

        # APP
        self._app_dot = tk.Label(self, text="●", font=FONT_STATUS, bg=BG_PANEL, fg=FG_DIM)
        self._app_dot.pack(side="left")
        tk.Label(self, text=" APP ", font=FONT_TINY, bg=BG_PANEL, fg=FG_DIM).pack(side="left")
        self._app_val = tk.Label(self, text="--", font=FONT_STATUS, bg=BG_PANEL, fg=FG_DIM)
        self._app_val.pack(side="left")
        self._sep()

        # CPU
        tk.Label(self, text="CPU ", font=FONT_TINY, bg=BG_PANEL, fg=FG_DIM).pack(side="left")
        self._cpu_bar = tk.Canvas(self, width=32, height=10, bg="#1a1a1a",
                                  highlightthickness=0, bd=0)
        self._cpu_bar.pack(side="left", padx=(0, 3))
        self._cpu_val = tk.Label(self, text="--", font=FONT_TINY, bg=BG_PANEL, fg=FG_DIM)
        self._cpu_val.pack(side="left")
        self._sep()

        # RAM
        tk.Label(self, text="RAM ", font=FONT_TINY, bg=BG_PANEL, fg=FG_DIM).pack(side="left")
        self._ram_bar = tk.Canvas(self, width=32, height=10, bg="#1a1a1a",
                                  highlightthickness=0, bd=0)
        self._ram_bar.pack(side="left", padx=(0, 3))
        self._ram_val = tk.Label(self, text="--", font=FONT_TINY, bg=BG_PANEL, fg=FG_DIM)
        self._ram_val.pack(side="left")
        self._sep()

        # TASKS
        tk.Label(self, text="TASKS ", font=FONT_TINY, bg=BG_PANEL, fg=FG_DIM).pack(side="left")
        self._tasks_val = tk.Label(self, text="--", font=FONT_STATUS, bg=BG_PANEL, fg=FG_DIM)
        self._tasks_val.pack(side="left")

    def _sep(self):
        tk.Label(self, text="│", font=FONT_TINY, bg=BG_PANEL, fg="#333").pack(side="left", padx=8)

    @staticmethod
    def _draw_bar(canvas, pct, color):
        canvas.delete("all")
        w = max(canvas.winfo_width(), 32)
        h = max(canvas.winfo_height(), 10)
        bw = max(int((pct / 100) * w), 0)
        if bw > 0:
            canvas.create_rectangle(0, 0, bw, h, fill=color, outline="")

    def update(self, online, stats, task_count):
        # APP
        if online:
            self._app_dot.config(fg=GREEN)
            self._app_val.config(text="ONLINE", fg=GREEN)
        else:
            self._app_dot.config(fg=RED_DIM)
            self._app_val.config(text="OFFLINE", fg=RED_DIM)
        # CPU / RAM
        if stats:
            self._cpu_val.config(text=stats["cpu"], fg=CYAN)
            self._draw_bar(self._cpu_bar, stats["cpu_pct"], CYAN)
            self._ram_val.config(text=stats["mem"], fg=AMBER)
            self._draw_bar(self._ram_bar, stats["mem_pct"], AMBER)
        else:
            self._cpu_val.config(text="--", fg=FG_DIM)
            self._cpu_bar.delete("all")
            self._ram_val.config(text="--", fg=FG_DIM)
            self._ram_bar.delete("all")
        # TASKS
        if task_count is not None:
            self._tasks_val.config(text=str(task_count), fg=GREEN)
        else:
            self._tasks_val.config(text="--", fg=FG_DIM)


class BackupTree(tk.Frame):
    """TUI-style collapsible backup list with radio selection."""
    def __init__(self, parent, on_change=None):
        super().__init__(parent, bg=BG)
        self._expanded = True
        self._on_change = on_change
        self._selected = tk.IntVar(value=0)
        self._backups = []

        self._header = tk.Label(self, text="▼ BACKUPS (0 available)",
                                font=FONT_TREE, bg=BG, fg=CYAN,
                                cursor="hand2", anchor="w")
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
        n = len(self._backups)
        self._header.config(text=f"{arrow} BACKUPS ({n} available)")

    def refresh(self):
        self._backups = list_backups()
        self._selected.set(0)
        for w in self._container.winfo_children():
            w.destroy()

        if not self._backups:
            tk.Label(self._container, text="  (no backups found)",
                     font=FONT_TINY, bg=BG, fg=FG_DIM).pack(anchor="w")
            self._update_header()
            return

        shown = self._backups[:MAX_BACKUPS_SHOWN]
        for i, b in enumerate(shown):
            dt_str = b["mtime"].strftime("%Y-%m-%d  %I:%M %p")
            size   = f"{b['size'] / (1024*1024):.1f} MB"
            marker = "●" if i == 0 else "○"
            suffix = "  ← latest" if i == 0 else ""
            embedded = "  (this package)" if b.get("embedded") else ""
            text = f"  {marker} {dt_str}   {size}{suffix}{embedded}"

            rb = tk.Radiobutton(
                self._container, text=text, variable=self._selected,
                value=i, font=FONT_TINY, bg=BG,
                fg=FG if i == 0 else FG_DIM,
                selectcolor=BG_PANEL, activebackground=BG,
                activeforeground=CYAN, indicatoron=0,
                relief="flat", bd=0, anchor="w",
                padx=4, pady=1, cursor="hand2",
                command=self._on_select,
            )
            rb.pack(fill="x")

        extra = len(self._backups) - MAX_BACKUPS_SHOWN
        if extra > 0:
            tk.Label(self._container, text=f"     ... +{extra} more in backups/",
                     font=FONT_TINY, bg=BG, fg=FG_DIM).pack(anchor="w")
        self._update_header()

    def _on_select(self):
        if self._on_change:
            self._on_change()

    def get_selected(self):
        idx = self._selected.get()
        if 0 <= idx < len(self._backups):
            return self._backups[idx]["path"]
        return None

    def has_backups(self):
        return len(self._backups) > 0


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN APPLICATION
# ══════════════════════════════════════════════════════════════════════════════

class KappaLauncher(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("KAPPA ROADMAP // DEPLOYMENT MANAGER")
        self.configure(bg=BG)
        self.resizable(False, False)
        self._polling = True
        self._status_busy = False
        self._build_ui()
        self.eval('tk::PlaceWindow . center')
        self._poll_status()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _on_close(self):
        self._polling = False
        self.destroy()

    # ── BUILD UI ──────────────────────────────────────────────────────────────
    def _build_ui(self):
        self._build_header()
        self._build_status_bar()
        self._build_backup_tree()
        self._build_deploy_section()
        self._build_export_section()
        self._build_danger_zone()
        self._build_log_box()
        self._build_destruction_line()

    def _build_header(self):
        tk.Frame(self, bg=CYAN, height=2).pack(fill="x")
        hdr = tk.Frame(self, bg=BG, pady=8)
        hdr.pack(fill="x", padx=20)
        tk.Label(hdr, text="KAPPA COMPUTER SYSTEMS",
                 font=FONT_HEAD, bg=BG, fg=CYAN).pack(anchor="w")
        tk.Label(hdr, text="DEPLOYMENT MANAGER  //  v2.0",
                 font=FONT_TINY, bg=BG, fg=FG_DIM).pack(anchor="w")
        tk.Frame(self, bg=CYAN, height=1).pack(fill="x", padx=20, pady=(0, 4))

    def _build_status_bar(self):
        outer = tk.Frame(self, bg="#1a1a1a", padx=1, pady=1)
        outer.pack(fill="x", padx=20, pady=(0, 4))
        self._status_bar = StatusBar(outer)
        self._status_bar.pack(fill="x")

    def _build_backup_tree(self):
        tk.Frame(self, bg="#1a1a1a", height=1).pack(fill="x", padx=20)
        self._backup_tree = BackupTree(self)
        self._backup_tree.pack(fill="x", padx=20, pady=(4, 4))
        tk.Frame(self, bg="#1a1a1a", height=1).pack(fill="x", padx=20)

    def _build_deploy_section(self):
        frm = tk.Frame(self, bg=BG)
        frm.pack(fill="x", padx=20, pady=(4, 2))

        trunc = truncate_path(str(get_project_dir()), 36)

        self._btn_deploy_here = self._make_btn(
            frm, f"DEPLOY >>>  [ RIGHT HERE ({trunc}) ]",
            CYAN, self._do_deploy_here,
            tip="Deploys the selected backup to the current\n"
                "directory and starts the Docker container.",
        )
        self._make_btn(
            frm, "DEPLOY >>>  [ SOMEWHERE ELSE... ]",
            CYAN, self._do_deploy_pick,
            tip="Opens a folder picker, deploys the selected\n"
                "backup there, and starts the Docker container.",
        )

    def _build_export_section(self):
        tk.Frame(self, bg="#1a1a1a", height=1).pack(fill="x", padx=20, pady=(4, 0))
        frm = tk.Frame(self, bg=BG)
        frm.pack(fill="x", padx=20, pady=(4, 2))
        self._make_btn(
            frm, "◎  Export Live Snapshot", GREEN, self._do_export,
            tip="Exports a snapshot of the live database\nto the backups directory.",
        )

    def _build_danger_zone(self):
        tk.Frame(self, bg="#222", height=1).pack(fill="x", padx=20, pady=(8, 0))
        tk.Label(self, text="⚠  DANGER ZONE", font=FONT_TINY,
                 bg=BG, fg=RED).pack(anchor="w", padx=20)

        outer = tk.Frame(self, bg=RED_DIM, padx=1, pady=1)
        outer.pack(fill="x", padx=20, pady=(2, 8))
        inner = tk.Frame(outer, bg=BG_DANGER, padx=10, pady=8)
        inner.pack(fill="x")

        self._make_btn(
            inner, "✖  Close Shop & Pack Up", RED, self._do_close_shop,
            danger=True,
            tip="Exports the database, stops Docker, and packs\n"
                "everything into a portable .pyz file.",
        )
        tk.Frame(inner, bg="#3a0a0a", height=1).pack(fill="x", pady=(6, 0))
        self._make_btn(
            inner, "~~ DESTROY EVERYTHING ~~", RED, self._do_destroy,
            danger=True, bold=True,
            tip="DANGER: Exports database, tears down container,\n"
                "and deletes all app files. Cannot be undone.",
        )

    def _build_log_box(self):
        tk.Frame(self, bg="#222", height=1).pack(fill="x", padx=20)
        self.log_box = tk.Text(
            self, height=10, bg="#050505", fg=FG_DIM, font=FONT_TINY,
            relief="flat", insertbackground=CYAN, state="disabled",
            padx=6, pady=6, wrap="word",
        )
        self.log_box.pack(fill="x", padx=20)
        for tag, color in [("cyan", CYAN), ("amber", AMBER), ("green", GREEN),
                           ("red", RED), ("dim", FG_DIM), ("fg", FG)]:
            self.log_box.tag_config(tag, foreground=color)

        # Boot messages
        if not check_docker_installed():
            self._log("[WARN] Docker not found — will auto-install on first deploy.", AMBER)
        else:
            self._log("System ready. Docker detected.", GREEN)
        if is_running_from_pyz():
            self._log(f"[PYZ] Running from: {get_pyz_path().name}", CYAN)

    def _build_destruction_line(self):
        tk.Frame(self, bg=RED_DIM, height=1).pack(fill="x", padx=20, pady=(4, 0))
        last = read_last_destruction()
        text  = f"LAST DESTRUCTION: {last}" if last else "LAST DESTRUCTION: none on record"
        color = RED_DIM if last else FG_DIM
        self._dest_lbl = tk.Label(self, text=text, font=FONT_TINY,
                                  bg=BG, fg=color, anchor="w", padx=4)
        self._dest_lbl.pack(fill="x", padx=20, pady=(2, 8))

    # ── HELPERS ───────────────────────────────────────────────────────────────
    def _make_btn(self, parent, label, color, cmd, *, danger=False, bold=False, tip=None):
        weight = "bold" if bold else "normal"
        bg_n   = BG_DANGER if danger else BG
        bg_h   = "#1a0808" if danger else "#1c1c1c"
        b = tk.Button(
            parent, text=label, font=("Courier New", 10, weight),
            bg=bg_n, fg=color, activebackground=bg_h,
            activeforeground=color, relief="flat", cursor="hand2",
            anchor="w", padx=12, pady=6, bd=0, command=cmd,
        )
        b.pack(fill="x", pady=1)
        b.bind("<Enter>", lambda e, w=b: w.config(bg=bg_h))
        b.bind("<Leave>", lambda e, w=b: w.config(bg=bg_n))
        if tip:
            Tooltip(b, tip)
        return b

    def _log(self, msg, color=None):
        tag_map = {CYAN: "cyan", AMBER: "amber", GREEN: "green",
                   RED: "red", FG_DIM: "dim", FG: "fg"}
        tag = tag_map.get(color, "fg")
        self.log_box.configure(state="normal")
        self.log_box.insert("end", msg.strip() + "\n", tag)
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _run_threaded(self, fn):
        threading.Thread(target=fn, daemon=True).start()

    # ── POLLING ───────────────────────────────────────────────────────────────
    def _poll_status(self):
        if not self._polling:
            return
        if not self._status_busy:
            self._status_busy = True
            threading.Thread(target=self._fetch_status, daemon=True).start()
        self.after(POLL_INTERVAL, self._poll_status)

    def _fetch_status(self):
        try:
            online = check_app_online()
            stats  = docker_stats()
            count  = get_task_count() if online else None
            if self._polling:
                self.after(0, self._status_bar.update, online, stats, count)
        finally:
            self._status_busy = False

    # ── ACTIONS ───────────────────────────────────────────────────────────────
    def _ensure_docker(self):
        """Check for Docker; auto-install if missing. Returns True if ready."""
        if check_docker_installed():
            return True
        self._log("[DOCKER] Not installed. Attempting auto-install ...", AMBER)
        if install_docker(self._log):
            # Verify it actually works now
            if check_docker_installed():
                self._log("[DOCKER] Verified — ready to go.", GREEN)
                return True
            self._log("[DOCKER] Installed but not yet in PATH. Restart this app.", AMBER)
            return False
        return False

    def _do_deploy_here(self):
        def _go():
            sel = self._backup_tree.get_selected()
            if not sel:
                self._log("[ERROR] No backup selected. Use 'Close Shop' to create one.", RED)
                return
            if not self._ensure_docker():
                return
            target = find_installed() or get_project_dir() / APP_NAME
            extract_from_pyz(sel, target, self._log)
            docker_up(target, self._log)
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
            docker_up(target, self._log)
        self._run_threaded(_go)

    def _do_export(self):
        def _go():
            cwd = find_installed()
            if not cwd:
                self._log("[ERROR] No installed app found to snapshot.", RED)
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
            self._log("[DONE] Packed and ready to move. Check backups/", GREEN)
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
        tk.Label(dlg, text="U FO REAL!?", font=("Courier New", 18, "bold"),
                 bg=BG, fg=RED).pack(pady=(16, 4))
        tk.Label(dlg, text=(
            "This will export your database, tear down\n"
            "the container, and delete all app files.\n"
            "This cannot be undone."
        ), font=FONT_TINY, bg=BG, fg=FG, justify="center").pack(pady=(0, 16))
        tk.Frame(dlg, bg="#222", height=1).pack(fill="x", padx=20)

        row = tk.Frame(dlg, bg=BG)
        row.pack(pady=14, padx=20, fill="x")

        def _yeah():
            dlg.destroy()
            self._run_threaded(self._destroy_for_real)

        tk.Button(row, text="yeah son", font=FONT_BTN,
                  bg=RED_DIM, fg=RED, activebackground="#2a0000",
                  activeforeground=RED, relief="flat", padx=20, pady=8,
                  cursor="hand2", command=_yeah
                  ).pack(side="left", expand=True, fill="x", padx=(0, 6))
        tk.Button(row, text="hell naw", font=FONT_BTN,
                  bg=BG_PANEL, fg=GREEN, activebackground="#0a1a0a",
                  activeforeground=GREEN, relief="flat", padx=20, pady=8,
                  cursor="hand2", command=dlg.destroy
                  ).pack(side="right", expand=True, fill="x")

    def _destroy_for_real(self):
        cwd = find_installed() or get_project_dir()
        self._log("[DESTROY] Initiating destruction sequence ...", RED)

        # 1. Export DB — safety net
        who = get_username()
        ts  = datetime.now().strftime("%d-%m-%Y at %I-%M-%S %p")
        src = cwd / DB_FILE
        if src.exists():
            fname = f"{who} destroyed everything on {ts}.json"
            dest  = get_backups_dir() / fname
            shutil.copy2(src, dest)
            self._log(f"[DB] Safety export → {dest.name}", GREEN)
        else:
            self._log("[DB] No database found — nothing to export.", FG_DIM)

        # 2. Docker down
        docker_down(cwd, self._log)

        # 3. Delete app files — preserve backups/ and launcher.py
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

        # 4. Log destruction event
        log_destruction(who, ts)

        # 5. Update bottom line
        self.after(0, self._refresh_destruction_line)
        self._log("[DESTROY] Done. Backups preserved in backups/", AMBER)

    def _refresh_destruction_line(self):
        last = read_last_destruction()
        if last:
            self._dest_lbl.config(text=f"LAST DESTRUCTION: {last}", fg=RED_DIM)


# ══════════════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

def _cli_pack():
    """CLI mode: create a .pyz from the current project directory."""
    def log_print(msg, _color=None):
        print(msg.encode("ascii", "replace").decode())
    src = Path(__file__).resolve().parent
    if not (src / COMPOSE).exists():
        print(f"[ERROR] No {COMPOSE} found in {src}")
        sys.exit(1)
    pyz = create_pyz(src, log_print)
    print(f"\nReady: {pyz}")
    print(f"Usage: python {pyz.name}")

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--pack":
        _cli_pack()
    else:
        app = KappaLauncher()
        app.mainloop()
