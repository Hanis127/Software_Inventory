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
└────────────────┬────────────────────┘
                 │ HTTPS + API key
                 │
┌────────────────▼────────────────────┐
│          Windows Agent              │
│        (agent.exe via NSSM)         │
│                                     │
│  • Polls for pending jobs           │
│  • Collects registry software       │
│  • Matches to Chocolatey packages   │
│  • Reports inventory every 10 min   │
└─────────────────────────────────────┘
```

**Server:** Flask (Python), PostgreSQL, runs with a self-signed TLS certificate.  
**Agent:** Compiled Python (`agent.exe`), installed as a Windows service via NSSM, communicates over HTTPS with certificate pinning and an API key.

---

## Requirements

### Server
- Python 3.10+
- PostgreSQL 13+
- The packages listed in `requirements.txt`

### Agent (each managed Windows machine)
- Windows 10 / Server 2016 or later
- [Chocolatey](https://chocolatey.org/install) installed
- [NSSM](https://nssm.cc/) (included in deployment package)
- `agent.exe`, `server_cert.pem`, and `agent.key` deployed to `C:\ProgramData\ChocoAgent\`

---

## Server Setup

### 1. Clone and install dependencies

```bash
git clone https://github.com/Hanis127/Software_Inventory.git
cd Software_Inventory
pip install -r requirements.txt
```

### 2. Configure environment

Copy `.env` to fill in your values:

```env
DB_HOST=localhost
DB_NAME=inventory
DB_USER=your_db_user
DB_PASSWORD=your_db_password
DB_PORT=5432
FLASK_SECRET_KEY=your-random-secret-key-here
OFFLINE_THRESHOLD_MINUTES=15
```

Generate a strong `FLASK_SECRET_KEY`:
```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

The server will refuse to start if `FLASK_SECRET_KEY` is not set.

### 3. Create the database

```sql
CREATE DATABASE inventory;
```

Then run the schema (see `__init__.py` or your migration script) to create the `computers`, `software`, `jobs`, `packages`, and `config` tables.

### 4. Generate TLS certificate

```bash
python generate_cert.py
```

This creates `server_cert.pem` and `key.pem` in the project root. Copy `server_cert.pem` to each agent deployment — the agent uses it for certificate pinning.

### 5. Create the first user

```bash
python create_first_user.py
```

### 6. Start the server

```bash
python app.py
```

The dashboard is available at `https://<your-server-ip>:5000`.

---

## Agent Deployment

### 1. Generate an agent API key

Log into the dashboard → Settings → **Generate Agent Key**. Copy the key.

### 2. Prepare the deployment package

Each managed machine needs:

| File | Location |
|---|---|
| `agent.exe` | `C:\ProgramData\ChocoAgent\` |
| `server_cert.pem` | `C:\ProgramData\ChocoAgent\` |
| `agent.key` | `C:\ProgramData\ChocoAgent\` |
| `nssm.exe` | Bundled with installer |
| `install.bat` | Run once to register the service |

Place the API key (plain text, no newline) in `agent.key`.

### 3. Run the installer

Run `install.bat` as Administrator on the target machine. It registers `agent.exe` as a Windows service named `ChocoAgent` via NSSM and starts it.

```
install.bat
```

The agent will appear in the dashboard within 10 minutes after its first inventory collection.

### 4. Verify

Check `C:\ProgramData\ChocoAgent\agent.log` for startup confirmation:

```
2025-01-01 12:00:00 INFO Agent starting on HOSTNAME
2025-01-01 12:00:00 INFO SSL: pinned to cert at C:\ProgramData\ChocoAgent\server_cert.pem
```

If `server_cert.pem` is missing, the agent logs a loud warning and falls back to disabled certificate verification — deploy the cert to fix this.

---

## Agent Behaviour

| Interval | Action |
|---|---|
| Every 10 minutes | Collects registry software, matches to Chocolatey IDs, sends inventory to server |
| Every 60 seconds | Polls for pending jobs and executes them |

### Software matching

The agent matches Windows registry entries to Chocolatey package IDs using:
1. Nuspec title match (reads `C:\ProgramData\chocolatey\lib\*\*.nuspec`)
2. Exact ID match
3. First-word match
4. Substring match

Unmatched Chocolatey packages are added to inventory directly.

### Job execution

Jobs are validated before execution. `package_id` must match `[a-zA-Z0-9][a-zA-Z0-9._-]{0,198}[a-zA-Z0-9]`. `source_url` must be a UNC path (`\\server\share`) or an HTTP(S) URL. Jobs failing validation are rejected and reported back to the server without execution.

Supported actions: `install`, `upgrade`, `uninstall`.

---

## Security Notes

- **TLS:** The server runs with a self-signed certificate generated by `generate_cert.py`. The agent pins trust to this specific certificate — it will not communicate with any other server.
- **Agent authentication:** Every agent request carries an `X-Agent-Key` header. Keys are generated via the dashboard and stored in the database. Rotate the key via the dashboard if it is compromised.
- **Session security:** Web sessions are protected by `FLASK_SECRET_KEY`, which must be set in `.env` and never committed to source control.
- **Input validation:** The agent validates `package_id` and `source_url` against allowlist regexes before passing them to Chocolatey. The server stores jobs as submitted — ensure access to the dashboard is restricted to trusted administrators.
- **Internal deployment only:** This tool is designed for trusted internal networks. The agent API endpoints have no rate limiting. Do not expose the server to the public internet.

---

## API Reference

### Agent endpoints (require `X-Agent-Key` header)

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/inventory` | Agent posts collected inventory |
| `GET` | `/api/jobs/pending/<hostname>` | Agent fetches pending jobs |
| `PATCH` | `/api/jobs/<job_id>` | Agent updates job status and output |
| `GET` | `/api/config` | Agent fetches server-side config |

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

## Known Limitations

- No connection pooling — each database query opens a new psycopg2 connection. Sufficient for small deployments; consider adding `psycopg2.pool.ThreadedConnectionPool` for larger environments.
- Chocolatey version data is fetched from the community feed via OData regex parsing, which may break if the feed format changes.
- No role-based access control — all authenticated users have full admin access.
- Minimum password length is 6 characters.
- No brute-force protection on the login endpoint.

---

## License

No license specified. All rights reserved by the author unless otherwise stated.
