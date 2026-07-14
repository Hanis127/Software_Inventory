# Software Inventory & Patch Manager

A lightweight Windows software inventory and patch management tool built with Flask, PostgreSQL, and a Windows agent. The server provides a web dashboard for monitoring installed software across machines, dispatching Chocolatey-based install/upgrade/uninstall jobs, running ad-hoc remote commands, pushing user notifications and scheduled restarts, and self-updating the agent fleet. The agent runs as a Windows service on each managed machine, collects inventory, executes jobs posted by the dashboard, and shows notification popups to the logged-in user.

---

## Architecture

```
┌──────────────────────────────────────────────────┐
│                  Web Dashboard                   │
│         (Flask + Waitress + PostgreSQL)          │
│                                                  │
│  • Inventory view + Chocolatey version tracking  │
│  • Inventory Actions (bulk select + upgrade)     │
│  • Deploy (install packages, community/internal) │
│  • Notifications & scheduled restarts            │
│  • Remote commands (cmd / PowerShell /           │
│    MSI uninstall / MSI GUID search)              │
│  • Agent fleet management + self-update push     │
│  • Job history, user management                  │
│  • Session auth; served behind IIS +             │
│    URL Rewrite (dev: HTTPS self-signed cert)     │
└────────────────┬─────────────────────────────────┘
                 │ HTTPS + per-agent token
                 │
┌────────────────▼─────────────────────────────────┐
│                  Windows Agent                   │
│        (compiled exe, run via NSSM service)      │
│                                                  │
│  • Self-enrolls, auto-rotates token (30 days)    │
│  • Polls for pending jobs every 60s              │
│  • Polls for notifications/restarts every 60s    │
│  • Collects registry + Chocolatey software       │
│  • Reports inventory every 10 min                │
│  • Executes install/upgrade/uninstall,           │
│    remote commands, and self-update jobs         │
│  • Shows popups in the user's desktop session    │
└──────────────────────────────────────────────────┘
```
**Server:** Flask (Python) behind Waitress (and, in production, IIS with URL Rewrite in front of it), PostgreSQL backend. `app.py` can also be run standalone with a self-signed TLS cert via `generate_cert.py` for direct HTTPS testing.
**Agent:** Compiled Python (PyInstaller), installed as a Windows service via NSSM, communicates over HTTPS with certificate pinning and a per-agent token. Internally referred to as `DMCPatchAgent`.

---


## Components

| File | Description |
|------|-------------|
| `agent.py` | Windows agent — enrollment, inventory collection, job execution, notification polling, self-update trigger |
| `app.py` | Flask app entrypoint — registers blueprints, session config, runs Waitress |
| `auth.py` | Session auth (login/users/passwords) + per-agent token enrollment, verification, rotation, and revocation |
| `db.py` | PostgreSQL connection pool (`psycopg2`) and the shared `query()` helper |
| `routes/inventory.py` | `/api/inventory` ingestion + Chocolatey "latest version" lookup (internal share/feed with public fallback) |
| `routes/jobs.py` | Job creation (single/bulk with staggered scheduling), listing, cancel/retry/delete, agent polling + result reporting |
| `routes/computers.py` | Computer listing (with online/offline status) and deletion |
| `routes/packages.py` | Managed package catalog (internal Chocolatey packages shown in the Deploy tab) |
| `routes/config.py` | Server-side key/value config (e.g. `internal_source_url`) |
| `routes/notifications.py` | Notification and scheduled-restart job creation, agent delivery polling, delay/acknowledge handling |
| `routes/agent_update.py` | Serves the latest agent build for self-update, reports configured agent version |
| `updater.py` | Standalone helper exe: stops the service, swaps in the newly downloaded agent build, restarts the service |
| `notify_popup.py` | Tkinter popup shown in the user's desktop session for notifications/scheduled restarts |
| `create_first_user.py` | One-time script to create the first dashboard admin user |
| `generate_cert.py` | Generates a self-signed SSL certificate (for direct HTTPS / cert pinning) |
| `templates/dashboard.html` | Web UI — Dashboard (inventory + Inventory Actions), Packages, Deploy, Notifications, Jobs, Settings/Users |
| `installer.iss.example` | Inno Setup installer template for the Windows agent |

---

## Requirements

### Server
- Python 3.10+
- PostgreSQL 13+
- The packages listed in `requirements.txt` (Flask, psycopg2-binary, requests, python-dotenv, cryptography, pywin32, waitress, gunicorn, gevent)
- In production: IIS with URL Rewrite in front of Waitress

### Agent (each managed Windows machine)
- Tested on Windows 11 / Windows Server 2019
- [Chocolatey](https://chocolatey.org/install) installed
- [NSSM](https://nssm.cc/) (included in deployment package)
- Compiled agent exe + `server_cert.pem` deployed to the install directory (default `C:\ProgramData\<InstallSubDir>`), with `config.json` written by the installer and `agent.key` generated on first enrollment

---

## Server Setup

### 1. Clone and install dependencies
```bash
git clone https://github.com/Hanis127/Software_Inventory.git
cd Software_Inventory
pip install -r requirements.txt
```

### 2. Generate a certificate for agent cert-pinning

```bash
python generate_cert.py
```

This produces `server_cert.pem` and `key.pem`. The cert includes a SubjectAlternativeName for your server IP. `server_cert.pem` is what gets deployed to each agent for certificate pinning — see [Security](#security).

### 3. Configure environment

Create a `.env` file next to `app.py`:

```env
DB_HOST=localhost
DB_NAME=inventory
DB_USER=your_db_user
DB_PASSWORD=your_db_password
DB_PORT=5432
OFFLINE_THRESHOLD_MINUTES=15
FLASK_SECRET_KEY=your-random-secret-key-here
ENROLLMENT_PASSWORD=your-enrollment-password-here
```

- `FLASK_SECRET_KEY` — used to sign browser sessions. Generate with `python -c "import secrets; print(secrets.token_hex(32))"`.
- `ENROLLMENT_PASSWORD` — agents present this password when enrolling for the first time. Set the same value in the installer wizard. Change it anytime to prevent new enrollments without affecting existing agents.

### 4. Database setup

PostgreSQL is required. Run the schema migrations:

```sql
-- Core tables (from initial setup)
-- Then add agent enrollment columns:
ALTER TABLE computers ADD COLUMN agent_token_hash VARCHAR(128);
ALTER TABLE computers ADD COLUMN agent_token_hint VARCHAR(8);
ALTER TABLE computers ADD COLUMN enrolled_at      TIMESTAMPTZ;
ALTER TABLE computers ADD COLUMN token_last_seen  TIMESTAMPTZ;
ALTER TABLE computers ADD COLUMN revoked          BOOLEAN NOT NULL DEFAULT FALSE;

CREATE TABLE enrollment_tokens (
    id         SERIAL PRIMARY KEY,
    token      VARCHAR(64) UNIQUE NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    expires_at TIMESTAMPTZ,
    used_at    TIMESTAMPTZ,
    used_by    VARCHAR(255)
);
```

### 5. Create the first user

```bash
python create_first_user.py
```

### 6. Start the server

```bash
python app.py
```
This runs Flask under Waitress on `http://0.0.0.0:5000` (plain HTTP, 16 threads). `app.py` does **not** load `server_cert.pem`/`key.pem` itself — that cert is only used for agent-side pinning.

For production, put IIS with URL Rewrite in front of Waitress and terminate TLS at IIS, so the dashboard and agents talk to the server over HTTPS end-to-end (e.g. `https://<your-server-ip>/`). Running `python app.py` directly is convenient for local development/testing but exposes the dashboard over plain HTTP.

---

## Agent Deployment

### Building the agent executable

```bash
pip install pyinstaller
pyinstaller --onefile agent.py --name agent
```

### Installer prerequisites

Place these files in the same directory as `installer.iss` before compiling:

- `agent.exe` — built by PyInstaller
- `nssm.exe` — Non-Sucking Service Manager (https://nssm.cc)
- `server_cert.pem` — the server's SSL certificate (copy from server)

Compile with [Inno Setup](https://jrsoftware.org/isinfo.php).

### Installer wizard

The GUI installer prompts for:

| Field | Description |
|-------|-------------|
| Server address | IP or hostname of the server (e.g. `192.168.1.10`) |
| Server port | Default `5000` |
| Enrollment password | Must match `ENROLLMENT_PASSWORD` in server `.env` |
| Server certificate | Path to `server_cert.pem` |

The installer writes `config.json` to the install directory, registers the cert in the Windows Trusted Root store, installs the agent as a Windows service via nssm, and starts the service only after `config.json` is ready.

### Silent install

```cmd
agent.exe /SILENT /SERVERADDR=192.168.1.10 /SERVERPORT=5000 /ENROLLMENTPW=yourpassword /CERT=C:\path\to\server_cert.pem
```

### Installation directory

Default: `C:\ProgramData\%your_InstallSubdir%`

To change the name, edit the `#define` variables at the top of `installer.iss`:

```pascal
#define AppName        "changeAppName"
#define AppVersion     "1.0"
#define AppExeName     "changeAppExeName.exe"
#define ServiceName    "changeServiceName"
#define DefaultPort    "5000"
#define InstallSubDir  "ChangeInstallSubdir"
```

---

## Dashboard Features

The web UI (`templates/dashboard.html`) is a single-page app with these tabs:

- **Dashboard** — software inventory table (per-machine, filterable/sortable), computer status, and an **Inventory Actions** sub-tab for bulk workflows: select machines by online/offline status, by IP range, or by outdated/matching software, then dispatch a bulk `upgrade` job across the selection.
- **Packages** — manage the internal package catalog used by the Deploy tab.
- **Deploy** — install or upgrade a package (community Chocolatey or internal source) across one or more selected machines, with live progress polling per machine.
- **Comman center** — send an immediate notification or schedule a restart across selected machines, with configurable urgency, delay options, and reminder milestones; view/cancel active notifications.
- **Jobs** — paginated job history with status, output, retry, and delete/cleanup actions.
- **Settings** — user management (add/remove users, change password), enrollment password lookup, internal source URL / other server config, and **agent management**: list enrolled agents with last-seen/version, revoke/un-revoke, and push an `agent_update` job to trigger a self-update.

Each machine also has a detail panel with quick actions (run a one-off command/message/restart, or send a package to just that machine) and a remote command console supporting `cmd.exe`, PowerShell, MSI uninstall by GUID, and MSI GUID lookup by app name.

---

## Agent Behavior

### First run — enrollment

On startup, if no `agent.key` exists the agent calls `POST /api/enroll` with the hostname and enrollment password. The server generates a unique token, stores its hash in the database, and returns the raw token to the agent which saves it as `agent.key`. If the hostname is already enrolled but `agent.key` is missing locally, the agent automatically retries with `force=true` to re-enroll.

### Inventory collection

Every **10 minutes** (`COLLECT_INTERVAL`) the agent:
1. Fetches server-side config (`GET /api/config`) for the internal Chocolatey source URL
2. Collects Windows registry installed software, installed Chocolatey packages, and nuspec metadata (`C:\ProgramData\chocolatey\lib\*\*.nuspec`) for title matching
3. Posts the results to `POST /api/inventory`

The server then kicks off a background refresh of any stale `choco_id` "latest version" lookups (default cache freshness: 1 hour) — checking the internal source first (web feed or UNC share), falling back to the public Chocolatey community repository, and keeping whichever version is highest.

### Software matching

The agent matches Windows registry entries to Chocolatey package IDs using:
1. Nuspec title match
2. Exact ID match
3. First-word match
4. Substring match

Unmatched Chocolatey packages are added to inventory directly.

### Job polling

Every **60 seconds** (± up to 15s jitter) the agent polls `GET /api/jobs/pending/<hostname>` and executes any queued jobs.

Supported `action` values:
- `install`, `upgrade`, `uninstall` — via Chocolatey
- `run_cmd`, `run_powershell` — arbitrary command/script execution (300s timeout)
- `run_msi_uninstall` — `msiexec /x <GUID>`, GUID strictly validated against `{8-4-4-4-12}` format before use
- `run_msi_guid_search` — looks up install GUIDs by display-name substring in the registry uninstall keys
- `agent_update` — downloads the latest agent build and hands off to `updater.py` to swap it in and restart the service

### Job execution

Jobs are validated before execution. `package_id` must match `[a-zA-Z0-9][a-zA-Z0-9._-]{0,198}[a-zA-Z0-9]`. `source_url` must be a UNC path (`\\server\share`) or an HTTP(S) URL. Jobs failing validation are rejected and reported back to the server without execution. For `upgrade`/`install` jobs with a `source_url`, the agent combines the internal share (root of the given path) with the public Chocolatey community feed as a single `--source` argument.

> **Known issue:** `choco upgrade` with a year-based internal version (e.g. `2026.x`) is treated by Chocolatey as newer than an older internal build numbered like `26.x`, which can cause upgrades to silently no-op. Fix under consideration: pass `--allow-downgrade` in `run_upgrade` whenever `source_url` is set.

### Notifications & scheduled restarts

Every **60 seconds** the agent also polls `GET /api/notify/pending/<hostname>` for due `notify` and `scheduled_restart` jobs. Popups are shown via `notify_popup.py`, launched in the active user's desktop session (the agent itself runs as a service in Session 0). Scheduled restarts support configurable reminder milestones (T-60/T-15/T-5 minutes) and a configurable number of user-initiated delays; user responses are reported back via `POST /api/notify/<job_id>/ack`.

### Self-update

The dashboard can push an `agent_update` job to one or more machines. The agent downloads the new build from `GET /api/agent/download`, saves it alongside the running exe, and launches the separate `updater.py` helper (since a running exe can't replace itself). The updater stops the NSSM service, renames the old exe to a backup, moves the new exe into place, and restarts the service.

### Token rotation

Every **30 days** (`KEY_ROTATION_DAYS`) the agent automatically calls `POST /api/rotate-key` with its current token. The server issues a new token and immediately invalidates the old one. No reinstallation required.

### Known limitation — internal share access

The Flask/Waitress service account typically does not have network share access, so server-side detection of internal Chocolatey repo versions from a share path (e.g. `\\server\ChocoPkgs`) can fail even though the path is reachable from managed machines. Verify the service account has read access to the share, or expose the internal feed over HTTP(S) instead.

---

## Security

### Per-agent tokens

Every agent has a unique token. Compromise of one machine does not affect others. Tokens are stored as SHA-256 hashes in the database — the raw token only ever exists on the agent.

### Revocation

From the dashboard (or directly via the API) an admin can revoke any individual agent. Revoked agents receive 401 on all requests immediately.

### Certificate pinning

The agent pins to the server's `server_cert.pem` file in its install directory. It does not rely solely on the Windows certificate store.

### Enrollment password

The enrollment password is stored only in the server's `.env` file and the agent's `config.json`. It is not stored in the database. Changing it on the server prevents new enrollments without affecting agents already enrolled.

### TLS

`generate_cert.py` produces the self-signed certificate the agent pins to (`server_cert.pem`) — the agent will not communicate with any other server or certificate. In production, TLS for the dashboard itself is expected to be terminated by IIS in front of Waitress; `app.py` alone serves plain HTTP and does not load `server_cert.pem`/`key.pem`.

### Session security
Web sessions are protected by `FLASK_SECRET_KEY`, which must be set in `.env` and never committed to source control.

### Input validation
The agent validates `package_id` and `source_url` against allowlist regexes before passing them to Chocolatey, and MSI uninstall GUIDs against a strict GUID pattern before calling `msiexec`. The server stores jobs as submitted — ensure access to the dashboard is restricted to trusted administrators, since the remote-command and MSI-uninstall job types execute arbitrary commands/scripts on managed machines.

### Disclaimer
**Internal deployment only:** This tool is designed for trusted internal networks. The agent API endpoints have no rate limiting. Do not expose the server to the public internet.


---

## API Endpoints

Auth model: session cookie (dashboard) or per-agent token via `X-Agent-Key` (or `Authorization: Bearer <token>`) header. See `auth.py`'s `before_request` hook for exactly which paths require what.

### Agent endpoints (per-agent token)

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/inventory` | Submit inventory data (also accepts a valid session) |
| `GET` | `/api/jobs/pending/<hostname>` | Poll for this agent's pending Chocolatey/command jobs |
| `PATCH` | `/api/jobs/<id>` | Report job status/output (`running`/`done`/`failed`) |
| `GET` | `/api/notify/pending/<hostname>` | Poll for due notifications / scheduled restarts |
| `POST` | `/api/notify/<job_id>/ack` | Report notification result (confirmed/delay/do_restart) |
| `GET` | `/api/config` | Fetch server config (internal source URL etc.; also accepts a valid session) |
| `POST` | `/api/rotate-key` | Rotate agent token |
| `GET` | `/api/agent/download` | Download the latest agent build for self-update |

### Public endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/enroll` | Enroll a new agent (requires enrollment password) |

### Dashboard endpoints (require session login unless noted)

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/computers` | List all computers and online/offline status |
| `DELETE` | `/api/computers/<hostname>` | Delete a computer and its jobs/software |
| `GET` | `/api/inventory` | Full software inventory across all machines, joined with cached Chocolatey latest versions |
| `POST` | `/api/jobs` | Create a job for a single computer |
| `POST` | `/api/jobs/bulk` | Create jobs for multiple computers, with optional `batch_size`/`batch_delay_seconds` staggering |
| `GET` | `/api/jobs` | List recent jobs (filterable by `status`/`hostname`, default limit 100) |
| `GET` | `/api/jobs/<job_id>` | Get a specific job |
| `DELETE` | `/api/jobs/<job_id>` | Delete a non-pending/non-running job |
| `DELETE` | `/api/jobs/completed` | Bulk-delete done/failed/cancelled jobs |
| `POST` | `/api/jobs/<job_id>/cancel` | Cancel a pending job |
| `POST` | `/api/jobs/<job_id>/retry` | Re-queue a failed/cancelled job as a fresh copy |
| `GET` | `/api/packages` | List managed (internal) packages |
| `POST` | `/api/packages` | Add/update a managed package |
| `DELETE` | `/api/packages/<id>` | Remove a managed package |
| `GET` | `/api/config` | Get server config |
| `POST` | `/api/config` | Update server config |
| `POST` | `/api/choco/refresh` | Force-refresh cached Chocolatey versions for all known packages |
| `GET` | `/api/choco/debug` | Dump the `choco_versions` cache table |
| `POST` | `/api/notify` | Create a `notify` or `scheduled_restart` job for one or more computers |
| `GET` | `/api/notify/active` | List pending/running notify + scheduled restart jobs |
| `POST` | `/api/notify/<job_id>/cancel` | Cancel a notification/restart job |
| `GET` | `/api/auth/agents` | List enrolled agents (hostname, token hint, last seen, revoked, agent version) |
| `POST` | `/api/auth/agents/<hostname>/revoke` | Revoke an agent |
| `POST` | `/api/auth/agents/<hostname>/unrevoke` | Un-revoke an agent |
| `GET` | `/api/auth/enrollment-password` | Retrieve the current enrollment password |
| `GET` | `/api/agent/version` | Get the configured "latest" agent version and whether the exe is present on disk |

### Auth endpoints (public unless noted)

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/auth/login` | Log in |
| `POST` | `/api/auth/logout` | Log out |
| `GET` | `/api/auth/me` | Current session info |
| `GET` | `/api/auth/users` | List users (session required) |
| `POST` | `/api/auth/users` | Create user (session required) |
| `DELETE` | `/api/auth/users/<id>` | Delete user, not self (session required) |
| `POST` | `/api/auth/change-password` | Change own password (session required) |

---

## Files on the Agent Machine

| Path              | Description                                                  |
|-------------------|--------------------------------------------------------------|
| `config.json`     | Server URL, port, enrollment password — written by installer |
| `server_cert.pem` | Server SSL certificate — copied by installer                 |
| `agent.key`       | Unique agent token — created on first run via enrollment     |
| `agent.log`       | Agent log file                                               |
| `nssm.exe`        | Installs agent as a Windows service                          |

All files live in the install directory (default `C:\ProgramData\<InstallSubDir>`, e.g. `C:\ProgramData\DMCPatchAgent`). On self-update, `dmcpatchagent_new.exe` and, briefly, `dmcpatchagent_old.exe` also appear here, along with `updater.log` and `notify_popup.log` for troubleshooting those components.

---

## Troubleshooting

**Agent not enrolling**
- Check `config.json` contains `enrollment_password`
- Check server `.env` has the same `ENROLLMENT_PASSWORD`
- Delete `agent.key` if it contains an old shared key and restart the service

**401 errors after enrollment**
- The token in `agent.key` may be stale — delete it and restart to re-enroll
- Check the agent is not revoked in the dashboard

**JSON parse error on startup**
- `config.json` has malformed content — reinstall or edit manually
- Ensure no unescaped backslashes in paths (the installer uses forward-slash-safe values)

**Service starts but no log file appears**
- Likely a race condition where the service started before `config.json` was written
- Restart the service — the installer now starts the service only after writing config

**Certificate errors**
- Regenerate `server_cert.pem` with the correct server IP in the SAN field using `generate_cert.py`
- Reinstall the agent so the new cert is copied and registered

**`choco upgrade` reports success but doesn't actually update the package**
- Likely the year-based internal version issue described in [Job execution](#job-execution): Chocolatey sees the currently-installed year-style version (e.g. `2026.x`) as newer than the internal repo's build-numbered version (e.g. `26.x`) and skips the upgrade
- Check `agent.log` for the actual `choco upgrade` output/version comparison
- Workaround under consideration: add `--allow-downgrade` in `run_upgrade` when `source_url` is set
