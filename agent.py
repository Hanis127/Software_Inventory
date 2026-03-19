import urllib.request
import urllib.error
import json
import time
import subprocess
import socket
import os
import threading
import logging
import winreg
import re
import ssl
import xml.etree.ElementTree as ET
from datetime import datetime

# ── Config ────────────────────────────────────────────────────────────────────
# config.json is written by the installer and lives next to the exe.
# Use sys.executable when frozen (PyInstaller), __file__ otherwise (dev/testing).
import sys
_exe_dir    = os.path.dirname(sys.executable if getattr(sys, 'frozen', False) else os.path.abspath(__file__))
CONFIG_PATH = os.path.join(_exe_dir, "config.json")

def _load_config():
    try:
        with open(CONFIG_PATH, 'r') as f:
            cfg = json.load(f)
        server_url = cfg.get('server_url', '').strip().rstrip('/')
        if not server_url:
            raise RuntimeError("config.json has no server_url — reinstall the agent")
        return cfg
    except FileNotFoundError:
        raise RuntimeError(f"config.json not found at {CONFIG_PATH} — reinstall the agent")

_cfg       = _load_config()
SERVER_URL = _cfg['server_url'].strip().rstrip('/')
INSTALL_DIR    = _exe_dir
LOG_PATH       = os.path.join(_exe_dir, 'agent.log')
AGENT_KEY_PATH = os.path.join(_exe_dir, 'agent.key')

COLLECT_INTERVAL  = 600
POLL_INTERVAL     = 60
NOTIFY_INTERVAL   = 60    # check for notifications every 60s
KEY_ROTATION_DAYS = 30
# ──────────────────────────────────────────────────────────────────────────────

os.makedirs(INSTALL_DIR, exist_ok=True)

logging.basicConfig(
    filename=LOG_PATH,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)

def log(msg):
    logging.info(msg)
    print(msg)


# ── Helpers ───────────────────────────────────────────────────────────────────
def api_post(path, data):
    body = json.dumps(data, default=str).encode()
    req  = urllib.request.Request(
        f"{SERVER_URL}{path}", data=body,
        headers=_auth_headers({"Content-Type": "application/json"}),
        method="POST"
    )
    with urllib.request.urlopen(req, timeout=10, context=_ssl_ctx) as r:
        return json.loads(r.read())

# SSL context that loads trusted certs from the Windows certificate store (CERTLM).
# Install server_cert.pem into the Windows Trusted Root CA store via your installer,
# then Python's ssl module will trust it automatically through the OS store.
def _build_ssl_context():
    # Prefer pinning to the exact cert file — most reliable for self-signed certs.
    cert_candidates = [
        os.path.join(_exe_dir, "server_cert.pem"),
        os.path.join(INSTALL_DIR, "server_cert.pem"),
    ]
    for cert_path in cert_candidates:
        if os.path.exists(cert_path):
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_REQUIRED
            ctx.load_verify_locations(cafile=cert_path)
            log(f"SSL: pinned to cert at {cert_path}")
            return ctx

    # Last resort — warn loudly
    log("WARNING: server_cert.pem not found — SSL verification DISABLED")
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx

_ssl_ctx = None  # initialized in main() after logging is set up

def get_agent_token():
    try:
        with open(AGENT_KEY_PATH, 'r') as f:
            return f.read().strip()
    except Exception:
        return None

def save_agent_token(token):
    with open(AGENT_KEY_PATH, 'w') as f:
        f.write(token)
    log(f"Agent token saved to {AGENT_KEY_PATH}")

def _auth_headers(extra=None):
    headers = dict(extra or {})
    token = get_agent_token()
    if token:
        headers['X-Agent-Key'] = token
    return headers

