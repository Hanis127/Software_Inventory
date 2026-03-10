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

# ── Agent API key auth ────────────────────────────────────────────────────────
def get_agent_key():
    row = query("SELECT value FROM config WHERE key = 'agent_api_key'", fetch='one')
    return row['value'] if row else None

def verify_agent_key(req):
    key      = req.headers.get('X-Agent-Key') or req.headers.get('Authorization', '').replace('Bearer ', '')
    expected = get_agent_key()
    if not expected or not key:
        return False
    return secrets.compare_digest(key.strip(), expected.strip())

def is_agent_path(path):
    agent_paths = ['/api/inventory', '/api/jobs/pending', '/api/jobs/', '/api/config']
    return any(path.startswith(p) for p in agent_paths)

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
        if is_agent_path(path):
            if not verify_agent_key(request):
                return jsonify({'error': 'Invalid or missing agent API key'}), 401
            return None
        if 'user_id' not in session:
            if path.startswith('/api/'):
                return jsonify({'error': 'Unauthorized'}), 401
        return None

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

@auth_bp.route('/api/auth/generate-agent-key', methods=['POST'])
@login_required
def generate_agent_key():
    new_key = secrets.token_urlsafe(32)
    query("INSERT INTO config (key, value) VALUES ('agent_api_key', %s) ON CONFLICT (key) DO UPDATE SET value = %s",
          (new_key, new_key))
    return jsonify({'ok': True, 'key': new_key})

@auth_bp.route('/api/auth/agent-key', methods=['GET'])
@login_required
def get_agent_key_endpoint():
    key = get_agent_key()
    if not key:
        return jsonify({'key': None, 'message': 'No agent key set. Generate one first.'})
    return jsonify({'key': key})