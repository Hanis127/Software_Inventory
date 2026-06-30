from flask import Blueprint, jsonify, request
from db import query, db_pool
from auth import login_required, verify_agent_token
import psycopg2.extras
import os
import requests, re, threading
from datetime import datetime, timezone, timedelta

inventory_bp = Blueprint('inventory', __name__)

CHOCO_API           = "https://community.chocolatey.org/api/v2"
CHOCO_REFRESH_HOURS = 1   # how long a cached "latest version" is considered fresh


@inventory_bp.route("/api/inventory", methods=["POST"])
def receive_inventory():
    """Agent posts inventory here every ~10 minutes."""
    data          = request.json
    hostname      = data.get("hostname")
    ip            = data.get("ip_address")
    os_ver        = data.get("os_version")
    agent_version = data.get("agent_version")
    software      = data.get("software", [])

    conn = None
    try:
        conn = db_pool.getconn()
        with conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                # Upsert computer
                cur.execute("""
                    INSERT INTO computers (hostname, ip_address, os_version, last_seen, status, agent_version)
                    VALUES (%s, %s, %s, NOW(), 'online', %s)
                    ON CONFLICT (hostname) DO UPDATE SET
                        ip_address   = EXCLUDED.ip_address,
                        os_version   = EXCLUDED.os_version,
                        last_seen    = NOW(),
                        status       = 'online',
                        agent_version = EXCLUDED.agent_version
                    RETURNING id
                """, (hostname, ip, os_ver, agent_version))
                computer_id = cur.fetchone()["id"]

                # Replace software for this machine
                cur.execute("DELETE FROM software WHERE computer_id = %s", (computer_id,))

                for pkg in software:
                    cur.execute("""
                        INSERT INTO software
                            (computer_id, display_name, display_version, publisher,
                             install_date, choco_id)
                        VALUES (%s, %s, %s, %s, %s, %s)
                    """, (
                        computer_id,
                        pkg.get("display_name"),
                        pkg.get("display_version"),
                        pkg.get("publisher"),
                        pkg.get("install_date"),
                        pkg.get("choco_id"),
                    ))
    finally:
        # This block executes even if an error occurs inside the 'with' block
        db_pool.putconn(conn)

    # Kick off a background refresh of any stale choco_id versions seen in
    # this payload. Doesn't block the agent's response.
    choco_ids = {pkg.get("choco_id") for pkg in software if pkg.get("choco_id")}
    if choco_ids:
        threading.Thread(
            target=refresh_stale_choco_versions,
            args=(choco_ids,),
            daemon=True
        ).start()

    return jsonify({"ok": True, "computer": hostname, "packages": len(software)})


@inventory_bp.route("/api/inventory")
def get_inventory():
    """Dashboard fetches all software from here. choco_latest comes from the
    centrally-maintained choco_versions table, not per-machine data."""
    rows = query("""
        SELECT
            c.hostname      AS computer,
            c.status,
            s.display_name,
            s.display_version,
            s.publisher,
            s.install_date,
            s.choco_id,
            cv.latest_version AS choco_latest,
            cv.checked_at      AS choco_cached_at
        FROM software s
        JOIN computers c ON c.id = s.computer_id
        LEFT JOIN choco_versions cv ON cv.choco_id = s.choco_id
        ORDER BY c.hostname, s.display_name
    """, fetch='all')
    return jsonify([dict(r) for r in rows])


# ── Choco version refresh (server-side, avoids agent-side NuGet caching) ──────

def get_internal_source_url():
    """Helper to dynamically fetch internal feed URL from the config table."""
    # Attempt 1: Assumes Key-Value structure (key='internal_source_url', value='...')
    try:
        rows = query("SELECT value FROM config WHERE key = 'internal_source_url'", fetch='all')
        if rows and rows[0].get('value'):
            return rows[0]['value'].rstrip('/')
    except Exception:
        # Silently drop to fallback if columns 'key'/'value' do not exist
        pass

    # Attempt 2: Assumes flat table layout with a specific column name
    try:
        rows = query("SELECT internal_source_url FROM config LIMIT 1", fetch='all')
        if rows and rows[0].get('internal_source_url'):
            return rows[0]['internal_source_url'].rstrip('/')
    except Exception as e:
        print(f"Error: Could not retrieve internal_source_url from config table: {e}")

    return None


def parse_versions_from_xml(xml_text, package_id):
    """Helper to cleanly extract and sort valid semantic version strings."""
    versions = re.findall(r"<d:Version[^>]*>(.*?)</d:Version>", xml_text)
    id_versions = re.findall(
        rf"Packages\(Id='{re.escape(package_id)}',Version='([^']+)'\)",
        xml_text, re.IGNORECASE
    )
    all_versions = list(set(versions + id_versions))

    if not all_versions:
        return None

    all_versions.sort(
        key=lambda v: [int(x) for x in re.sub(r'[^0-9.]', '', v).split('.') if x],
        reverse=True
    )
    return all_versions[0]