def enroll_agent():
    enrollment_password = _cfg.get('enrollment_password', '')
    if not enrollment_password:
        log("ERROR: No enrollment_password in config.json")
        return False
    hostname = socket.gethostname()
    log(f"Enrolling {hostname}...")

    def _do_enroll(force=False):
        payload = {'hostname': hostname, 'enrollment_password': enrollment_password}
        if force:
            payload['force'] = True
        body = json.dumps(payload).encode()
        req  = urllib.request.Request(
            f"{SERVER_URL}/api/enroll",
            data=body,
            headers={'Content-Type': 'application/json'},
            method='POST'
        )
        with urllib.request.urlopen(req, timeout=15, context=_ssl_ctx) as r:
            return json.loads(r.read())

    try:
        data = _do_enroll()
        if data.get('ok') and data.get('token'):
            save_agent_token(data['token'])
            log("Enrollment successful")
            return True
        log(f"Enrollment failed: {data}")
        return False
    except urllib.error.HTTPError as e:
        if e.code == 409:
            # Already enrolled but agent.key is missing — force re-enroll
            log("Hostname already enrolled but agent.key missing — retrying with force=true")
            try:
                data = _do_enroll(force=True)
                if data.get('ok') and data.get('token'):
                    save_agent_token(data['token'])
                    log("Force re-enrollment successful")
                    return True
                log(f"Force re-enrollment failed: {data}")
            except Exception as e2:
                log(f"Force re-enrollment error: {e2}")
            return False
        elif e.code == 401:
            log("ERROR: Enrollment password is incorrect. Check enrollment_password in config.json matches ENROLLMENT_PASSWORD in server .env.")
        elif e.code == 403:
            log("ERROR: This agent has been revoked. Contact your administrator.")
        else:
            log(f"Enrollment error: HTTP {e.code}: {e.reason}")
        return False
    except Exception as e:
        log(f"Enrollment error: {e}")
        return False

def should_rotate():
    try:
        import time as _time
        mtime    = os.path.getmtime(AGENT_KEY_PATH)
        age_days = (_time.time() - mtime) / 86400
        return age_days >= KEY_ROTATION_DAYS
    except Exception:
        return False

def rotate_token():
    log("Rotating agent token...")
    try:
        req = urllib.request.Request(
            f"{SERVER_URL}/api/rotate-key",
            data=b'{}',
            headers=_auth_headers({'Content-Type': 'application/json'}),
            method='POST'
        )
        with urllib.request.urlopen(req, timeout=15, context=_ssl_ctx) as r:
            data = json.loads(r.read())
        if data.get('ok') and data.get('token'):
            save_agent_token(data['token'])
            log("Token rotation successful")
            return True
        log(f"Token rotation failed: {data}")
        return False
    except Exception as e:
        log(f"Token rotation error: {e}")
        return False

def api_get(path):
    req = urllib.request.Request(f"{SERVER_URL}{path}", headers=_auth_headers())
    with urllib.request.urlopen(req, timeout=10, context=_ssl_ctx) as r:
        return json.loads(r.read())

def api_patch(path, data):
    body = json.dumps(data, default=str).encode()
    req  = urllib.request.Request(
        f"{SERVER_URL}{path}", data=body,
        headers=_auth_headers({"Content-Type": "application/json"}),
        method="PATCH"
    )
    with urllib.request.urlopen(req, timeout=10, context=_ssl_ctx) as r:
        return json.loads(r.read())


# ── Config fetch ──────────────────────────────────────────────────────────────
def fetch_config():
    """Fetch server-side config. Returns dict, empty dict on failure."""
    try:
        cfg = api_get("/api/config")
        log(f"Config fetched: {cfg}")
        return cfg
    except Exception as e:
        log(f"Failed to fetch config: {e}")
        return {}


# ── Data collection ───────────────────────────────────────────────────────────
def get_os_version():
    try:
        result = subprocess.run(
            ["powershell", "-Command",
             "(Get-WmiObject Win32_OperatingSystem).Caption"],
            capture_output=True, text=True, timeout=10,
            encoding='utf-8', errors='replace',
            creationflags=0x08000000
        )
        return result.stdout.strip()
    except Exception:
        return "Unknown"

def get_ip():
    try:
        return socket.gethostbyname(socket.gethostname())
    except Exception:
        return "Unknown"

