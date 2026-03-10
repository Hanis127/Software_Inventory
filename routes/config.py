from flask import Blueprint, jsonify, request
from Software_inventory.db import query

config_bp = Blueprint('config', __name__)

@config_bp.route('/api/config', methods=['GET'])
def get_config():
    rows = query("SELECT key, value FROM config", fetch='all')
    return jsonify({r['key']: r['value'] for r in rows})

@config_bp.route('/api/config', methods=['POST'])
def set_config():
    data = request.json
    for key, value in data.items():
        query("""
            INSERT INTO config (key, value) VALUES (%s, %s)
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
        """, (key, value))
    return jsonify({'ok': True})