from flask import Blueprint, jsonify, request
from Software_inventory.db  import query
import uuid

jobs_bp = Blueprint('jobs', __name__)

# TODO Tady jsem komentoval
# @jobs_bp.route('/api/jobs', methods=['POST'])
# def create_job():
#     print("CREATE JOB CALLED")
#     data = request.json
#     print("Incoming JSON:", data)
#
#     computer     = data.get('computer')
#     package_id   = data.get('package_id')
#     display_name = data.get('display_name', package_id)
#     action       = data.get('action', 'upgrade')
#
#     row = query("SELECT id FROM computers WHERE hostname = %s", (computer,), fetch='one')
#     print("Row result:", row)
#
#     if not row:
#         print("Computer not found!")
#         return jsonify({'error': 'Computer not found'}), 404
#
#     computer_id = row['id']  # ✅ fix here
#
#     result = query(
#         """INSERT INTO jobs (computer_id, package_id, display_name, status, action)
#            VALUES (%s, %s, %s, 'pending', %s) RETURNING id""",
#         (computer_id, package_id, display_name, action),
#         fetch='one'  # <-- add fetch='one' to get the inserted row
#     )
#
#     job_id = result['id']
#     print("Inserted job ID:", job_id)
#
#     return jsonify({'job_id': str(job_id)})
#
#
# @jobs_bp.route("/api/jobs/pending/<hostname>")
# def pending_jobs(hostname):
#     rows = query("""
#         SELECT j.id, j.package_id, j.display_name, j.action
#         FROM jobs j
#         JOIN computers c ON c.id = j.computer_id
#         WHERE c.hostname = %s AND j.status = 'pending'
#         ORDER BY j.created_at
#     """, (hostname,), fetch='all')
#     return jsonify([dict(r) for r in rows])


@jobs_bp.route('/api/jobs', methods=['POST'])
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


@jobs_bp.route("/api/jobs/pending/<hostname>")
def pending_jobs(hostname):
    rows = query("""
        SELECT j.id, j.package_id, j.display_name, j.action, j.source_url
        FROM jobs j
        JOIN computers c ON c.id = j.computer_id
        WHERE c.hostname = %s AND j.status = 'pending'
        ORDER BY j.created_at
    """, (hostname,), fetch='all')
    return jsonify([dict(r) for r in rows])
# odsud nahoru po koment je to nove

@jobs_bp.route("/api/jobs/<job_id>", methods=["PATCH"])
def update_job(job_id):
    data = request.json
    query("""
        UPDATE jobs 
        SET status = %s, output = %s, updated_at = NOW()
        WHERE id = %s
    """, (data["status"], data.get("output", ""), job_id))
    return jsonify({"ok": True})


@jobs_bp.route("/api/jobs/<job_id>")
def get_job(job_id):
    row = query("SELECT * FROM jobs WHERE id = %s", (job_id,), fetch='one')
    return jsonify(dict(row)) if row else ("Not found", 404)


@jobs_bp.route("/api/jobs")
def list_jobs():
    rows = query("""
        SELECT j.*, c.hostname
        FROM jobs j
        JOIN computers c ON c.id = j.computer_id
        ORDER BY j.created_at DESC
        LIMIT 100
    """, fetch='all')
    return jsonify([dict(r) for r in rows])