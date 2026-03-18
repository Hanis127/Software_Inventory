from flask import Blueprint, request, jsonify, session
from functools import wraps
from Software_inventory.db import query
import hashlib
import os
import secrets

auth_bp = Blueprint('auth', __name__)

# ── Password hashing ──────────────────────────────────────────────────────────
def hash_password(password):
    salt = os.urandom(32)
    key  = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 100000)
    return salt.hex() + ':' + key.hex()

def verify_password(stored, provided):
    try:
        salt_hex, key_hex = stored.split(':')
        salt = bytes.fromhex(salt_hex)
        key  = hashlib.pbkdf2_hmac('sha256', provided.encode(), salt, 100000)
        return key.hex() == key_hex
    except Exception:
        return False

# ── Agent token hashing ───────────────────────────────────────────────────────
def hash_token(token):
    """SHA-256 hash of the raw token — fast is fine here, no need for pbkdf2."""
    return hashlib.sha256(token.encode()).hexdigest()

def generate_agent_token():
    """Generate a new unique agent token. Returns (raw_token, token_hash, hint)."""
    raw   = secrets.token_urlsafe(32)
    hashed = hash_token(raw)
    hint  = raw[:8]
    return raw, hashed, hint

# ── Agent authentication ──────────────────────────────────────────────────────
def get_request_token(req):
    return req.headers.get('X-Agent-Key') or req.headers.get('Authorization', '').replace('Bearer ', '').strip()

def verify_agent_token(req):
    """
    Verify the agent token from the request.
    Returns the computer row if valid, None otherwise.
    Updates token_last_seen on success.
    """
    raw_token = get_request_token(req)
    if not raw_token:
        return None
    token_hash = hash_token(raw_token)
    row = query(
        "SELECT id, hostname, revoked FROM computers WHERE agent_token_hash = %s",
        (token_hash,), fetch='one'
    )
    if not row or row['revoked']:
        return None
    # Update last seen
    query("UPDATE computers SET token_last_seen = NOW() WHERE id = %s", (row['id'],))
    return row

def is_agent_path(path):
    # Only paths where agents authenticate via token AND have no @login_required decorator.
    # Jobs and notify routes handle their own auth explicitly -- do NOT add them here.
    agent_paths = ['/api/inventory', '/api/config']
    return any(path.startswith(p) for p in agent_paths)

def is_self_auth_path(path):
    # Paths that handle their own auth internally -- bypass before_request check.
    self_auth = ['/api/jobs/', '/api/jobs', '/api/notify/']
    return any(path.startswith(p) for p in self_auth)

# ── Enrollment password ───────────────────────────────────────────────────────
def get_enrollment_password():
    return os.environ.get('ENROLLMENT_PASSWORD', '').strip()

# ── Session auth ──────────────────────────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify({'error': 'Unauthorized'}), 401
        return f(*args, **kwargs)
    return decorated

def init_auth(app):
    @app.before_request
    def require_login():
        path = request.path
        if path.startswith('/api/auth/') or path.startswith('/static/'):
            return None
        # Enrollment and rotation endpoints are public (self-protected)
        if path in ('/api/enroll', '/api/rotate-key'):
            return None
        # Routes that handle their own auth internally
        if is_self_auth_path(path):
            return None
        # Agent paths: accept valid per-agent token OR browser session
        if is_agent_path(path):
            if verify_agent_token(request) or 'user_id' in session:
                return None
            return jsonify({'error': 'Invalid or missing agent token'}), 401
        # All other API paths: require session
        if 'user_id' not in session:
            if path.startswith('/api/'):
                return jsonify({'error': 'Unauthorized'}), 401
        return None

# ── Enrollment ────────────────────────────────────────────────────────────────
@auth_bp.route('/api/enroll', methods=['POST'])
def enroll():
    """
    Agent calls this on first run to get its unique token.
    Requires the enrollment password set by the admin.
    If the hostname is already enrolled and not revoked, returns a conflict.
    """
    data                = request.json or {}
    hostname            = data.get('hostname', '').strip().upper()
    enrollment_password = data.get('enrollment_password', '')

    if not hostname:
        return jsonify({'error': 'hostname required'}), 400

    # Verify enrollment password
    expected = get_enrollment_password()
    if not expected:
        return jsonify({'error': 'Enrollment not configured on server'}), 503
    if not secrets.compare_digest(enrollment_password, expected):
        return jsonify({'error': 'Invalid enrollment password'}), 401

    # Check if already enrolled
    existing = query(
        "SELECT id, revoked FROM computers WHERE hostname = %s AND agent_token_hash IS NOT NULL",
        (hostname,), fetch='one'
    )
    force = data.get('force', False)
    if existing:
        if existing['revoked']:
            return jsonify({'error': 'This agent has been revoked. Contact your administrator.'}), 403
        if not force:
            return jsonify({'error': 'Already enrolled. Use /api/rotate-key to refresh token, or re-enroll with force=true if agent.key was lost.'}), 409
        # force=true: wipe old token and re-enroll (enrollment password already verified above)

    # Generate unique token
    raw_token, token_hash, hint = generate_agent_token()

    # Upsert computer record with token
    query("""
        INSERT INTO computers (hostname, agent_token_hash, agent_token_hint, enrolled_at, token_last_seen, revoked)
        VALUES (%s, %s, %s, NOW(), NOW(), FALSE)
        ON CONFLICT (hostname) DO UPDATE
        SET agent_token_hash = EXCLUDED.agent_token_hash,
            agent_token_hint = EXCLUDED.agent_token_hint,
            enrolled_at      = NOW(),
            token_last_seen  = NOW(),
            revoked          = FALSE
    """, (hostname, token_hash, hint))

    return jsonify({'ok': True, 'token': raw_token})