def get_registry_software():
    software = {}
    reg_paths = [
        (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\Wow6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_CURRENT_USER,  r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
    ]
    for hive, path in reg_paths:
        try:
            key = winreg.OpenKey(hive, path)
            for i in range(winreg.QueryInfoKey(key)[0]):
                try:
                    subkey_name = winreg.EnumKey(key, i)
                    subkey = winreg.OpenKey(key, subkey_name)
                    def val(name):
                        try:
                            return winreg.QueryValueEx(subkey, name)[0]
                        except Exception:
                            return ""
                    name = val("DisplayName")
                    if name and name not in software:
                        software[name] = {
                            "display_name":    name,
                            "display_version": val("DisplayVersion"),
                            "publisher":       val("Publisher"),
                            "install_date":    val("InstallDate"),
                            "choco_id":        None
                        }
                except Exception:
                    continue
        except Exception:
            continue
    return list(software.values())

def get_nuspec_titles():
    """Returns {choco_id: title} by reading nuspec files from chocolatey lib folder."""
    titles = {}
    lib_path = r"C:\ProgramData\chocolatey\lib"
    try:
        if not os.path.exists(lib_path):
            return titles
        for pkg_id in os.listdir(lib_path):
            nuspec_path = os.path.join(lib_path, pkg_id, f"{pkg_id}.nuspec")
            if os.path.exists(nuspec_path):
                try:
                    tree = ET.parse(nuspec_path)
                    root = tree.getroot()
                    # nuspec uses a namespace
                    ns = root.tag.split('}')[0].strip('{') if '}' in root.tag else ''
                    ns_prefix = f"{{{ns}}}" if ns else ''
                    title_el = root.find(f".//{ns_prefix}title")
                    if title_el is not None and title_el.text:
                        titles[pkg_id.lower()] = title_el.text.strip()
                except Exception:
                    continue
    except Exception as e:
        log(f"Nuspec title scan failed: {e}")
    return titles

def get_choco_packages():
    """Returns {choco_id: installed_version} for all locally installed choco packages."""
    choco_map = {}
    try:
        result = subprocess.run(
            ["choco", "list", "--local-only", "--limit-output"],
            capture_output=True, text=True, timeout=30,
            encoding='utf-8', errors='replace',
            creationflags=0x08000000
        )
        for line in result.stdout.strip().splitlines():
            parts = line.split("|")
            if len(parts) == 2:
                choco_map[parts[0].strip().lower()] = parts[1].strip()
    except Exception as e:
        log(f"Choco list failed: {e}")
    return choco_map

def get_choco_outdated(internal_source=None):
    """Returns {choco_id: latest_version} for outdated packages.
    Runs against community feed and optionally internal source, merges results."""
    outdated = {}

    def run_outdated(extra_args=[]):
        try:
            result = subprocess.run(
                ["choco", "outdated", "--limit-output"] + extra_args,
                capture_output=True, text=True, timeout=60,
                encoding='utf-8', errors='replace',
                creationflags=0x08000000
            )
            for line in result.stdout.strip().splitlines():
                parts = line.split("|")
                if len(parts) >= 3:
                    pkg_id = parts[0].strip().lower()
                    latest = parts[2].strip()
                    outdated[pkg_id] = latest
        except Exception as e:
            log(f"Choco outdated failed {extra_args}: {e}")

    # Community feed
    run_outdated()

    # Internal feed
    if internal_source:
        run_outdated(["--source", internal_source])
        log(f"Checked internal source for outdated: {internal_source}")

    return outdated

def match_choco_id(display_name, choco_map, nuspec_titles=None):
    if not display_name:
        return None

    name_lower = display_name.lower().strip()
    name_clean = re.sub(r'[^a-z0-9]', '', name_lower)

    # Nuspec title match - compare registry display name to package title from nuspec
    if nuspec_titles:
        # Strip version numbers (e.g. "9.0.30729.6161") before comparing
        name_no_ver = re.sub(r'[\s\-]+[\d]+(?:\.[\d]+)+\s*$', '', name_lower).strip()
        name_no_ver_clean = re.sub(r'[^a-z0-9]', '', name_no_ver)
        for pkg_id, title in nuspec_titles.items():
            if pkg_id in choco_map:
                title_no_ver = re.sub(r'[\s\-]+[\d]+(?:\.[\d]+)+\s*$', '', title.lower()).strip()
                title_clean = re.sub(r'[^a-z0-9]', '', title_no_ver)
                # Exact match after stripping versions
                if name_no_ver_clean == title_clean:
                    return pkg_id
                # One contains the other
                if len(title_clean) > 5 and (title_clean in name_no_ver_clean or name_no_ver_clean in title_clean):
                    return pkg_id

    # Exact match on choco_id
    for pkg_id in choco_map:
        pkg_clean = re.sub(r'[^a-z0-9]', '', pkg_id.lower())
        if name_clean == pkg_clean:
            return pkg_id

    # First word match
    first_word = re.sub(r'[^a-z0-9]', '', name_lower.split()[0]) if name_lower.split() else ''
    if first_word and len(first_word) > 2:
        for pkg_id in choco_map:
            pkg_clean = re.sub(r'[^a-z0-9]', '', pkg_id.lower())
            if first_word == pkg_clean:
                return pkg_id

    # Choco ID substring of display name
    for pkg_id in choco_map:
        pkg_clean = re.sub(r'[^a-z0-9]', '', pkg_id.lower())
        if len(pkg_clean) > 3 and pkg_clean in name_clean:
            return pkg_id

    return None

def collect_and_send(internal_source=None):
    log("Collecting inventory...")
    hostname       = socket.gethostname()
    ip             = get_ip()
    os_ver         = get_os_version()
    software       = get_registry_software()
    choco_map      = get_choco_packages()
    choco_outdated = get_choco_outdated(internal_source=internal_source)
    nuspec_titles  = get_nuspec_titles()
    log(f"Nuspec titles found: {len(nuspec_titles)}")

    log(f"Choco installed: {len(choco_map)} packages, outdated: {len(choco_outdated)}")

    already_matched = set()

    for pkg in software:
        pkg["choco_id"] = match_choco_id(pkg["display_name"], choco_map, nuspec_titles)
        if pkg["choco_id"]:
            already_matched.add(pkg["choco_id"])
        if pkg["choco_id"] and pkg["choco_id"] in choco_outdated:
            pkg["choco_latest"] = choco_outdated[pkg["choco_id"]]
        elif pkg["choco_id"] and pkg["choco_id"] in choco_map:
            pkg["choco_latest"] = choco_map[pkg["choco_id"]]
        else:
            pkg["choco_latest"] = None

    for choco_id, installed_ver in choco_map.items():
        if choco_id not in already_matched:
            software.append({
                "display_name":    choco_id,
                "display_version": installed_ver,
                "publisher":       "",
                "install_date":    "",
                "choco_id":        choco_id,
                "choco_latest":    choco_outdated.get(choco_id, installed_ver)
            })
            log(f"  Added unmatched choco package directly: {choco_id} {installed_ver}")

    matched = sum(1 for p in software if p["choco_id"])
    log(f"Found {len(software)} packages, {matched} matched to Chocolatey")

    payload = {
        "hostname":   hostname,
        "ip_address": ip,
        "os_version": os_ver,
        "software":   software
    }

    try:
        result = api_post("/api/inventory", payload)
        log(f"Inventory sent: {result}")
    except Exception as e:
        log(f"Failed to send inventory: {e}")


# ── Job runners ───────────────────────────────────────────────────────────────
def normalize_unc(path):
    # Ensure UNC paths have double backslash prefix after JSON parsing eats one
    if path and not path.startswith('\\\\'):
        path = '\\\\' + path.lstrip('\\')
    return path

# ── Input validation ──────────────────────────────────────────────────────────
# Valid Chocolatey package IDs: letters, digits, dots, hyphens, underscores
VALID_PKG_ID = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9._-]{0,198}[a-zA-Z0-9]$')

