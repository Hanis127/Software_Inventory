from flask import Blueprint, jsonify
from Software_inventory.db  import query
import os

computers_bp = Blueprint('computers', __name__)

THRESHOLD = int(os.getenv("OFFLINE_THRESHOLD_MINUTES", 15))

@computers_bp.route("/api/computers")
def get_computers():
    rows = query("""
        SELECT 
            id, hostname, ip_address, os_version,
            last_seen,
            CASE 
                WHEN last_seen > NOW() - INTERVAL '%s minutes' THEN 'online'
                ELSE 'offline'
            END AS status
        FROM computers
        ORDER BY hostname
    """, (THRESHOLD,), fetch='all')
    return jsonify([dict(r) for r in rows])