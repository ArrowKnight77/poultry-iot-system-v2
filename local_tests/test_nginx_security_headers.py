from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SITE_PATH = ROOT / "deploy/nginx/poultry-api.conf"
SNIPPET_PATH = ROOT / "deploy/nginx/snippets/poultry-security-headers.conf"

EXPECTED_ADD_HEADERS = (
    'add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;',
    'add_header X-Content-Type-Options "nosniff" always;',
    'add_header X-Frame-Options "SAMEORIGIN" always;',
    'add_header X-XSS-Protection "0" always;',
    'add_header Referrer-Policy "strict-origin-when-cross-origin" always;',
    'add_header Permissions-Policy "geolocation=(), camera=(), microphone=(), payment=(), usb=()" always;',
    "add_header Content-Security-Policy \"object-src 'none'; base-uri 'self'; frame-ancestors 'self'; form-action 'self'; upgrade-insecure-requests\" always;",
)

MANAGED_HEADERS = (
    "Strict-Transport-Security",
    "Content-Security-Policy",
    "X-Content-Type-Options",
    "X-Frame-Options",
    "X-XSS-Protection",
    "Referrer-Policy",
    "Permissions-Policy",
)


def server_blocks(config):
    return config.split("server {")[1:]


class NginxSecurityHeaderPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.site = SITE_PATH.read_text(encoding="utf-8")
        cls.snippet = SNIPPET_PATH.read_text(encoding="utf-8")

    def test_https_server_includes_security_policy_once(self):
        blocks = server_blocks(self.site)
        self.assertEqual(len(blocks), 2)
        https_block, http_block = blocks

        self.assertIn("listen 443 ssl;", https_block)
        self.assertEqual(
            https_block.count(
                "include /etc/nginx/snippets/poultry-security-headers.conf;"
            ),
            1,
        )
        self.assertNotIn("poultry-security-headers.conf", http_block)

    def test_http_server_redirects_without_hsts_policy(self):
        _, http_block = server_blocks(self.site)
        self.assertIn("listen 80;", http_block)
        self.assertIn("return 301 https://$host$request_uri;", http_block)
        self.assertIn("server_tokens off;", http_block)
        self.assertNotIn("Strict-Transport-Security", http_block)

    def test_proxy_routes_are_preserved(self):
        expected_routes = (
            "proxy_pass http://127.0.0.1:5000/api/;",
            "proxy_pass http://127.0.0.1:5000/lecturas;",
            "proxy_pass http://127.0.0.1:5001/;",
        )
        for route in expected_routes:
            with self.subTest(route=route):
                self.assertEqual(self.site.count(route), 1)

    def test_security_events_browser_request_uses_dashboard_session_proxy(self):
        https_block, _ = server_blocks(self.site)
        exact_location = "location = /api/security-events {"
        dashboard_proxy = (
            "proxy_pass http://127.0.0.1:5001/api/security-events;"
        )

        self.assertEqual(https_block.count(exact_location), 1)
        self.assertEqual(https_block.count(dashboard_proxy), 1)
        self.assertLess(
            https_block.index(exact_location),
            https_block.index("location /api/ {"),
        )
        self.assertNotIn("location = /api/security-events/ingest", https_block)

    def test_every_security_header_is_canonical_and_always_enabled(self):
        for directive in EXPECTED_ADD_HEADERS:
            with self.subTest(directive=directive):
                self.assertEqual(self.snippet.count(directive), 1)
                self.assertTrue(directive.endswith(" always;"))

    def test_upstream_managed_headers_are_hidden(self):
        for header in MANAGED_HEADERS:
            directive = f"proxy_hide_header {header};"
            with self.subTest(header=header):
                self.assertEqual(self.snippet.count(directive), 1)

    def test_hsts_is_strong_but_not_preloaded(self):
        self.assertIn("max-age=31536000; includeSubDomains", self.snippet)
        self.assertNotIn("preload", self.snippet.lower())

    def test_nginx_version_is_not_exposed(self):
        self.assertIn("server_tokens off;", self.snippet)

    def test_no_embedded_private_material(self):
        combined = f"{self.site}\n{self.snippet}"
        self.assertNotIn("BEGIN PRIVATE KEY", combined)
        self.assertNotIn("BEGIN CERTIFICATE", combined)
        self.assertIn(
            "/etc/letsencrypt/live/poultry-system.duckdns.org/privkey.pem",
            self.site,
        )

    def test_ci_nginx_image_is_pinned_by_digest(self):
        workflow = (
            ROOT / ".github/workflows/nginx-security-headers.yml"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "nginx:1.24.0-alpine@sha256:"
            "77e5d4a6ad906c5d3793764085706577fa705b1dc6f244ea0241c4b5e2155385",
            workflow,
        )


if __name__ == "__main__":
    unittest.main()