# Valid source: UNC path (\\server\share) or http(s) URL
VALID_SOURCE = re.compile(
    r'^('
    r'\\\\[a-zA-Z0-9._-]+\\[a-zA-Z0-9._\\/:-]+'
    r'|https?://[a-zA-Z0-9._:/?&=%-]+'
    r')$'
)

def validate_package_id(pkg):
    if not pkg or not isinstance(pkg, str):
        return False
    return bool(VALID_PKG_ID.match(pkg))

def validate_source_url(url):
    if url is None:
        return True  # None = use default Chocolatey source, always fine
    if not isinstance(url, str):
        return False
    return bool(VALID_SOURCE.match(normalize_unc(url)))

def run_upgrade(package_id, source_url=None):
    log(f"Running: choco upgrade {package_id}" + (f" --source {source_url}" if source_url else ""))
    try:
        cmd = ["choco", "upgrade", package_id, "-y", "--no-progress"]
        if source_url:
            cmd += ["--source", source_url]
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=300,
            encoding='utf-8', errors='replace',
            creationflags=0x08000000
        )
        return result.returncode == 0, result.stdout + result.stderr
    except Exception as e:
        return False, str(e)

def run_install(package_id, source_url=None):
    log(f"Running: choco install {package_id}" + (f" --source {source_url}" if source_url else ""))
    try:
        cmd = ["choco", "install", package_id, "-y", "--no-progress"]
        if source_url:
            cmd += ["--source", source_url]
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=300,
            encoding='utf-8', errors='replace',
            creationflags=0x08000000
        )
        return result.returncode == 0, result.stdout + result.stderr
    except Exception as e:
        return False, str(e)

