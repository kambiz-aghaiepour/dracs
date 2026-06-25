"""Google OAuth2 authentication helpers for DRACS."""

import json
import os


def _load_client_config():
    """Load and validate the Google OAuth2 client secret JSON file."""
    path = os.environ.get(
        "GOOGLE_CLIENT_SECRET_PATH",
        os.path.join("/etc/dracs", "google_client_secret.json"),
    )
    try:
        with open(path) as f:
            config = json.load(f)
        if "web" not in config:
            return None
        required = {"client_id", "client_secret", "auth_uri", "token_uri"}
        if not required.issubset(config["web"]):
            return None
        return config
    except (OSError, json.JSONDecodeError):
        return None


def is_enabled():
    """Return True if Google OAuth2 is enabled and the client secret is valid."""
    if os.environ.get("GOOGLE_AUTH", "false").lower() not in ("true", "1", "yes"):
        return False
    return _load_client_config() is not None


def make_flow(redirect_uri, state=None):
    """Create a google_auth_oauthlib Flow for the authorization code grant."""
    from google_auth_oauthlib.flow import Flow

    config = _load_client_config()
    if config is None:
        raise RuntimeError("Google OAuth2 client secret is not configured")

    scopes = [
        "openid",
        "https://www.googleapis.com/auth/userinfo.email",
    ]
    flow = Flow.from_client_config(config, scopes=scopes, state=state)
    flow.redirect_uri = redirect_uri
    return flow


def get_verified_email(credentials):
    """Return the verified email address from Google credentials, or None."""
    from google.oauth2 import id_token
    from google.auth.transport import requests as google_requests

    config = _load_client_config()
    if config is None:
        return None
    client_id = config["web"]["client_id"]
    try:
        id_info = id_token.verify_oauth2_token(
            credentials.id_token,
            google_requests.Request(),
            audience=client_id,
        )
        if not id_info.get("email_verified"):
            return None
        return id_info.get("email")
    except Exception:
        return None
