# Software Inventory & Patch Manager

A lightweight Windows software inventory and patch management tool built with Flask, PostgreSQL, and a Windows agent. The server provides a web dashboard for monitoring installed software across machines and dispatching Chocolatey-based install/upgrade/uninstall jobs. The agent runs as a Windows service on each managed machine, collects inventory, and executes jobs posted by the dashboard.

---

## Architecture

```
┌─────────────────────────────────────┐
│          Web Dashboard              │
│        (Flask + PostgreSQL)         │
│                                     │
│  • Inventory view                   │
│  • Job dispatch (install/upgrade/   │
│    uninstall)                       │
│  • Computer status                  │
│  • Chocolatey version tracking      │
│  • HTTPS with self-signed cert      │
└────────────────┬────────────────────┘
                 │ HTTPS + API key
                 │
┌────────────────▼────────────────────┐
│          Windows Agent              │
│        (agent.exe via NSSM)         │
│                                     │
│  • Self-enrolls                     │
│  • Polls for pending jobs           │
│  • Collects registry software       │
│  • Matches to Chocolatey packages   │
│  • Reports inventory every 10 min   │
│  • Auto-rotates token every 30 days │
└─────────────────────────────────────┘
```
**Server:** Flask (Python), PostgreSQL, runs with a self-signed TLS certificate.  
**Agent:** Compiled Python (`agent.exe`), installed as a Windows service via NSSM, communicates over HTTPS with certificate pinning and an API key.

---


## Components

| File | Description |
|------|-------------|
| `agent.py` | Windows agent — inventory collection, enrollment, job execution |
| `app.py` | Flask API server |
| `auth.py` | Authentication — session auth, per-agent token enrollment/revocation/rotation |
| `dashboard.html` | Web UI — inventory browser, job management, user management |
| `installer.iss` | Inno Setup installer script for the Windows agent |
| `generate_cert.py` | Generates self-signed SSL certificate for the server |

---

## Requirements

### Server
- Python 3.10+
- PostgreSQL 13+
- The packages listed in `requirements.txt`

### Agent (each managed Windows machine)
- Tested on Windows 11 / Windows Server 2019
- [Chocolatey](https://chocolatey.org/install) installed
- [NSSM](https://nssm.cc/) (included in deployment package)
- `agent.exe`, `server_cert.pem` deployed to `C:\ProgramData\Agent\` and agent.key generated during installation

---

## Server Setup

### 1. Generate SSL certificate
```bash
git clone https://github.com/Hanis127/Software_Inventory.git
cd Software_Inventory
pip install -r requirements.txt
```
### 2. Generate SSL certificate

```bash
python generate_cert.py
```

This produces `server_cert.pem` and `key.pem`. The cert includes a SubjectAlternativeName for your server IP — required for cert pinning on agents.

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
The dashboard is available at `https://<your-server-ip>:5000`.

The server runs on HTTPS using `server_cert.pem` and `key.pem`.

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

## Agent Behavior

### First run — enrollment

On startup, if no `agent.key` exists the agent calls `POST /api/enroll` with the hostname and enrollment password. The server generates a unique token, stores its hash in the database, and returns the raw token to the agent which saves it as `agent.key`.

### Inventory collection

Every **10 minutes** the agent collects:
- Windows registry installed software
- Chocolatey installed packages and outdated status
- Nuspec metadata for package title matching

Results are sent to `POST /api/inventory`.

### Software matching

The agent matches Windows registry entries to Chocolatey package IDs using:
1. Nuspec title match (reads `C:\ProgramData\chocolatey\lib\*\*.nuspec`)
2. Exact ID match
3. First-word match
4. Substring match

Unmatched Chocolatey packages are added to inventory directly.

### Job polling

Every **60 seconds** the agent polls `GET /api/jobs/pending` and executes any queued jobs (install, update, uninstall via Chocolatey).

### Job execution

Jobs are validated before execution. `package_id` must match `[a-zA-Z0-9][a-zA-Z0-9._-]{0,198}[a-zA-Z0-9]`. `source_url` must be a UNC path (`\\server\share`) or an HTTP(S) URL. Jobs failing validation are rejected and reported back to the server without execution.

Supported actions: `install`, `upgrade`, `uninstall`.


### Token rotation

Every **30 days** the agent automatically calls `POST /api/rotate-key` with its current token. The server issues a new token and immediately invalidates the old one. No reinstallation required.

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

### TLS The server runs with a self-signed certificate
Certificate generated by `generate_cert.py`. The agent pins trust to this specific certificate — it will not communicate with any other server.
 
### Session security
Web sessions are protected by `FLASK_SECRET_KEY`, which must be set in `.env` and never committed to source control.

### Input validation
The agent validates `package_id` and `source_url` against allowlist regexes before passing them to Chocolatey. The server stores jobs as submitted — ensure access to the dashboard is restricted to trusted administrators.

### Disclaimer
Internal deployment only:** This tool is designed for trusted internal networks. The agent API endpoints have no rate limiting. Do not expose the server to the public internet.


---

## API Endpoints

### Agent endpoints (require valid agent token via `X-Agent-Key` header)

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/inventory` | Submit inventory data |
| `GET` | `/api/jobs/pending` | Poll for pending jobs |
| `POST` | `/api/jobs/<id>/result` | Report job result |
| `GET` | `/api/config` | Fetch server config (internal source URL etc.) |
| `POST` | `/api/rotate-key` | Rotate agent token |

### Public endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/enroll` | Enroll a new agent (requires enrollment password) |

### Dashboard endpoints (require session login)

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/computers` | List all computers and online status |
| `GET` | `/api/inventory` | Full software inventory across all machines |
| `POST` | `/api/jobs` | Create a job for a single computer |
| `POST` | `/api/jobs/bulk` | Create jobs for multiple computers |
| `GET` | `/api/jobs` | List recent jobs (last 100) |
| `GET` | `/api/jobs/<job_id>` | Get a specific job |
| `GET` | `/api/packages` | List managed packages |
| `POST` | `/api/packages` | Add a managed package |
| `DELETE` | `/api/packages/<id>` | Remove a managed package |
| `GET` | `/api/config` | Get server config |
| `POST` | `/api/config` | Update server config |
| `POST` | `/api/choco/refresh` | Refresh Chocolatey version cache |

### Auth endpoints (public)

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/auth/login` | Log in |
| `POST` | `/api/auth/logout` | Log out |
| `GET` | `/api/auth/me` | Current session info |
| `GET` | `/api/auth/users` | List users |
| `POST` | `/api/auth/users` | Create user |
| `DELETE` | `/api/auth/users/<id>` | Delete user |
| `POST` | `/api/auth/change-password` | Change password |
| `POST` | `/api/auth/generate-agent-key` | Generate new agent API key |
| `GET` | `/api/auth/agent-key` | Retrieve current agent API key |

---

## Files on the Agent Machine

| Path              | Description                                                  |
|-------------------|--------------------------------------------------------------|
| `config.json`     | Server URL, port, enrollment password — written by installer |
| `server_cert.pem` | Server SSL certificate — copied by installer                 |
| `agent.key`       | Unique agent token — created on first run via enrollment     |
| `agent.log`       | Agent log file                                               |
| `nssm.exe`        | Installs agent as a Windows service                          |

All files live in the install directory (default `C:\ProgramData\Agent`).

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