def run_uninstall(package_id):
    log(f"Running: choco uninstall {package_id}")
    try:
        result = subprocess.run(
            ["choco", "uninstall", package_id, "-y", "--no-progress"],
            capture_output=True, text=True, timeout=300,
            encoding='utf-8', errors='replace',
            creationflags=0x08000000
        )
        return result.returncode == 0, result.stdout + result.stderr
    except Exception as e:
        return False, str(e)


# ── Job polling ───────────────────────────────────────────────────────────────
def poll_jobs():
    hostname = socket.gethostname()
    try:
        jobs = api_get(f"/api/jobs/pending/{hostname}")
        for job in jobs:
            action     = job.get('action', 'upgrade')
            package_id = job.get('package_id', '')
            source_url = normalize_unc(job.get('source_url'))

            # Notification/restart jobs are handled by poll_notifications, not choco
            if action in ('notify', 'scheduled_restart'):
                continue

            # Validate before executing anything
            if not validate_package_id(package_id):
                log(f"SECURITY: Rejecting job {job['id']} - invalid package_id: {repr(package_id)}")
                api_patch(f"/api/jobs/{job['id']}", {"status": "failed", "output": f"Rejected: invalid package_id {repr(package_id)}"})
                continue
            if not validate_source_url(source_url):
                log(f"SECURITY: Rejecting job {job['id']} - invalid source_url: {repr(source_url)}")
                api_patch(f"/api/jobs/{job['id']}", {"status": "failed", "output": f"Rejected: invalid source_url {repr(source_url)}"})
                continue

            log(f"Job received: {action} {package_id}" + (f" from {source_url}" if source_url else ""))
            api_patch(f"/api/jobs/{job['id']}", {"status": "running", "output": ""})

            if action == 'install':
                success, output = run_install(package_id, source_url)
            elif action == 'uninstall':
                success, output = run_uninstall(package_id)
            else:
                success, output = run_upgrade(package_id, source_url)

            status = "done" if success else "failed"
            api_patch(f"/api/jobs/{job['id']}", {
                "status": status,
                "output": output[-3000:]
            })
            log(f"Job {job['id']} finished: {status}")
    except Exception as e:
        log(f"Job poll error: {e}")


# ── Notifications ─────────────────────────────────────────────────────────────
# Track popups already showing so we don't spawn duplicates
_active_popups      = set()
_active_popups_lock = threading.Lock()

def poll_notifications():
    """Check server for due notifications and scheduled restarts."""
    hostname = socket.gethostname()
    try:
        req = urllib.request.Request(
            f"{SERVER_URL}/api/notify/pending/{hostname}",
            headers=_auth_headers(),
            method='GET'
        )
        with urllib.request.urlopen(req, timeout=15, context=_ssl_ctx) as r:
            jobs = json.loads(r.read())
    except Exception as e:
        log(f"Notification poll error: {e}")
        return

    for job in jobs:
        job_id = job.get('id')
        with _active_popups_lock:
            if job_id in _active_popups:
                continue   # popup already showing for this job
            _active_popups.add(job_id)
        t = threading.Thread(
            target=_handle_notification_thread,
            args=(job,),
            daemon=True
        )
        t.start()


def _handle_notification_thread(job):
    """Runs in a background thread - never blocks the main loop."""
    try:
        handle_notification(job)
    except Exception as e:
        log(f"Notification handle error {job.get('id')}: {e}")
    finally:
        with _active_popups_lock:
            _active_popups.discard(job.get('id'))


