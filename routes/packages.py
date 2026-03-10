from flask import Blueprint, jsonify, request
from Software_inventory.db import query

packages_bp = Blueprint('packages', __name__)

@packages_bp.route('/api/packages', methods=['GET'])
def list_packages():
    rows = query("SELECT * FROM packages ORDER BY display_name", fetch='all')
    return jsonify([dict(r) for r in rows])

@packages_bp.route('/api/packages', methods=['POST'])
def add_package():
    data         = request.json
    choco_id     = data.get('choco_id', '').strip().lower()
    display_name = data.get('display_name', choco_id).strip()
    description  = data.get('description', '').strip()

    if not choco_id:
        return jsonify({'error': 'choco_id required'}), 400

    result = query("""
        INSERT INTO packages (choco_id, display_name, description)
        VALUES (%s, %s, %s)
        ON CONFLICT (choco_id) DO UPDATE SET
            display_name = EXCLUDED.display_name,
            description  = EXCLUDED.description
        RETURNING id
    """, (choco_id, display_name, description), fetch='one')

    return jsonify({'ok': True, 'id': result['id']})

@packages_bp.route('/api/packages/<int:pkg_id>', methods=['DELETE'])
def remove_package(pkg_id):
    query("DELETE FROM packages WHERE id = %s", (pkg_id,))
    return jsonify({'ok': True})