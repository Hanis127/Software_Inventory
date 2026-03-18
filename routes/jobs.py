from flask import Blueprint, jsonify, request
from Software_inventory.db import query
from Software_inventory.auth import login_required, verify_agent_token

jobs_bp = Blueprint('jobs', __name__)

# ── Browser/dashboard routes (require login) ──────────────────────────────────

@jobs_bp.route('/api/jobs', methods=['POST'])
@login_required
def create_job():
    data         = request.json
    computer     = data.get('computer')
    package_id   = data.get('package_id')
    display_name = data.get('display_name', package_id)
    action       = data.get('action', 'upgrade')
    source_url   = data.get('source_url')
    row = query("SELECT id FROM computers WHERE hostname = %s",
                (computer,), fetch='one')
    if not row:
        return jsonify({'error': 'Computer not found'}), 404
    result = query("""
        INSERT INTO jobs (computer_id, package_id, display_name, status, action, source_url)
        VALUES (%s, %s, %s, 'pending', %s, %s) RETURNING id
    """, (row['id'], package_id, display_name, action, source_url), fetch='one')
    return jsonify({'job_id': str(result['id'])})

@jobs_bp.route('/api/jobs/bulk', methods=['POST'])
@login_required
def create_bulk_jobs():
    data         = request.json
    package_id   = data.get('package_id')
    display_name = data.get('display_name', package_id)
    action       = data.get('action', 'install')
    source_url   = data.get('source_url')
    hostnames    = data.get('computers', [])
    if not package_id or not hostnames:
        return jsonify({'error': 'package_id and computers required'}), 400
    job_ids = []
    skipped = []
    for hostname in hostnames:
        row = query("SELECT id FROM computers WHERE hostname = %s",
                    (hostname,), fetch='one')
        if row:
            result = query("""
                INSERT INTO jobs (computer_id, package_id, display_name,
                                  status, action, source_url)
                VALUES (%s, %s, %s, 'pending', %s, %s) RETURNING id
            """, (row['id'], package_id, display_name, action, source_url),
            fetch='one')
            job_ids.append(str(result['id']))
        else:
            skipped.append(hostname)
    return jsonify({'ok': True, 'queued': len(job_ids),
                    'job_ids': job_ids, 'skipped': skipped})

@jobs_bp.route('/api/jobs/completed', methods=['DELETE'])
@login_required
def delete_completed_jobs():
    result = query("""
        DELETE FROM jobs WHERE status IN ('done', 'failed', 'cancelled')
        RETURNING id
    """, fetch='all')
    return jsonify({'ok': True, 'deleted': len(result) if result else 0})

@jobs_bp.route('/api/jobs/<job_id>', methods=['DELETE'])
@login_required
def delete_job(job_id):
    row = query("SELECT id, status FROM jobs WHERE id = %s", (job_id,), fetch='one')
    if not row:
        return jsonify({'error': 'Job not found'}), 404
    if row['status'] in ('pending', 'running'):
        return jsonify({'error': 'Cannot delete a pending or running job — cancel it first'}), 400
    query("DELETE FROM jobs WHERE id = %s", (job_id,))
    return jsonify({'ok': True})

@jobs_bp.route('/api/jobs/<job_id>/cancel', methods=['POST'])
@login_required
def cancel_job(job_id):
    row = query("SELECT id, status FROM jobs WHERE id = %s", (job_id,), fetch='one')
    if not row:
        return jsonify({'error': 'Job not found'}), 404
    if row['status'] != 'pending':
        return jsonify({'error': f'Cannot cancel a job with status: {row["status"]}'}), 400
    query("UPDATE jobs SET status = 'cancelled', updated_at = NOW() WHERE id = %s", (job_id,))
    return jsonify({'ok': True})

@jobs_bp.route('/api/jobs/<job_id>', methods=['GET'])
@login_required
def get_job(job_id):
    row = query("SELECT * FROM jobs WHERE id = %s", (job_id,), fetch='one')
    return jsonify(dict(row)) if row else ('Not found', 404)

@jobs_bp.route('/api/jobs', methods=['GET'])
@login_required
def list_jobs():
    limit  = min(int(request.args.get('limit', 100)), 1000)
    status = request.args.get('status', '').strip()
    if status:
        rows = query("""
            SELECT j.*, c.hostname
            FROM jobs j
            JOIN computers c ON c.id = j.computer_id
            WHERE j.status = %s
            ORDER BY j.created_at DESC
            LIMIT %s
        """, (status, limit), fetch='all')
    else:
        rows = query("""
            SELECT j.*, c.hostname
            FROM jobs j
            JOIN computers c ON c.id = j.computer_id
            ORDER BY j.created_at DESC
            LIMIT %s
        """, (limit,), fetch='all')
    return jsonify([dict(r) for r in rows])

# ── Agent-only routes (require valid agent token) ─────────────────────────────

@jobs_bp.route('/api/jobs/pending/<hostname>')
def pending_jobs(hostname):
    agent = verify_agent_token(request)
    if not agent:
        return jsonify({'error': 'Invalid or missing agent token'}), 401
    # Agent can only fetch its own jobs
    if agent['hostname'].upper() != hostname.upper():
        return jsonify({'error': 'Forbidden'}), 403
    rows = query("""
        SELECT j.id, j.package_id, j.display_name, j.action, j.source_url
        FROM jobs j
        JOIN computers c ON c.id = j.computer_id
        WHERE c.hostname = %s AND j.status = 'pending'
        ORDER BY j.created_at
    """, (hostname,), fetch='all')
    return jsonify([dict(r) for r in rows])

@jobs_bp.route('/api/jobs/<job_id>', methods=['PATCH'])
def update_job(job_id):
    agent = verify_agent_token(request)
    if not agent:
        return jsonify({'error': 'Invalid or missing agent token'}), 401
    # Verify the job belongs to this agent
    row = query("""
        SELECT j.id FROM jobs j
        JOIN computers c ON c.id = j.computer_id
        WHERE j.id = %s AND c.hostname = %s
    """, (job_id, agent['hostname']), fetch='one')
    if not row:
        return jsonify({'error': 'Forbidden'}), 403
    data = request.json
    query("""
        UPDATE jobs
        SET status = %s, output = %s, updated_at = NOW()
        WHERE id = %s
    """, (data['status'], data.get('output', ''), job_id))
    return jsonify({'ok': True})