def _launch_in_user_session(exe_path, arg):
    # Launch exe in the active user desktop session from a service (Session 0).
    # Pure ctypes implementation -- no pywin32 required.
    import ctypes
    import ctypes.wintypes
    import tempfile

    kernel32  = ctypes.windll.kernel32
    wtsapi32  = ctypes.windll.wtsapi32
    advapi32  = ctypes.windll.advapi32
    userenv   = ctypes.windll.userenv

    # Constants
    INFINITE                   = 0xFFFFFFFF
    NORMAL_PRIORITY_CLASS      = 0x00000020
    CREATE_NO_WINDOW           = 0x08000000
    CREATE_UNICODE_ENVIRONMENT = 0x00000400
    TokenPrimary               = 1
    SecurityImpersonation      = 2

    class STARTUPINFO(ctypes.Structure):
        _fields_ = [
            ("cb",              ctypes.wintypes.DWORD),
            ("lpReserved",      ctypes.wintypes.LPWSTR),
            ("lpDesktop",       ctypes.wintypes.LPWSTR),
            ("lpTitle",         ctypes.wintypes.LPWSTR),
            ("dwX",             ctypes.wintypes.DWORD),
            ("dwY",             ctypes.wintypes.DWORD),
            ("dwXSize",         ctypes.wintypes.DWORD),
            ("dwYSize",         ctypes.wintypes.DWORD),
            ("dwXCountChars",   ctypes.wintypes.DWORD),
            ("dwYCountChars",   ctypes.wintypes.DWORD),
            ("dwFillAttribute", ctypes.wintypes.DWORD),
            ("dwFlags",         ctypes.wintypes.DWORD),
            ("wShowWindow",     ctypes.wintypes.WORD),
            ("cbReserved2",     ctypes.wintypes.WORD),
            ("lpReserved2",     ctypes.c_char_p),
            ("hStdInput",       ctypes.wintypes.HANDLE),
            ("hStdOutput",      ctypes.wintypes.HANDLE),
            ("hStdError",       ctypes.wintypes.HANDLE),
        ]

    class PROCESS_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("hProcess",    ctypes.wintypes.HANDLE),
            ("hThread",     ctypes.wintypes.HANDLE),
            ("dwProcessId", ctypes.wintypes.DWORD),
            ("dwThreadId",  ctypes.wintypes.DWORD),
        ]

    result_file = tempfile.mktemp(suffix=".txt", prefix="notify_result_")
    hToken      = ctypes.wintypes.HANDLE()
    hDupToken   = ctypes.wintypes.HANDLE()

    try:
        # Get active console session
        session_id = kernel32.WTSGetActiveConsoleSessionId()
        if session_id == 0xFFFFFFFF:
            raise RuntimeError("No active console session")

        # Get user token for that session
        if not wtsapi32.WTSQueryUserToken(session_id, ctypes.byref(hToken)):
            raise RuntimeError(f"WTSQueryUserToken failed: {ctypes.GetLastError()}")

        # Duplicate as primary token
        if not advapi32.DuplicateTokenEx(
            hToken, 0x02000000, None, SecurityImpersonation,
            TokenPrimary, ctypes.byref(hDupToken)
        ):
            raise RuntimeError(f"DuplicateTokenEx failed: {ctypes.GetLastError()}")

        # Build environment block for user
        lpEnv = ctypes.c_void_p()
        userenv.CreateEnvironmentBlock(ctypes.byref(lpEnv), hDupToken, False)

        si = STARTUPINFO()
        si.cb        = ctypes.sizeof(STARTUPINFO)
        si.lpDesktop = "winsta0\\default"
        pi = PROCESS_INFORMATION()

        arg_escaped = arg.replace('"', '\\"')
        cmd = f'"{exe_path}" "{arg_escaped}" --result-file "{result_file}"'

        log(f"Launching popup in user session {session_id}")

        if not advapi32.CreateProcessAsUserW(
            hDupToken, None, cmd,
            None, None, False,
            NORMAL_PRIORITY_CLASS | CREATE_NO_WINDOW | CREATE_UNICODE_ENVIRONMENT,
            lpEnv, None, ctypes.byref(si), ctypes.byref(pi)
        ):
            raise RuntimeError(f"CreateProcessAsUserW failed: {ctypes.GetLastError()}")

        kernel32.WaitForSingleObject(pi.hProcess, INFINITE)
        kernel32.CloseHandle(pi.hProcess)
        kernel32.CloseHandle(pi.hThread)

        result_text = ""
        if os.path.exists(result_file):
            with open(result_file, "r") as f:
                result_text = f.read().strip()
        return result_text

    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(f"ctypes session launch error: {e}")
    finally:
        if hToken.value:
            kernel32.CloseHandle(hToken)
        if hDupToken.value:
            kernel32.CloseHandle(hDupToken)
        try:
            if os.path.exists(result_file):
                os.unlink(result_file)
        except Exception:
            pass