# ── Key rotation ──────────────────────────────────────────────────────────────
@auth_bp.route('/api/rotate-key', methods=['POST'])
def rotate_key():
    """
    Agent calls this to rotate its own token.
    Must present its current valid token.
    Returns a new token, old one is immediately invalidated.
    """
    agent = verify_agent_token(request)
    if not agent:
        return jsonify({'error': 'Invalid or missing agent token'}), 401

    raw_token, token_hash, hint = generate_agent_token()
    query("""
        UPDATE computers
        SET agent_token_hash = %s, agent_token_hint = %s, enrolled_at = NOW()
        WHERE id = %s
    """, (token_hash, hint, agent['id']))

    return jsonify({'ok': True, 'token': raw_token})

# ── Admin: agent management ───────────────────────────────────────────────────
@auth_bp.route('/api/auth/agents', methods=['GET'])
@login_required
def list_agents():
    rows = query("""
        SELECT hostname, agent_token_hint, enrolled_at, token_last_seen, revoked
        FROM computers
        WHERE agent_token_hash IS NOT NULL
        ORDER BY hostname
    """, fetch='all')
    return jsonify([dict(r) for r in rows])

@auth_bp.route('/api/auth/agents/<hostname>/revoke', methods=['POST'])
@login_required
def revoke_agent(hostname):
    row = query("SELECT id FROM computers WHERE hostname = %s", (hostname,), fetch='one')
    if not row:
        return jsonify({'error': 'Agent not found'}), 404
    query("UPDATE computers SET revoked = TRUE WHERE hostname = %s", (hostname,))
    return jsonify({'ok': True})

@auth_bp.route('/api/auth/agents/<hostname>/unrevoke', methods=['POST'])
@login_required
def unrevoke_agent(hostname):
    row = query("SELECT id FROM computers WHERE hostname = %s", (hostname,), fetch='one')
    if not row:
        return jsonify({'error': 'Agent not found'}), 404
    query("UPDATE computers SET revoked = FALSE WHERE hostname = %s", (hostname,))
    return jsonify({'ok': True})

@auth_bp.route('/api/auth/enrollment-password', methods=['GET'])
@login_required
def get_enrollment_password_endpoint():
    pw = get_enrollment_password()
    if not pw:
        return jsonify({'password': None, 'message': 'ENROLLMENT_PASSWORD not set in server .env'})
    return jsonify({'password': pw})

# ── Auth API ──────────────────────────────────────────────────────────────────
@auth_bp.route('/api/auth/login', methods=['POST'])
def login():
    data     = request.json or {}
    username = data.get('username', '').strip().lower()
    password = data.get('password', '')
    if not username or not password:
        return jsonify({'error': 'Username and password required'}), 400
    row = query("SELECT id, password_hash FROM users WHERE username = %s", (username,), fetch='one')
    if not row or not verify_password(row['password_hash'], password):
        return jsonify({'error': 'Invalid username or password'}), 401
    session['user_id']  = row['id']
    session['username'] = username
    session.permanent   = True
    return jsonify({'ok': True, 'username': username})

@auth_bp.route('/api/auth/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({'ok': True})

@auth_bp.route('/api/auth/me')
def me():
    if 'user_id' not in session:
        return jsonify({'logged_in': False}), 401
    return jsonify({'logged_in': True, 'username': session.get('username')})

@auth_bp.route('/api/auth/users', methods=['GET'])
@login_required
def list_users():
    rows = query("SELECT id, username, created_at FROM users ORDER BY username", fetch='all')
    return jsonify([dict(r) for r in rows])

@auth_bp.route('/api/auth/users', methods=['POST'])
@login_required
def create_user():
    data     = request.json or {}
    username = data.get('username', '').strip().lower()
    password = data.get('password', '')
    if not username or not password:
        return jsonify({'error': 'Username and password required'}), 400
    if len(password) < 6:
        return jsonify({'error': 'Password must be at least 6 characters'}), 400
    existing = query("SELECT id FROM users WHERE username = %s", (username,), fetch='one')
    if existing:
        return jsonify({'error': 'Username already exists'}), 409
    pw_hash = hash_password(password)
    query("INSERT INTO users (username, password_hash) VALUES (%s, %s)", (username, pw_hash))
    return jsonify({'ok': True})

@auth_bp.route('/api/auth/users/<int:user_id>', methods=['DELETE'])
@login_required
def delete_user(user_id):
    if user_id == session.get('user_id'):
        return jsonify({'error': 'Cannot delete your own account'}), 400
    query("DELETE FROM users WHERE id = %s", (user_id,))
    return jsonify({'ok': True})

@auth_bp.route('/api/auth/change-password', methods=['POST'])
@login_required
def change_password():
    data         = request.json or {}
    current      = data.get('current_password', '')
    new_password = data.get('new_password', '')
    if len(new_password) < 6:
        return jsonify({'error': 'Password must be at least 6 characters'}), 400
    row = query("SELECT password_hash FROM users WHERE id = %s", (session['user_id'],), fetch='one')
    if not row or not verify_password(row['password_hash'], current):
        return jsonify({'error': 'Current password is incorrect'}), 401
    pw_hash = hash_password(new_password)
    query("UPDATE users SET password_hash = %s WHERE id = %s", (pw_hash, session['user_id']))
    return jsonify({'ok': True})