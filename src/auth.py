import hashlib
import os
import secrets
from functools import wraps
from flask import request, session, redirect, url_for, jsonify


def _hash_password(password: str) -> str:
    """SHA-256 hash with fixed prefix. Sufficient for a local-network app."""
    return hashlib.sha256(f"emptyarr:{password}".encode()).hexdigest()


def _get_credentials(config=None):
    """
    Resolution order:
    1. EMPTYARR_USERNAME / EMPTYARR_PASSWORD env vars (backward compat)
    2. config.auth_username / config.auth_password_hash (set via Settings UI)
    Returns (username, password_hash) or (None, None)
    """
    env_user = os.environ.get("EMPTYARR_USERNAME", "")
    env_pass = os.environ.get("EMPTYARR_PASSWORD", "")
    if env_user and env_pass:
        return env_user, _hash_password(env_pass)

    if config and getattr(config, "auth_username", "") and getattr(config, "auth_password_hash", ""):
        return config.auth_username, config.auth_password_hash

    return None, None


def auth_enabled(config=None) -> bool:
    u, _ = _get_credentials(config)
    return bool(u)


def check_credentials(username: str, password: str, config=None) -> bool:
    u, ph = _get_credentials(config)
    if not u:
        return True
    return (secrets.compare_digest(username, u) and
            secrets.compare_digest(_hash_password(password), ph))


def hash_password(password: str) -> str:
    """Public export for use when saving credentials."""
    return _hash_password(password)


def is_authenticated() -> bool:
    return session.get("authenticated") is True


def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        from app import config as _config
        if not auth_enabled(_config):
            return f(*args, **kwargs)
        if not is_authenticated():
            if request.path.startswith("/api/"):
                return jsonify({"error": "Unauthorized"}), 401
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated