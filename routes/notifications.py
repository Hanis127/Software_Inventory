from flask import Blueprint, jsonify, request
from Software_inventory.db import query
from Software_inventory.auth import login_required, verify_agent_token
from datetime import datetime, timezone
import json

notify_bp = Blueprint('notifications', __name__)

# ── Dashboard: create notification / scheduled restart ────────────────────────

@notify_bp.route('/api/notify', methods=['POST'])
@login_required
def create_notification():
    """
    Create a notify or scheduled_restart job for one or more computers.

    Body:
    {
        "action":        "notify" | "scheduled_restart",
        "computers":     ["HOST1", "HOST2"],
        "notify_title":  "Scheduled Maintenance",
        "notify_message":"Please save your work and restart.",
        "urgency":       "info" | "warning" | "critical",
        "scheduled_for": "2026-03-20T14:00:00Z",   -- when first notification fires
        "restart_at":    "2026-03-21T06:00:00Z",   -- scheduled_restart only
        "delay_options": [15, 60, 240, 1440],       -- minutes user can choose to delay
        "max_delays":    4
    }
    """
    data         = request.json or {}
    action       = data.get('action', 'notify')
    computers    = data.get('computers', [])
    title        = data.get('notify_title', '').strip()
    message      = data.get('notify_message', '').strip()
    urgency      = data.get('urgency', 'warning')
    scheduled_for = data.get('scheduled_for')   # ISO string or None = now
    restart_at   = data.get('restart_at')        # ISO string, required for scheduled_restart
    delay_options = data.get('delay_options', [15, 60, 240, 1440])
    max_delays   = data.get('max_delays', 4)

    if action not in ('notify', 'scheduled_restart'):
        return jsonify({'error': 'Invalid action'}), 400
    if not computers:
        return jsonify({'error': 'No computers specified'}), 400
    if not title or not message:
        return jsonify({'error': 'Title and message required'}), 400
    if action == 'scheduled_restart' and not restart_at:
        return jsonify({'error': 'restart_at required for scheduled_restart'}), 400
    if urgency not in ('info', 'warning', 'critical'):
        urgency = 'warning'

    job_ids = []
    skipped = []
    for hostname in computers:
        row = query("SELECT id FROM computers WHERE hostname = %s", (hostname,), fetch='one')
        if not row:
            skipped.append(hostname)
            continue
        result = query("""
            INSERT INTO jobs (
                computer_id, package_id, display_name, action, status,
                notify_title, notify_message, urgency,
                scheduled_for, restart_at,
                delay_options, delay_count, max_delays,
                reminders_sent
            ) VALUES (
                %s, %s, %s, %s, 'pending',
                %s, %s, %s,
                COALESCE(%s::timestamptz, NOW()),
                %s::timestamptz,
                %s::jsonb, 0, %s,
                '[]'::jsonb
            ) RETURNING id
        """, (
            row['id'],
            action,                     # reuse package_id as identifier
            title,                      # display_name shown in job history
            action,
            title, message, urgency,
            scheduled_for,
            restart_at,
            json.dumps(delay_options),
            max_delays,
        ), fetch='one')
        job_ids.append(str(result['id']))

    return jsonify({'ok': True, 'queued': len(job_ids), 'job_ids': job_ids, 'skipped': skipped})


@notify_bp.route('/api/notify/active', methods=['GET'])
@login_required
def list_active_notifications():
    """Return pending/running notify and scheduled_restart jobs, optionally filtered by hostname."""
    hostname = request.args.get('hostname', '').strip().upper()
    if hostname:
        rows = query("""
            SELECT j.id, j.action, j.notify_title, j.notify_message, j.urgency,
                   j.scheduled_for, j.restart_at, j.delay_options, j.delay_count,
                   j.max_delays, j.reminders_sent, j.status, j.created_at,
                   c.hostname
            FROM jobs j
            JOIN computers c ON c.id = j.computer_id
            WHERE j.action IN ('notify', 'scheduled_restart')
              AND j.status IN ('pending', 'running')
              AND c.hostname = %s
            ORDER BY j.restart_at ASC NULLS LAST, j.scheduled_for ASC
        """, (hostname,), fetch='all')
    else:
        rows = query("""
            SELECT j.id, j.action, j.notify_title, j.notify_message, j.urgency,
                   j.scheduled_for, j.restart_at, j.delay_options, j.delay_count,
                   j.max_delays, j.reminders_sent, j.status, j.created_at,
                   c.hostname
            FROM jobs j
            JOIN computers c ON c.id = j.computer_id
            WHERE j.action IN ('notify', 'scheduled_restart')
              AND j.status IN ('pending', 'running')
            ORDER BY j.restart_at ASC NULLS LAST, j.scheduled_for ASC
        """, fetch='all')
    return jsonify([dict(r) for r in rows])