def refresh_stale_choco_versions(choco_ids, max_age_hours=CHOCO_REFRESH_HOURS):
    """For each choco_id, refresh latest_version if missing or older than max_age."""
    if not choco_ids:
        return

    # Grab the internal URL string exactly once before entering processing loops
    internal_url = get_internal_source_url()

    rows = query("""
        SELECT choco_id, checked_at FROM choco_versions WHERE choco_id = ANY(%s)
    """, (list(choco_ids),), fetch='all')
    checked = {r['choco_id']: r['checked_at'] for r in rows}

    now = datetime.now(timezone.utc)
    to_refresh = []
    for cid in choco_ids:
        last = checked.get(cid)
        if last is None:
            to_refresh.append(cid)
            continue
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        if (now - last) > timedelta(hours=max_age_hours):
            to_refresh.append(cid)

    for cid in to_refresh:
        latest = fetch_choco_latest(cid, internal_url)
        if latest:
            query("""
                INSERT INTO choco_versions (choco_id, latest_version, checked_at)
                VALUES (%s, %s, NOW())
                ON CONFLICT (choco_id) DO UPDATE SET
                    latest_version = EXCLUDED.latest_version,
                    checked_at     = NOW()
            """, (cid, latest))
        else:
            query("""
                INSERT INTO choco_versions (choco_id, latest_version, checked_at)
                VALUES (%s, NULL, NOW())
                ON CONFLICT (choco_id) DO UPDATE SET checked_at = NOW()
            """, (cid,))


def fetch_choco_latest(package_id, internal_url=None):
    # ─── STEP 1: INTERROGATE INTERNAL REPOSITORY FIRST ───
    if internal_url:
        internal_url_str = str(internal_url).strip()

        # A. Is it a Web Server Source? (ProGet, Nexus, Chocolatey.Server)
        if internal_url_str.lower().startswith(('http://', 'https://')):
            try:
                url = f"{internal_url_str}/FindPackagesById()?id='{package_id}'&semVerLevel=2.0.0"
                r = requests.get(url, timeout=10)
                if r.status_code == 200:
                    latest_internal = parse_versions_from_xml(r.text, package_id)
                    if latest_internal:
                        print(f"DEBUG: Found internal web version for {package_id}: {latest_internal}")
                        return latest_internal
            except Exception as e:
                print(f"Warning: Failed to scan internal web repo for {package_id}: {e}")

        # B. Is it a Local Directory / Windows Network Share Source? (\\Server\Share)
        else:
            try:
                if os.path.exists(internal_url_str):
                    files = os.listdir(internal_url_str)

                    # Local choco packages are named flatly: 'package_id.version.nupkg'
                    # e.g., 'sqlserver-odbcdriver-18.18.6.2.1.nupkg'
                    pattern = rf"^{re.escape(package_id)}\.(.+)\.nupkg$"
                    share_versions = []

                    for f in files:
                        match = re.match(pattern, f, re.IGNORECASE)
                        if match:
                            share_versions.append(match.group(1))

                    if share_versions:
                        share_versions.sort(
                            key=lambda v: [int(x) for x in re.sub(r'[^0-9.]', '', v).split('.') if x],
                            reverse=True
                        )
                        print(f"DEBUG: Found local share version for {package_id}: {share_versions[0]}")
                        return share_versions[0]
                else:
                    print(f"Warning: Internal share path does not exist or is inaccessible: {internal_url_str}")
            except Exception as e:
                print(f"Warning: Failed to read internal file share {internal_url_str}: {e}")

    # ─── STEP 2: FALLBACK TO SYSTEM PUBLIC COMMUNITY REPOSITORY ───
    try:
        url = f"{CHOCO_API}/FindPackagesById()?id='{package_id}'&semVerLevel=2.0.0"
        r = requests.get(url, timeout=10)
        r.raise_for_status()

        latest_public = parse_versions_from_xml(r.text, package_id)
        if latest_public:
            print(f"DEBUG: Found public version for {package_id}: {latest_public}")
            return latest_public
    except Exception as e:
        print(f"Error fetching {package_id} from public Choco: {e}")

    return None


@inventory_bp.route("/api/choco/refresh", methods=["POST"])
@login_required
def refresh_choco_versions_endpoint():
    """Manually force-refresh all known choco_ids."""
    rows = query("""
        SELECT DISTINCT choco_id FROM software WHERE choco_id IS NOT NULL
    """, fetch='all')
    choco_ids = [r['choco_id'] for r in rows]

    updated = 0
    for cid in choco_ids:
        latest = fetch_choco_latest(cid)
        if latest:
            query("""
                INSERT INTO choco_versions (choco_id, latest_version, checked_at)
                VALUES (%s, %s, NOW())
                ON CONFLICT (choco_id) DO UPDATE SET
                    latest_version = EXCLUDED.latest_version,
                    checked_at     = NOW()
            """, (cid, latest))
            updated += 1

    return jsonify({"ok": True, "updated": updated, "total": len(choco_ids)})


@inventory_bp.route("/api/choco/debug")
@login_required
def choco_debug():
    rows = query("""
        SELECT choco_id, latest_version, checked_at FROM choco_versions
        ORDER BY checked_at DESC NULLS LAST
    """, fetch='all')
    return jsonify({"db_rows": [dict(r) for r in rows]})