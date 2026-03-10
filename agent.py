import urllib.request
import urllib.error
import json
import time
import subprocess
import socket
import os
import logging
import winreg
import re
import ssl
import xml.etree.ElementTree as ET
from datetime import datetime


# ── Config ────────────────────────────────────────────────────────────────────
SERVER_URL       = "https://YOUR_SERVER_IP:5000"
COLLECT_INTERVAL = 600   # 10 minutes
POLL_INTERVAL    = 60    # job polling in seconds
LOG_PATH         = r"C:\ProgramData\ChocoAgent\agent.log"
# ──────────────────────────────────────────────────────────────────────────────

os.makedirs(r"C:\ProgramData\ChocoAgent", exist_ok=True)

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
    req = urllib.request.Request(
        f"{SERVER_URL}{path}",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    with urllib.request.urlopen(req, timeout=10, context=_ssl_ctx) as r:
        return json.loads(r.read())

# SSL context that pins trust to the specific server certificate.
# Place server_cert.pem (copy of server_cert.pem from the Flask server) alongside agent.exe.
# This is more secure than a CA-signed cert for internal use — the agent will only
# talk to a server presenting exactly this certificate.
def _build_ssl_context():
    cert_candidates = [
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "server_cert.pem"),
        r"C:\ProgramData\ChocoAgent\server_cert.pem",
    ]
    for cert_path in cert_candidates:
        if os.path.exists(cert_path):
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_REQUIRED
            ctx.load_verify_locations(cafile=cert_path)
            log(f"SSL: pinned to cert at {cert_path}")
            return ctx
    # Fallback: no cert found — warn loudly but continue with verification disabled
    # Deploy server_cert.pem alongside agent.exe to fix this
    log("WARNING: server_cert.pem not found — SSL certificate verification is DISABLED. "
        "Copy server_cert.pem from the Flask server alongside agent.exe.")
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx

_ssl_ctx = None  # initialized in main() after logging is set up

def api_get(path):
    req = urllib.request.Request(f"{SERVER_URL}{path}")
    with urllib.request.urlopen(req, timeout=10, context=_ssl_ctx) as r:
        return json.loads(r.read())

def api_patch(path, data):
    body = json.dumps(data, default=str).encode()
    req = urllib.request.Request(
        f"{SERVER_URL}{path}",
        data=body,
        headers={"Content-Type": "application/json"},
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


# ── Main loop ─────────────────────────────────────────────────────────────────
def main():
    global _ssl_ctx
    _ssl_ctx = _build_ssl_context()
    log(f"Agent starting on {socket.gethostname()}")
    last_collect = 0
    config       = {}

    while True:
        now = time.time()

        if now - last_collect >= COLLECT_INTERVAL:
            config          = fetch_config()
            internal_source = normalize_unc(config.get('internal_source_url'))
            collect_and_send(internal_source=internal_source)
            last_collect = time.time()

        poll_jobs()
        time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    main()