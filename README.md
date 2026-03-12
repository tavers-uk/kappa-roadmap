# KAPPA ROADMAP

Self-hosted internal project roadmap dashboard for Kappa Computer Systems.
Retro-futuristic viewer frontend, password-protected admin panel, portable Docker deployment.

---

## What's in the box

```
kappa-roadmap/
├── launcher.py          ← TUI-styled deployment manager (Python/Tkinter)
├── launch.bat           ← Windows bootstrap (auto-installs Python if missing)
├── launch.sh            ← Linux bootstrap (auto-installs Python if missing)
├── build.bat            ← Create portable .pyz package (Windows)
├── build.sh             ← Create portable .pyz package (Linux)
├── server.js            ← Express API + static file server
├── db.js                ← JSON flat-file storage
├── package.json         ← Node.js dependencies (express + bcryptjs only)
├── Dockerfile
├── docker-compose.yml
├── .env.example
├── public/
│   └── index.html       ← Viewer frontend (retro dashboard)
└── admin/
    └── index.html       ← Admin CRUD panel (password protected)
```

---

## Quick Start (local dev)

```bash
cp .env.example .env
npm install
npm start
# → http://localhost:3000       (viewer)
# → http://localhost:3000/admin  (admin, password: kappa2026)
```

---

## Docker Deploy

```bash
cp .env.example .env
docker compose up -d
```

App: `http://localhost:3000` | Admin: `http://localhost:3000/admin`

---

## Portable Handoff (.pyz)

The entire app packs into a **single `.pyz` file** — launcher + app + data.
Take it to any machine with Python 3 + Docker and deploy.

### Create a package

**From the launcher GUI:** Click "Close Shop & Pack Up"

**From CLI:**
```bash
python launcher.py --pack
```

Output lands in `backups/`:
```
backups/
├── kappa-roadmap-20260312-141530.pyz   ← the portable package
├── launch.bat                           ← Windows bootstrap
└── launch.sh                            ← Linux bootstrap
```

### Deploy on a new machine

Copy the `.pyz` (and optionally `launch.bat`/`launch.sh`) to the target machine.

```bash
python kappa-roadmap-20260312-141530.pyz
```

The launcher opens → select the backup → Deploy → done.

### Zero-install bootstrap

If Python isn't installed on the target, use the bootstrap scripts:

- **Windows:** `launch.bat` — auto-installs Python via winget, then runs the `.pyz`
- **Linux:** `launch.sh` — auto-installs Python via apt/dnf/pacman, then runs the `.pyz`

Docker is auto-installed by the launcher if missing (winget on Windows, get.docker.com on Linux).

---

## Launcher GUI

TUI-styled deployment manager with live monitoring.

| Feature | Description |
|---|---|
| **Status bar** | Live app status, CPU, RAM, task count with sparkline bars |
| **Backup tree** | Last 5 packages with dates/sizes, radio-select |
| **Deploy HERE** | Unpack selected backup to current dir, start Docker |
| **Deploy SOMEWHERE ELSE** | Folder picker, then deploy |
| **Export Live Snapshot** | Copy `roadmap.json` to backups/ |
| **Close Shop & Pack Up** | Snapshot + docker down + create portable `.pyz` |
| **DESTROY EVERYTHING** | "U FO REAL!?" dialog, exports safety backup, full teardown |
| **Destruction audit** | Bottom line shows last destruction event + who did it |

Run: `python launcher.py`

---

## Admin API

All admin routes require `x-admin-token` header set to your `ADMIN_PASSWORD`.

| Method | Endpoint | Description |
|---|---|---|
| GET | /api/tasks | Get all tasks (public) |
| POST | /api/tasks | Add a task |
| PUT | /api/tasks/:id | Update a task |
| DELETE | /api/tasks/:id | Delete a task |
| POST | /api/seed | Seed initial data |
| POST | /api/auth | Validate admin password |

---

## Security Notes

- The viewer (`/`) is **public** — no auth — designed for internal network use
- The admin panel (`/admin`) requires the password set in `.env`
- For internet-facing: put nginx in front with auth or VPN gate it
- Change `ADMIN_PASSWORD` from the default `kappa2026` immediately

---

## Embedding in Halo PSA

The viewer at `http://[your-host]:3000` can be embedded in Halo as an iframe via an HTML widget or custom web tab. Auto-refreshes every 60 seconds.

---

## Credits

| Role | Credit |
|---|---|
| Creative Vision | Shiva |
| Strategic Planning | Chad & Shiva |
| Project Planning | Shiva & Claude Code |
| Coding & Implementation | Claude Code |
| Cross-Platform Design & Engineering | Shiva & Claude Code |
| Deployment | Kappa Computer Systems LLC |

---

*KAPPA COMPUTER SYSTEMS LLC // INTERNAL USE ONLY*