@notify_bp.route('/api/notify/<job_id>/cancel', methods=['POST'])
@login_required
def cancel_notification(job_id):
    row = query("SELECT id FROM jobs WHERE id = %s AND action IN ('notify','scheduled_restart')", (job_id,), fetch='one')
    if not row:
        return jsonify({'error': 'Not found'}), 404
    query("UPDATE jobs SET status = 'cancelled' WHERE id = %s", (job_id,))
    return jsonify({'ok': True})


# ── Agent: poll and respond to notifications ──────────────────────────────────

@notify_bp.route('/api/notify/pending/<hostname>', methods=['GET'])
def get_pending_notifications(hostname):
    """
    Agent calls this to get notifications/restarts due for delivery.
    Returns:
    - notify jobs where scheduled_for <= NOW()
    - scheduled_restart jobs where a reminder is due or restart_at <= NOW()
    """
    agent = verify_agent_token(request)
    if not agent:
        return jsonify({'error': 'Unauthorized'}), 401
    if agent['hostname'].upper() != hostname.upper():
        return jsonify({'error': 'Forbidden'}), 403

    now = datetime.now(timezone.utc)

    # Notify jobs due now
    notify_jobs = query("""
        SELECT j.id, j.action, j.notify_title, j.notify_message, j.urgency,
               j.scheduled_for, j.restart_at, j.delay_options, j.delay_count,
               j.max_delays, j.reminders_sent
        FROM jobs j
        JOIN computers c ON c.id = j.computer_id
        WHERE c.hostname = %s
          AND j.status = 'pending'
          AND j.action = 'notify'
          AND j.scheduled_for <= NOW()
    """, (hostname,), fetch='all')

    # Scheduled restart jobs — check if any reminder milestone is due
    restart_jobs = query("""
        SELECT j.id, j.action, j.notify_title, j.notify_message, j.urgency,
               j.scheduled_for, j.restart_at, j.delay_options, j.delay_count,
               j.max_delays, j.reminders_sent
        FROM jobs j
        JOIN computers c ON c.id = j.computer_id
        WHERE c.hostname = %s
          AND j.status = 'pending'
          AND j.action = 'scheduled_restart'
    """, (hostname,), fetch='all')

    due = []

    for job in notify_jobs:
        due.append(_serialize_job(job, 'notify'))

    # label, fires when minutes_left is within this window
    reminder_milestones = [
        ('t-60', 60, 62),
        ('t-15', 15, 17),
        ('t-5',   5,  7),
    ]

    for job in restart_jobs:
        restart_at = job['restart_at']
        if not restart_at:
            continue

        reminders_sent = job['reminders_sent'] or []
        if isinstance(reminders_sent, str):
            reminders_sent = json.loads(reminders_sent)

        restart_dt = restart_at if hasattr(restart_at, 'tzinfo') else \
            datetime.fromisoformat(str(restart_at)).replace(tzinfo=timezone.utc)

        minutes_left = (restart_dt - now).total_seconds() / 60

        # Time to restart — tell agent to execute it
        if minutes_left <= 0:
            due.append(_serialize_job(job, 'do_restart'))
            continue

        # Initial notification when scheduled_for has passed
        scheduled_for = job['scheduled_for']
        fired_label   = None
        if scheduled_for:
            sf_dt = scheduled_for if hasattr(scheduled_for, 'tzinfo') else \
                datetime.fromisoformat(str(scheduled_for)).replace(tzinfo=timezone.utc)
            if now >= sf_dt and 'initial' not in reminders_sent:
                fired_label = 'initial'

        # Fixed reminders at T-60, T-15, T-5 (only if initial already sent)
        if not fired_label and 'initial' in reminders_sent:
            for label, trigger_mins, window_top in reminder_milestones:
                if label not in reminders_sent and trigger_mins <= minutes_left <= window_top:
                    fired_label = label
                    break

        if fired_label:
            # Mark as sent in DB immediately before returning to agent
            # so that rapid re-polls don't fire the same reminder twice
            reminders_sent.append(fired_label)
            query("""
                UPDATE jobs SET reminders_sent = %s::jsonb, updated_at = NOW()
                WHERE id = %s
            """, (json.dumps(reminders_sent), job['id']))
            due.append(_serialize_job(job, 'remind', label=fired_label,
                                     minutes_left=int(minutes_left)))

    return jsonify(due)


