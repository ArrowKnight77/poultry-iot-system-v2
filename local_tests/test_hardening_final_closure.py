import base64
from contextlib import redirect_stdout
from datetime import datetime
from io import StringIO
import os
from pathlib import Path
import unittest


os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["FLASK_ENV"] = "testing"
os.environ["SECRET_KEY"] = "test-secret-key-0123456789-abcdef"
os.environ["INGEST_API_KEY"] = "test-ingest-key-0123456789-abcdef"
os.environ["SECURITY_EVENTS_API_KEY"] = (
    "test-security-events-key-0123456789-abcdef"
)
os.environ["DASHBOARD_PROXY_KEY"] = (
    "test-dashboard-proxy-key-0123456789-abcdef"
)
os.environ["JWT_SECRET_KEY"] = "test-jwt-secret-key-0123456789-abcdef"
os.environ["MFA_ENCRYPTION_KEY"] = base64.urlsafe_b64encode(
    b"hardening-final-closure-test-000"
).decode("ascii")

with redirect_stdout(StringIO()):
    from api_avicola import api
    from dashboard_avicola import dashboard


ROOT = Path(__file__).resolve().parents[1]


class RegistrationAuthorizationTests(unittest.TestCase):
    def setUp(self):
        self.client = api.app.test_client()

        with api.app.app_context():
            api.db.drop_all()
            api.db.create_all()

            self.admin = api.User(
                username="closure_admin",
                full_name="Closure Admin",
                role="admin",
                initials="CA",
                mfa_enabled=True,
                mfa_secret_encrypted="encrypted-test-secret",
                mfa_enrolled_at=datetime.utcnow(),
            )
            self.admin.set_password("closure-admin-password")
            self.viewer = api.User(
                username="closure_viewer",
                full_name="Closure Viewer",
                role="visor",
                initials="CV",
            )
            self.viewer.set_password("closure-viewer-password")
            api.db.session.add_all([self.admin, self.viewer])
            api.db.session.commit()
            self.admin_id = self.admin.id
            self.viewer_id = self.viewer.id

    def token_for(self, user_id, mfa_verified=False):
        with api.app.app_context():
            user = api.db.session.get(api.User, user_id)
            return api.generate_jwt_token(
                user,
                mfa_verified=mfa_verified,
            )

    def test_registration_requires_authentication(self):
        response = self.client.post(
            "/api/register",
            json={"username": "blocked", "password": "not-created"},
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.get_json()["error"], "Token requerido.")

    def test_registration_rejects_non_admin_user(self):
        response = self.client.post(
            "/api/register",
            headers={
                "Authorization": f"Bearer {self.token_for(self.viewer_id)}"
            },
            json={"username": "blocked", "password": "not-created"},
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json()["error"], "Permisos insuficientes.")

    def test_verified_admin_can_create_user_and_action_is_audited(self):
        response = self.client.post(
            "/api/register",
            headers={
                "Authorization": (
                    f"Bearer {self.token_for(self.admin_id, mfa_verified=True)}"
                )
            },
            json={
                "username": "closure_operator",
                "password": "closure-operator-password",
                "full_name": "Closure Operator",
                "role": "operador",
            },
        )

        self.assertEqual(response.status_code, 201)

        with api.app.app_context():
            created = api.User.query.filter_by(
                username="closure_operator"
            ).one()
            event = api.SecurityEvent.query.filter_by(
                event_type="privileged_action"
            ).order_by(api.SecurityEvent.id.desc()).first()

            self.assertEqual(created.role, "operador")
            self.assertIsNotNone(event)
            self.assertEqual(event.actor_username, "closure_admin")


class ErrorRedactionAndRateLimitTests(unittest.TestCase):
    def test_api_internal_error_never_returns_exception_text(self):
        secret_detail = "postgresql://user:password@private-db/internal"

        with api.app.test_request_context("/lecturas"):
            try:
                raise RuntimeError(secret_detail)
            except RuntimeError:
                response, status = api.internal_error_response()

        self.assertEqual(status, 500)
        self.assertEqual(response.get_json(), {
            "error": "Error interno del servidor."
        })
        self.assertNotIn(secret_detail, response.get_data(as_text=True))

    def test_dashboard_internal_error_never_returns_exception_text(self):
        secret_detail = "https://internal-api:5000/private"

        with dashboard.app.test_request_context("/api/live-data"):
            try:
                raise RuntimeError(secret_detail)
            except RuntimeError:
                response, status = dashboard._dashboard_internal_error()

        self.assertEqual(status, 500)
        self.assertEqual(response.get_json(), {
            "error": "Error interno del dashboard."
        })
        self.assertNotIn(secret_detail, response.get_data(as_text=True))

    def test_live_data_has_effective_specific_rate_limit(self):
        client = api.app.test_client()
        responses = [client.get("/api/live-data") for _ in range(121)]

        self.assertEqual(responses[0].status_code, 200)
        self.assertEqual(responses[-1].status_code, 429)


class FinalClosureStaticTests(unittest.TestCase):
    def test_exception_details_and_development_secret_are_absent(self):
        api_source = (ROOT / "api_avicola/api.py").read_text(encoding="utf-8")
        dashboard_source = (
            ROOT / "dashboard_avicola/dashboard.py"
        ).read_text(encoding="utf-8")

        self.assertNotIn("str(e)", api_source)
        self.assertNotIn("str(e)", dashboard_source)
        self.assertNotIn("dev-secret-key", dashboard_source)

    def test_polling_endpoints_are_not_exempt(self):
        api_source = (ROOT / "api_avicola/api.py").read_text(encoding="utf-8")

        for limit_name in (
            "LIVE_DATA_RATE_LIMIT",
            "HISTORICAL_DATA_RATE_LIMIT",
            "ALERT_QUERY_RATE_LIMIT",
            "PARVADA_QUERY_RATE_LIMIT",
        ):
            with self.subTest(limit_name=limit_name):
                self.assertIn(f"@limiter.limit({limit_name})", api_source)

        self.assertNotIn(
            "@limiter.exempt  # Exempt from rate limiting because it's polled frequently",
            api_source,
        )


if __name__ == "__main__":
    unittest.main()
