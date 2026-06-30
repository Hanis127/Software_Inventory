from flask import Blueprint, jsonify
from db  import query
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

@computers_bp.route('/api/computers/<hostname>', methods=['DELETE'])
def delete_computer(hostname):
    row = query("SELECT id FROM computers WHERE hostname = %s", (hostname,), fetch='one')
    if not row:
        return jsonify({'error': 'Computer not found'}), 404
    computer_id = row['id']
    # Delete in order to respect FK constraints
    query("DELETE FROM jobs    WHERE computer_id = %s", (computer_id,))
    query("DELETE FROM software WHERE computer_id = %s", (computer_id,))
    query("DELETE FROM computers WHERE id = %s", (computer_id,))
    return jsonify({'ok': True})