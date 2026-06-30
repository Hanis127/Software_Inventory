import os
from flask import Blueprint, jsonify, send_file, current_app
from auth import login_required, verify_agent_token

agent_update_bp = Blueprint('agent_update', __name__)

# Path to the latest compiled agent exe. Override via AGENT_EXE_PATH in .env.
AGENT_EXE_PATH = os.environ.get(
    'AGENT_EXE_PATH',  # 1. Search for this key name in your .env file
    r'\\fsczmc01\Shared_Install\DMCPatchAgent\dmcpatchagent.exe'  # 2. Default fallback if not found in .env
)


@agent_update_bp.route('/api/agent/version', methods=['GET'])
@login_required
def get_agent_version():
    """Returns the current 'latest' agent version configured on the server."""
    version = os.environ.get('AGENT_VERSION', '')
    exe_exists = os.path.exists(AGENT_EXE_PATH)
    return jsonify({'version': version, 'exe_available': exe_exists})


@agent_update_bp.route('/api/agent/download', methods=['GET'])
def download_agent():
    """Agent downloads the latest exe here. Requires a valid agent token."""
    agent = verify_agent_token(__import__('flask').request)
    if not agent:
        return jsonify({'error': 'Invalid or missing agent token'}), 401

    if not os.path.exists(AGENT_EXE_PATH):
        return jsonify({'error': 'No agent exe available on server'}), 404

    return send_file(
        AGENT_EXE_PATH,
        as_attachment=True,
        download_name='dmcpatchagent_new.exe',
        mimetype='application/octet-stream'
    )
