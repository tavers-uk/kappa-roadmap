# KAPPA ROADMAP — Deployment Guide

## What's in the box

```
kappa-roadmap/
├── launcher.py          ← GUI launcher (Python/Tkinter)
├── build.bat            ← Build launcher.exe on Windows
├── build.sh             ← Build launcher binary on Linux
├── server.js            ← Express API + static file server
├── db.js                ← SQLite setup
├── package.json         ← Node.js dependencies
├── Dockerfile
├── docker-compose.yml
├── .env.example         ← Copy to .env and set your password
├── public/
│   └── index.html       ← The viewer frontend (retro dashboard)
└── admin/
    └── index.html       ← Admin CRUD panel (password protected)
```

---

## Quick Start (VS Code / local dev)

```bash
cp .env.example .env
npm install
npm start
# → http://localhost:3000       (viewer)
# → http://localhost:3000/admin  (admin panel, password: kappa2026)
```

---

## Docker Deploy (any platform)

### Linux
```bash
cp .env.example .env
# Edit .env and set ADMIN_PASSWORD
docker compose up -d
```

### Windows (Docker Desktop)
```powershell
copy .env.example .env
# Edit .env and set ADMIN_PASSWORD
docker compose up -d
```

App runs at `http://localhost:3000`
Admin at `http://localhost:3000/admin`

### Change the port
Edit `.env`:
```
PORT=8080
```

---

## Launcher GUI

The launcher provides a one-click deployment manager:

1. **Unpack & Move In Here** — Extracts app to current directory, starts Docker
2. **Unpack & Move In...** — Opens folder picker, then deploys there
3. **Export Live Database** — Snapshots the DB to current directory (safe backup)

### DANGER ZONE
4. **Close Shop, Pack Up, & Move Out** — DB backup → docker down → zip everything
5. **~~ DESTROY EVERYTHING ~~** — Exports DB with timestamped filename, tears down container, deletes app files

### Build the launcher

**Windows:**
```cmd
build.bat
# → dist\KCS-Roadmap-Launcher.exe
```

**Linux:**
```bash
chmod +x build.sh && ./build.sh
# → dist/kcs-roadmap-launcher
```

To run without building: `python3 launcher.py` (requires Python 3.8+ with Tkinter)

---

## Moving / Migrating

Your entire persistent state lives in `data/roadmap.db` — a single SQLite file.

**To migrate to a new host:**
1. Use **"Close Shop, Pack Up"** in the launcher — it creates a `.zip`
2. Copy the `.zip` to the new host
3. Unzip, run `docker compose up -d`
4. Done — all data intact

**Manual backup:**
```bash
cp data/roadmap.db ~/backups/roadmap-$(date +%Y%m%d).db
```

---

## Admin API (for integrations)

All admin routes require the `x-admin-token` header set to your `ADMIN_PASSWORD`.

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | /api/tasks | Get all tasks (public) |
| POST | /api/tasks | Add a task |
| PUT | /api/tasks/:id | Update a task |
| DELETE | /api/tasks/:id | Delete a task |
| POST | /api/seed | Seed initial data |
| POST | /api/auth | Validate admin password |

---

## Security Notes

- The viewer (`/`) is **public** — no auth required — by design for internal network use
- The admin panel (`/admin`) requires the password set in `.env`
- For internet-facing deployment: put nginx in front with basic auth or VPN gate it
- Change `ADMIN_PASSWORD` from the default `kappa2026` immediately

---

## Embedding in Halo PSA

The viewer is a plain HTML page served at `http://[your-host]:3000`. You can embed it in Halo as an iframe using an HTML widget or a custom web tab. No additional configuration needed — the page auto-refreshes every 60 seconds.

---

*KAPPA COMPUTER SYSTEMS LLC // INTERNAL USE ONLY*
