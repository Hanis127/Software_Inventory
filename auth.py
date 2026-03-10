from flask import Blueprint, request, jsonify, session
from functools import wraps
from db import query
import hashlib
import os

auth_bp = Blueprint('auth', __name__)

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

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        # Allow agent endpoints without auth (they use internal HTTP or have no session)
        agent_paths = ['/api/inventory', '/api/jobs/pending/', '/api/jobs/']
        path = request.path
        if any(path.startswith(p) for p in agent_paths) and request.method in ('POST', 'PATCH', 'GET'):
            # Check if it's a browser request (has Accept: text/html) or agent request
            if 'text/html' not in request.headers.get('Accept', ''):
                return f(*args, **kwargs)
        if 'user_id' not in session:
            return jsonify({'error': 'Unauthorized'}), 401
        return f(*args, **kwargs)
    return decorated

def init_auth(app):
    """Call this from your main app to protect all API routes and serve login."""
    @app.before_request
    def require_login():
        # Public paths - no auth needed
        public = ['/login', '/api/auth/login', '/static/']
        if any(request.path.startswith(p) for p in public):
            return None
        # Agent paths - no session needed (machine-to-machine)
        agent_paths = ['/api/inventory', '/api/jobs/pending', '/api/jobs/']
        if any(request.path.startswith(p) for p in agent_paths):
            if 'text/html' not in request.headers.get('Accept', ''):
                return None
        # Everything else requires login
        if 'user_id' not in session:
            if request.path.startswith('/api/'):
                return jsonify({'error': 'Unauthorized'}), 401
            # Serve dashboard - it will show login screen based on /api/auth/me
            return None

# ── Auth API ──────────────────────────────────────────────────────────────────

@auth_bp.route('/api/auth/login', methods=['POST'])
def login():
    data     = request.json or {}
    username = data.get('username', '').strip().lower()
    password = data.get('password', '')
    if not username or not password:
        return jsonify({'error': 'Username and password required'}), 400
    row = query("SELECT id, password_hash FROM users WHERE username = %s",
                (username,), fetch='one')
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
def list_users():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    rows = query("SELECT id, username, created_at FROM users ORDER BY username", fetch='all')
    return jsonify([dict(r) for r in rows])

@auth_bp.route('/api/auth/users', methods=['POST'])
def create_user():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
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
    query("INSERT INTO users (username, password_hash) VALUES (%s, %s)",
          (username, pw_hash))
    return jsonify({'ok': True})

@auth_bp.route('/api/auth/users/<int:user_id>', methods=['DELETE'])
def delete_user(user_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    if user_id == session.get('user_id'):
        return jsonify({'error': 'Cannot delete your own account'}), 400
    query("DELETE FROM users WHERE id = %s", (user_id,))
    return jsonify({'ok': True})

@auth_bp.route('/api/auth/change-password', methods=['POST'])
def change_password():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    data         = request.json or {}
    current      = data.get('current_password', '')
    new_password = data.get('new_password', '')
    if len(new_password) < 6:
        return jsonify({'error': 'Password must be at least 6 characters'}), 400
    row = query("SELECT password_hash FROM users WHERE id = %s",
                (session['user_id'],), fetch='one')
    if not row or not verify_password(row['password_hash'], current):
        return jsonify({'error': 'Current password is incorrect'}), 401
    pw_hash = hash_password(new_password)
    query("UPDATE users SET password_hash = %s WHERE id = %s",
          (pw_hash, session['user_id']))
    return jsonify({'ok': True})