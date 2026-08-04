import base64
from contextlib import redirect_stdout
from io import StringIO
import os
import unittest
from unittest.mock import patch


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
    b"container-hardening-test-key-000"
).decode("ascii")

with redirect_stdout(StringIO()):
    from api_avicola import api
    from dashboard_avicola import dashboard


class APIHealthTests(unittest.TestCase):
    def setUp(self):
        self.client = api.app.test_client()

    def test_health_returns_ok_when_database_query_succeeds(self):
        with patch.object(api.db.session, "execute") as execute:
            response = self.client.get("/api/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"status": "ok"})
        execute.assert_called_once()

    def test_health_returns_503_without_error_details(self):
        with (
            patch.object(
                api.db.session,
                "execute",
                side_effect=RuntimeError("sensitive database error"),
            ),
            patch.object(api.db.session, "rollback") as rollback,
        ):
            response = self.client.get("/api/health")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.get_json(), {"status": "unavailable"})
        self.assertNotIn("sensitive", response.get_data(as_text=True))
        rollback.assert_called_once_with()


class DashboardHealthTests(unittest.TestCase):
    def test_health_returns_ok_without_authentication(self):
        response = dashboard.app.test_client().get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"status": "ok"})


if __name__ == "__main__":
    unittest.main()
