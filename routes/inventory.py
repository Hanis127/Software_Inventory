from flask import Blueprint, jsonify, request
from Software_inventory.db import query, get_conn
from Software_inventory.auth import login_required, verify_agent_token
import psycopg2.extras
import requests, re

inventory_bp = Blueprint('inventory', __name__)

CHOCO_API   = "https://community.chocolatey.org/api/v2"
CACHE_HOURS = 24

@inventory_bp.route("/api/inventory", methods=["POST"])
def receive_inventory():
    """Agent posts inventory here — authenticated via agent token in before_request."""
    data      = request.json
    hostname  = data.get("hostname")
    ip        = data.get("ip_address")
    os_ver    = data.get("os_version")
    software  = data.get("software", [])

    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                INSERT INTO computers (hostname, ip_address, os_version, last_seen, status)
                VALUES (%s, %s, %s, NOW(), 'online')
                ON CONFLICT (hostname) DO UPDATE SET
                    ip_address = EXCLUDED.ip_address,
                    os_version = EXCLUDED.os_version,
                    last_seen  = NOW(),
                    status     = 'online'
                RETURNING id
            """, (hostname, ip, os_ver))
            computer_id = cur.fetchone()["id"]

            cur.execute("DELETE FROM software WHERE computer_id = %s", (computer_id,))

            for pkg in software:
                cur.execute("""
                    INSERT INTO software
                        (computer_id, display_name, display_version, publisher,
                         install_date, choco_id, choco_latest, choco_cached_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
                """, (
                    computer_id,
                    pkg.get("display_name"),
                    pkg.get("display_version"),
                    pkg.get("publisher"),
                    pkg.get("install_date"),
                    pkg.get("choco_id"),
                    pkg.get("choco_latest"),
                ))

    return jsonify({"ok": True, "computer": hostname, "packages": len(software)})


@inventory_bp.route("/api/inventory")
def get_inventory():
    """Dashboard fetches all software — authenticated via session in before_request."""
    rows = query("""
        SELECT
            c.hostname      AS computer,
            c.status,
            s.display_name,
            s.display_version,
            s.publisher,
            s.install_date,
            s.choco_id,
            s.choco_latest,
            s.choco_cached_at
        FROM software s
        JOIN computers c ON c.id = s.computer_id
        ORDER BY c.hostname, s.display_name
    """, fetch='all')
    return jsonify([dict(r) for r in rows])


@inventory_bp.route("/api/choco/refresh", methods=["POST"])
@login_required
def refresh_choco_versions():
    stale = query("""
        SELECT DISTINCT choco_id
        FROM software
        WHERE choco_id IS NOT NULL
          AND (choco_cached_at IS NULL OR choco_cached_at < NOW() - INTERVAL '24 hours')
    """, fetch='all')

    updated = 0
    for row in stale:
        pkg_id = row["choco_id"]
        latest = fetch_choco_latest(pkg_id)
        if latest:
            query("""
                UPDATE software
                SET choco_latest = %s, choco_cached_at = NOW()
                WHERE choco_id = %s
            """, (latest, pkg_id))
            updated += 1

    return jsonify({"ok": True, "updated": updated})


def fetch_choco_latest(package_id):
    try:
        url = (
            f"{CHOCO_API}/Search()?searchTerm='{package_id}'"
            f"&targetFramework=''&includePrerelease=false"
        )
        r = requests.get(url, timeout=10)

        versions = re.findall(r"<d:Version[^>]*>(.*?)</d:Version>", r.text)
        id_versions = re.findall(
            rf"Packages\(Id='{re.escape(package_id)}',Version='([^']+)'\)",
            r.text, re.IGNORECASE
        )

        all_versions = list(set(versions + id_versions))
        if not all_versions:
            return None

        all_versions.sort(
            key=lambda v: [int(x) for x in re.sub(r'[^0-9.]', '', v).split('.') if x],
            reverse=True
        )
        return all_versions[0]
    except Exception as e:
        print(f"Error fetching {package_id}: {e}")
        return None


@inventory_bp.route("/api/choco/debug")
@login_required
def choco_debug():
    stale = query("""
        SELECT DISTINCT choco_id, choco_latest, choco_cached_at
        FROM software
        WHERE choco_id IS NOT NULL
    """, fetch='all')
    return jsonify({"db_rows": [dict(r) for r in stale]})