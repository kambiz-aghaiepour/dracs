"""Tests for Google OAuth2 authentication (google_auth module + webapp routes)."""

import json
import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from dracs.db import db_initialize

# --------------------------------------------------------------------------- #
# google_auth module unit tests                                                 #
# --------------------------------------------------------------------------- #


class TestLoadClientConfig:
    def test_missing_file_returns_none(self, tmp_path):
        with patch.dict(
            os.environ,
            {"GOOGLE_CLIENT_SECRET_PATH": str(tmp_path / "missing.json")},
        ):
            from dracs.google_auth import _load_client_config

            assert _load_client_config() is None

    def test_bad_json_returns_none(self, tmp_path):
        f = tmp_path / "bad.json"
        f.write_text("not valid json")
        with patch.dict(os.environ, {"GOOGLE_CLIENT_SECRET_PATH": str(f)}):
            from dracs.google_auth import _load_client_config

            assert _load_client_config() is None

    def test_missing_web_key_returns_none(self, tmp_path):
        f = tmp_path / "installed.json"
        f.write_text(json.dumps({"installed": {"client_id": "x"}}))
        with patch.dict(os.environ, {"GOOGLE_CLIENT_SECRET_PATH": str(f)}):
            from dracs.google_auth import _load_client_config

            assert _load_client_config() is None

    def test_missing_required_field_returns_none(self, tmp_path):
        f = tmp_path / "partial.json"
        f.write_text(json.dumps({"web": {"client_id": "x"}}))
        with patch.dict(os.environ, {"GOOGLE_CLIENT_SECRET_PATH": str(f)}):
            from dracs.google_auth import _load_client_config

            assert _load_client_config() is None

    def test_valid_config_returns_dict(self, tmp_path):
        cfg = {
            "web": {
                "client_id": "id123",
                "client_secret": "sec",
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        }
        f = tmp_path / "good.json"
        f.write_text(json.dumps(cfg))
        with patch.dict(os.environ, {"GOOGLE_CLIENT_SECRET_PATH": str(f)}):
            from dracs.google_auth import _load_client_config

            result = _load_client_config()
        assert result is not None
        assert result["web"]["client_id"] == "id123"


class TestIsEnabled:
    def test_disabled_by_default(self, tmp_path):
        env = {k: v for k, v in os.environ.items() if k not in ("GOOGLE_AUTH",)}
        env["GOOGLE_CLIENT_SECRET_PATH"] = str(tmp_path / "missing.json")
        with patch.dict(os.environ, env, clear=True):
            from dracs.google_auth import is_enabled

            assert is_enabled() is False

    def test_disabled_explicitly(self, tmp_path):
        with patch.dict(
            os.environ,
            {
                "GOOGLE_AUTH": "false",
                "GOOGLE_CLIENT_SECRET_PATH": str(tmp_path / "missing.json"),
            },
        ):
            from dracs.google_auth import is_enabled

            assert is_enabled() is False

    def test_enabled_but_no_file(self, tmp_path):
        with patch.dict(
            os.environ,
            {
                "GOOGLE_AUTH": "true",
                "GOOGLE_CLIENT_SECRET_PATH": str(tmp_path / "missing.json"),
            },
        ):
            from dracs.google_auth import is_enabled

            assert is_enabled() is False

    def test_enabled_with_valid_config(self, tmp_path):
        cfg = {
            "web": {
                "client_id": "id",
                "client_secret": "sec",
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        }
        f = tmp_path / "secret.json"
        f.write_text(json.dumps(cfg))
        with patch.dict(
            os.environ,
            {"GOOGLE_AUTH": "true", "GOOGLE_CLIENT_SECRET_PATH": str(f)},
        ):
            from dracs.google_auth import is_enabled

            assert is_enabled() is True


class TestMakeFlow:
    def test_raises_when_not_configured(self, tmp_path):
        with patch.dict(
            os.environ,
            {"GOOGLE_CLIENT_SECRET_PATH": str(tmp_path / "missing.json")},
        ):
            from dracs.google_auth import make_flow

            with pytest.raises(RuntimeError, match="not configured"):
                make_flow("https://example.com/callback")

    def test_returns_flow_with_redirect_uri(self, tmp_path):
        cfg = {
            "web": {
                "client_id": "id",
                "client_secret": "sec",
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        }
        f = tmp_path / "secret.json"
        f.write_text(json.dumps(cfg))
        mock_flow = MagicMock()
        with patch.dict(os.environ, {"GOOGLE_CLIENT_SECRET_PATH": str(f)}):
            with patch(
                "google_auth_oauthlib.flow.Flow.from_client_config",
                return_value=mock_flow,
            ):
                from dracs.google_auth import make_flow

                result = make_flow("https://example.com/cb", state="s1")
        assert result is mock_flow
        assert mock_flow.redirect_uri == "https://example.com/cb"


class TestGetVerifiedEmail:
    def _write_config(self, tmp_path):
        cfg = {
            "web": {
                "client_id": "id123",
                "client_secret": "sec",
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        }
        f = tmp_path / "secret.json"
        f.write_text(json.dumps(cfg))
        return str(f)

    def test_no_config_returns_none(self, tmp_path):
        with patch.dict(
            os.environ,
            {"GOOGLE_CLIENT_SECRET_PATH": str(tmp_path / "missing.json")},
        ):
            from dracs.google_auth import get_verified_email

            assert get_verified_email(MagicMock()) is None

    def test_returns_email_when_verified(self, tmp_path):
        path = self._write_config(tmp_path)
        creds = MagicMock(id_token="tok")
        id_info = {"email_verified": True, "email": "user@example.com"}
        with patch.dict(os.environ, {"GOOGLE_CLIENT_SECRET_PATH": path}):
            with patch(
                "google.oauth2.id_token.verify_oauth2_token", return_value=id_info
            ):
                with patch("google.auth.transport.requests.Request"):
                    from dracs.google_auth import get_verified_email

                    result = get_verified_email(creds)
        assert result == "user@example.com"

    def test_returns_none_when_unverified(self, tmp_path):
        path = self._write_config(tmp_path)
        creds = MagicMock(id_token="tok")
        id_info = {"email_verified": False, "email": "user@example.com"}
        with patch.dict(os.environ, {"GOOGLE_CLIENT_SECRET_PATH": path}):
            with patch(
                "google.oauth2.id_token.verify_oauth2_token", return_value=id_info
            ):
                with patch("google.auth.transport.requests.Request"):
                    from dracs.google_auth import get_verified_email

                    result = get_verified_email(creds)
        assert result is None

    def test_returns_none_on_exception(self, tmp_path):
        path = self._write_config(tmp_path)
        creds = MagicMock(id_token="tok")
        with patch.dict(os.environ, {"GOOGLE_CLIENT_SECRET_PATH": path}):
            with patch(
                "google.oauth2.id_token.verify_oauth2_token",
                side_effect=ValueError("bad token"),
            ):
                with patch("google.auth.transport.requests.Request"):
                    from dracs.google_auth import get_verified_email

                    result = get_verified_email(creds)
        assert result is None


# --------------------------------------------------------------------------- #
# webapp route tests                                                            #
# --------------------------------------------------------------------------- #


@pytest.fixture
def google_webapp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db_initialize(path)
    yield path
    if os.path.exists(path):
        os.unlink(path)


@pytest.fixture
def google_client(google_webapp_db):
    with patch.dict(
        os.environ,
        {
            "DRACS_DB": google_webapp_db,
            "DRACS_DNS_STRING": "mgmt-",
            "DRACS_DNS_MODE": "prefix",
            "DRACS_LOG_DIR": tempfile.mkdtemp(),
        },
    ):
        import dracs.webapp as webapp_mod

        webapp_mod.DB_PATH = google_webapp_db
        webapp_mod.db_initialize(google_webapp_db)
        webapp_mod.app.config["TESTING"] = True
        with webapp_mod.app.test_client() as c:
            yield c


class TestAuthGoogleRoute:
    def test_disabled_redirects_to_index(self, google_client):
        import dracs.webapp as webapp_mod

        with patch.object(webapp_mod, "GOOGLE_AUTH_ENABLED", False):
            resp = google_client.get("/auth/google")
        assert resp.status_code == 302
        assert "Location" in resp.headers

    def test_enabled_redirects_to_google(self, google_client):
        import dracs.webapp as webapp_mod

        mock_flow = MagicMock()
        mock_flow.authorization_url.return_value = (
            "https://accounts.google.com/auth?foo=bar",
            "state123",
        )
        with patch.object(webapp_mod, "GOOGLE_AUTH_ENABLED", True):
            with patch("dracs.google_auth.make_flow", return_value=mock_flow):
                resp = google_client.get("/auth/google")
        assert resp.status_code == 302
        assert "accounts.google.com" in resp.headers["Location"]


class TestAuthGoogleCallbackRoute:
    def _mock_flow(self):
        f = MagicMock()
        f.fetch_token.return_value = None
        return f

    def test_disabled_redirects(self, google_client):
        import dracs.webapp as webapp_mod

        with patch.object(webapp_mod, "GOOGLE_AUTH_ENABLED", False):
            resp = google_client.get("/auth/google/callback?state=x&code=y")
        assert resp.status_code == 302

    def test_no_state_in_session_redirects(self, google_client):
        import dracs.webapp as webapp_mod

        with patch.object(webapp_mod, "GOOGLE_AUTH_ENABLED", True):
            resp = google_client.get("/auth/google/callback?state=abc&code=y")
        assert resp.status_code == 302

    def test_state_mismatch_redirects(self, google_client):
        import dracs.webapp as webapp_mod

        with patch.object(webapp_mod, "GOOGLE_AUTH_ENABLED", True):
            with google_client.session_transaction() as sess:
                sess["google_oauth_state"] = "expected"
            resp = google_client.get("/auth/google/callback?state=wrong&code=y")
        assert resp.status_code == 302

    def test_fetch_token_failure_redirects(self, google_client):
        import dracs.webapp as webapp_mod

        mock_flow = MagicMock()
        mock_flow.fetch_token.side_effect = Exception("token exchange failed")
        with patch.object(webapp_mod, "GOOGLE_AUTH_ENABLED", True):
            with google_client.session_transaction() as sess:
                sess["google_oauth_state"] = "abc"
            with patch("dracs.google_auth.make_flow", return_value=mock_flow):
                resp = google_client.get("/auth/google/callback?state=abc&code=y")
        assert resp.status_code == 302

    def test_no_email_redirects(self, google_client):
        import dracs.webapp as webapp_mod

        with patch.object(webapp_mod, "GOOGLE_AUTH_ENABLED", True):
            with google_client.session_transaction() as sess:
                sess["google_oauth_state"] = "abc"
            with patch("dracs.google_auth.make_flow", return_value=self._mock_flow()):
                with patch("dracs.google_auth.get_verified_email", return_value=None):
                    resp = google_client.get("/auth/google/callback?state=abc&code=y")
        assert resp.status_code == 302

    def test_existing_user_gets_session(self, google_client, google_webapp_db):
        import dracs.webapp as webapp_mod
        from dracs.users import create_user

        create_user("existing", "pw", "user", created_by="test")
        with patch.object(webapp_mod, "GOOGLE_AUTH_ENABLED", True):
            with google_client.session_transaction() as sess:
                sess["google_oauth_state"] = "abc"
            with patch("dracs.google_auth.make_flow", return_value=self._mock_flow()):
                with patch(
                    "dracs.google_auth.get_verified_email",
                    return_value="existing@example.com",
                ):
                    resp = google_client.get("/auth/google/callback?state=abc&code=y")
        assert resp.status_code == 302
        with google_client.session_transaction() as sess:
            assert sess.get("authenticated") is True
            assert sess.get("username") == "existing"
            assert sess.get("role") == "user"

    def test_existing_admin_user_gets_admin_role_in_session(
        self, google_client, google_webapp_db
    ):
        import dracs.webapp as webapp_mod
        from dracs.users import create_user

        create_user("adminuser", "pw", "admin", created_by="test")
        with patch.object(webapp_mod, "GOOGLE_AUTH_ENABLED", True):
            with google_client.session_transaction() as sess:
                sess["google_oauth_state"] = "abc"
            with patch("dracs.google_auth.make_flow", return_value=self._mock_flow()):
                with patch(
                    "dracs.google_auth.get_verified_email",
                    return_value="adminuser@example.com",
                ):
                    resp = google_client.get("/auth/google/callback?state=abc&code=y")
        assert resp.status_code == 302
        with google_client.session_transaction() as sess:
            assert sess.get("authenticated") is True
            assert sess.get("username") == "adminuser"
            assert sess.get("role") == "admin"

    def test_new_user_autocreated(self, google_client, google_webapp_db):
        import dracs.webapp as webapp_mod
        from dracs.users import list_users

        with patch.object(webapp_mod, "GOOGLE_AUTH_ENABLED", True):
            with google_client.session_transaction() as sess:
                sess["google_oauth_state"] = "abc"
            with patch("dracs.google_auth.make_flow", return_value=self._mock_flow()):
                with patch(
                    "dracs.google_auth.get_verified_email",
                    return_value="new@example.com",
                ):
                    resp = google_client.get("/auth/google/callback?state=abc&code=y")
        assert resp.status_code == 302
        assert any(u["username"] == "new" for u in list_users())

    def test_new_user_gets_quads_role_on_quads_site(
        self, google_client, google_webapp_db
    ):
        import dracs.webapp as webapp_mod
        from dracs.users import get_user_role_for_site
        from dracs.db import get_default_site_id

        quads_cfg = {
            "defaults": {"quads_enabled": "true", "quads_url": "http://quads.local"},
            "hosts": {},
        }
        with patch.object(webapp_mod, "GOOGLE_AUTH_ENABLED", True):
            with google_client.session_transaction() as sess:
                sess["google_oauth_state"] = "abc"
            with patch("dracs.google_auth.make_flow", return_value=self._mock_flow()):
                with patch(
                    "dracs.google_auth.get_verified_email",
                    return_value="quads@example.com",
                ):
                    with patch(
                        "dracs.sites.get_site_ini_config", return_value=quads_cfg
                    ):
                        resp = google_client.get(
                            "/auth/google/callback?state=abc&code=y"
                        )
        assert resp.status_code == 302
        site_id = get_default_site_id()
        assert get_user_role_for_site("quads", site_id) == "quads"

    def test_create_user_failure_redirects(self, google_client, google_webapp_db):
        import dracs.webapp as webapp_mod

        with patch.object(webapp_mod, "GOOGLE_AUTH_ENABLED", True):
            with google_client.session_transaction() as sess:
                sess["google_oauth_state"] = "abc"
            with patch("dracs.google_auth.make_flow", return_value=self._mock_flow()):
                with patch(
                    "dracs.google_auth.get_verified_email",
                    return_value="fail@example.com",
                ):
                    with patch(
                        "dracs.webapp.create_user",
                        side_effect=Exception("db error"),
                    ):
                        resp = google_client.get(
                            "/auth/google/callback?state=abc&code=y"
                        )
        assert resp.status_code == 302
        with google_client.session_transaction() as sess:
            assert not sess.get("authenticated")

    def test_user_with_null_global_role_gets_admin_from_site_role(
        self, google_client, google_webapp_db
    ):
        import dracs.webapp as webapp_mod
        from dracs.users import create_user, set_user_site_role
        from dracs.db import get_default_site_id

        create_user("siteadmin", "pw", None, created_by="test")
        set_user_site_role("siteadmin", get_default_site_id(), "admin")
        with patch.object(webapp_mod, "GOOGLE_AUTH_ENABLED", True):
            with google_client.session_transaction() as sess:
                sess["google_oauth_state"] = "abc"
            with patch("dracs.google_auth.make_flow", return_value=self._mock_flow()):
                with patch(
                    "dracs.google_auth.get_verified_email",
                    return_value="siteadmin@example.com",
                ):
                    resp = google_client.get("/auth/google/callback?state=abc&code=y")
        assert resp.status_code == 302
        with google_client.session_transaction() as sess:
            assert sess.get("authenticated") is True
            assert sess.get("username") == "siteadmin"
            assert sess.get("role") == "admin"

    def test_user_with_null_global_role_gets_user_from_site_role(
        self, google_client, google_webapp_db
    ):
        import dracs.webapp as webapp_mod
        from dracs.users import create_user, set_user_site_role
        from dracs.db import get_default_site_id

        create_user("siteuser2", "pw", None, created_by="test")
        set_user_site_role("siteuser2", get_default_site_id(), "user")
        with patch.object(webapp_mod, "GOOGLE_AUTH_ENABLED", True):
            with google_client.session_transaction() as sess:
                sess["google_oauth_state"] = "abc"
            with patch("dracs.google_auth.make_flow", return_value=self._mock_flow()):
                with patch(
                    "dracs.google_auth.get_verified_email",
                    return_value="siteuser2@example.com",
                ):
                    resp = google_client.get("/auth/google/callback?state=abc&code=y")
        assert resp.status_code == 302
        with google_client.session_transaction() as sess:
            assert sess.get("authenticated") is True
            assert sess.get("username") == "siteuser2"
            assert sess.get("role") == "user"


class TestLocalLoginSiteRoleFallback:
    def test_local_login_null_global_role_gets_admin_from_site_role(
        self, google_client, google_webapp_db
    ):
        from dracs.users import create_user, set_user_site_role
        from dracs.db import get_default_site_id

        create_user("siteonly", "correctpw", None, created_by="test")
        set_user_site_role("siteonly", get_default_site_id(), "admin")

        resp = google_client.post(
            "/login",
            json={"username": "siteonly", "password": "correctpw"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        with google_client.session_transaction() as sess:
            assert sess.get("role") == "admin"

    def test_local_login_null_global_role_gets_user_from_site_role(
        self, google_client, google_webapp_db
    ):
        from dracs.users import create_user, set_user_site_role
        from dracs.db import get_default_site_id

        create_user("siteuser", "pw2", None, created_by="test")
        set_user_site_role("siteuser", get_default_site_id(), "user")

        resp = google_client.post(
            "/login",
            json={"username": "siteuser", "password": "pw2"},
        )
        assert resp.status_code == 200
        with google_client.session_transaction() as sess:
            assert sess.get("role") == "user"