def handle_notification(job):
    import subprocess
    job_id       = job["id"]
    deliver_as   = job.get("deliver_as", "notify")
    reminder_lbl = job.get("reminder_label")

    log(f"Notification {job_id} deliver_as={deliver_as} urgency={job.get('urgency')}")

    popup_exe = os.path.join(_exe_dir, "notify_popup", "notify_popup.exe")
    if not getattr(sys, "frozen", False):
        popup_exe = os.path.join(_exe_dir, "notify_popup.py")
    payload   = json.dumps(job)

    log(f"Notification {job_id}: launching popup from {popup_exe}")
    if not os.path.exists(popup_exe):
        log(f"ERROR: popup not found at {popup_exe}")
        ack_notification(job_id, "dismissed", reminder_lbl)
        return

    result = "dismissed"
    try:
        result = _launch_in_user_session(popup_exe, payload)
        if not result:
            log(f"Notification {job_id}: popup returned no output")
            result = "dismissed"
    except RuntimeError as e:
        log(f"Notification {job_id}: user session launch failed ({e}), trying direct")
        try:
            cmd  = [popup_exe, payload] if getattr(sys, "frozen", False) \
                   else [sys.executable, popup_exe, payload]
            proc = subprocess.run(cmd, capture_output=True, text=True)
            result = proc.stdout.strip() or "dismissed"
            if proc.returncode != 0:
                log(f"Notification {job_id}: stderr: {proc.stderr.strip()[:300]}")
        except Exception as e2:
            log(f"Popup direct launch error: {e2}")
    except Exception as e:
        log(f"Popup launch error: {e}")

    log(f"Notification {job_id} result: {result}")
    ack_notification(job_id, result, reminder_lbl)

    if result == "do_restart":
        log("Executing scheduled restart...")
        subprocess.run(
            ["shutdown", "/r", "/t", "60", "/c",
             "Scheduled system restart, save your work. Restarting in 1 minute."],
            capture_output=True
        )


def ack_notification(job_id, result, reminder_label=None):
    delay_mins = None
    if result.startswith('delay:'):
        try:
            delay_mins = int(result.split(':')[1])
        except ValueError:
            delay_mins = 60
        result = 'delay'

    # dismissed means popup closed without action - treat as confirmed for notify
    # so the job doesn't stay stuck as pending forever
    if result == 'dismissed':
        result = 'confirmed'

    body = {'result': result}
    if delay_mins:
        body['delay_minutes'] = delay_mins
    if reminder_label:
        body['reminder_label'] = reminder_label

    try:
        data = json.dumps(body).encode()
        req  = urllib.request.Request(
            f"{SERVER_URL}/api/notify/{job_id}/ack",
            data=data,
            headers=_auth_headers({'Content-Type': 'application/json'}),
            method='POST'
        )
        with urllib.request.urlopen(req, timeout=15, context=_ssl_ctx) as r:
            pass
    except Exception as e:
        log(f"Ack error for {job_id}: {e}")


def main():
    global _ssl_ctx
    _ssl_ctx = _build_ssl_context()
    log(f"Agent starting on {socket.gethostname()}")
    last_collect = 0
    config       = {}

    # Enroll if no token yet
    if not get_agent_token():
        if not enroll_agent():
            log("ERROR: Enrollment failed. Check enrollment_password in config.json.")
            return

    # Rotate if due
    if should_rotate():
        rotate_token()

    while True:
        now = time.time()

        if now - last_collect >= COLLECT_INTERVAL:
            config          = fetch_config()
            internal_source = normalize_unc(config.get('internal_source_url'))
            collect_and_send(internal_source=internal_source)
            last_collect = time.time()
            if should_rotate():
                rotate_token()

        poll_jobs()
        poll_notifications()
        time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    main()