def _serialize_job(job, deliver_as, label=None, minutes_left=None):
    delay_options = job['delay_options'] or [15, 60, 240, 1440]
    if isinstance(delay_options, str):
        delay_options = json.loads(delay_options)
    return {
        'id':            str(job['id']),
        'action':        job['action'],
        'deliver_as':    deliver_as,   # notify / remind / do_restart
        'reminder_label': label,
        'title':         job['notify_title'],
        'message':       job['notify_message'],
        'urgency':       job['urgency'],
        'restart_at':    str(job['restart_at']) if job['restart_at'] else None,
        'minutes_left':  minutes_left,
        'delay_options': delay_options,
        'delays_used':   job['delay_count'] or 0,
        'max_delays':    job['max_delays'] or 4,
    }


@notify_bp.route('/api/notify/<job_id>/ack', methods=['POST'])
def acknowledge_notification(job_id):
    """
    Agent calls this after showing a notification.
    Body: { "result": "confirmed" | "delay", "delay_minutes": 60, "reminder_label": "initial" }
    """
    agent = verify_agent_token(request)
    if not agent:
        return jsonify({'error': 'Unauthorized'}), 401

    job = query("""
        SELECT j.*, c.hostname FROM jobs j
        JOIN computers c ON c.id = j.computer_id
        WHERE j.id = %s
    """, (job_id,), fetch='one')
    if not job:
        return jsonify({'error': 'Not found'}), 404
    if job['hostname'].upper() != agent['hostname'].upper():
        return jsonify({'error': 'Forbidden'}), 403

    data          = request.json or {}
    result        = data.get('result')           # confirmed / delay
    delay_minutes = int(data.get('delay_minutes', 0))
    reminder_label = data.get('reminder_label')  # which reminder was shown

    reminders_sent = job['reminders_sent'] or []
    if isinstance(reminders_sent, str):
        reminders_sent = json.loads(reminders_sent)

    if result == 'confirmed':
        if job['action'] == 'notify':
            # Simple notification confirmed — mark done
            query("UPDATE jobs SET status = 'done', updated_at = NOW() WHERE id = %s", (job_id,))
        else:
            # Restart confirmed — mark this reminder as sent so it doesn't re-fire
            if reminder_label and reminder_label not in reminders_sent:
                reminders_sent.append(reminder_label)
            # Always persist reminders_sent even if unchanged, to confirm DB write
            query("""
                UPDATE jobs SET reminders_sent = %s::jsonb, updated_at = NOW()
                WHERE id = %s
            """, (json.dumps(reminders_sent), job_id))
            print(f"[ack] job {job_id} confirmed, label={reminder_label}, reminders_sent now={reminders_sent}")

    elif result == 'delay' and delay_minutes > 0:
        delay_count = (job['delay_count'] or 0) + 1
        max_delays  = job['max_delays'] or 4

        if delay_count > max_delays:
            return jsonify({'error': 'Max delays reached'}), 400

        # Shift restart_at and reschedule initial notification
        from datetime import timedelta
        if job['action'] == 'scheduled_restart' and job['restart_at']:
            restart_dt = job['restart_at']
            if not hasattr(restart_dt, 'tzinfo'):
                restart_dt = datetime.fromisoformat(str(restart_dt)).replace(tzinfo=timezone.utc)
            new_restart    = restart_dt + timedelta(minutes=delay_minutes)
            # Schedule first notification 2 minutes from now so agent doesn't
            # immediately re-fire the popup before the user has time to react
            new_scheduled  = datetime.now(timezone.utc) + timedelta(minutes=2)
            query("""
                UPDATE jobs
                SET restart_at      = %s,
                    scheduled_for   = %s,
                    delay_count     = %s,
                    reminders_sent  = '[]'::jsonb,
                    updated_at      = NOW()
                WHERE id = %s
            """, (new_restart.isoformat(), new_scheduled.isoformat(), delay_count, job_id))
        else:
            # Notify-only: reschedule after delay_minutes
            query("""
                UPDATE jobs
                SET scheduled_for = NOW() + (%s || ' minutes')::interval,
                    delay_count   = %s,
                    updated_at    = NOW()
                WHERE id = %s
            """, (str(delay_minutes), delay_count, job_id))

    elif result == 'do_restart':
        # Agent has executed the restart
        query("UPDATE jobs SET status = 'done', updated_at = NOW() WHERE id = %s", (job_id,))

    elif reminder_label:
        # Just mark reminder as sent, no user action
        if reminder_label not in reminders_sent:
            reminders_sent.append(reminder_label)
        query("""
            UPDATE jobs SET reminders_sent = %s::jsonb, updated_at = NOW()
            WHERE id = %s
        """, (json.dumps(reminders_sent), job_id))

    return jsonify({'ok': True})