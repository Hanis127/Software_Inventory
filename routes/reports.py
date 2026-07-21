from flask import Blueprint, jsonify, request
from db import query
from auth import login_required

reports_bp = Blueprint('reports', __name__)

# Actions that represent an actual patch/software deployment, as opposed to
# remote commands (run_cmd/run_powershell) or notifications (notify/scheduled_restart).
DEPLOYMENT_ACTIONS = ('install', 'upgrade', 'uninstall', 'run_msi_uninstall', 'agent_update')


@reports_bp.route('/api/reports/job-history')
@login_required
def job_history():
    """Per-day job status counts, for the Reporting tab's deployment chart.

    Query params:
      days     - lookback window in days (default 30, max 180)
      action   - filter to a single action (default: all deployment-type actions)
      hostname - filter to a single computer
    """
    days     = min(int(request.args.get('days', 30)), 180)
    action   = request.args.get('action', '').strip()
    hostname = request.args.get('hostname', '').strip().upper()

    where  = ["j.created_at >= NOW() - (%s || ' days')::interval"]
    params = [days]

    if action:
        where.append("j.action = %s")
        params.append(action)
    else:
        where.append("j.action = ANY(%s)")
        params.append(list(DEPLOYMENT_ACTIONS))

    if hostname:
        where.append("c.hostname = %s")
        params.append(hostname)

    rows = query(f"""
        SELECT DATE(j.created_at) AS day, j.action, j.status, COUNT(*) AS count
        FROM jobs j
        JOIN computers c ON c.id = j.computer_id
        WHERE {' AND '.join(where)}
        GROUP BY DATE(j.created_at), j.action, j.status
        ORDER BY day
    """, params, fetch='all')

    return jsonify([dict(r) for r in rows])


@reports_bp.route('/api/reports/job-actions')
@login_required
def job_actions():
    """Distinct deployment-type actions seen in jobs, to populate the report's action filter."""
    rows = query("""
        SELECT DISTINCT action FROM jobs
        WHERE action = ANY(%s)
        ORDER BY action
    """, (list(DEPLOYMENT_ACTIONS),), fetch='all')
    return jsonify([r['action'] for r in rows])