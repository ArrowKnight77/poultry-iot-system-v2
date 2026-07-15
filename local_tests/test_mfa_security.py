import base64
import secrets
import unittest
from datetime import datetime, timedelta, timezone

import pyotp

from api_avicola.mfa_security import (
    MFAConfigurationError,
    MFASecretError,
    MFAService,
)


class MFAServiceTests(unittest.TestCase):
    def setUp(self):
        key = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii")
        self.service = MFAService(
            encryption_key=key,
            issuer_name="Poultry IoT Test",
            valid_window=1,
        )
        self.secret = self.service.generate_secret()
        self.now = datetime(2026, 7, 14, 18, 30, 15, tzinfo=timezone.utc)

    def test_rejects_invalid_encryption_key(self):
        with self.assertRaises(MFAConfigurationError):
            MFAService("not-a-fernet-key", "Poultry IoT Test")

    def test_encrypts_and_decrypts_secret(self):
        encrypted = self.service.encrypt_secret(self.secret)

        self.assertNotEqual(encrypted, self.secret)
        self.assertEqual(self.service.decrypt_secret(encrypted), self.secret)

    def test_rejects_tampered_ciphertext(self):
        encrypted = self.service.encrypt_secret(self.secret)
        tampered = f"{encrypted[:-1]}A"

        with self.assertRaises(MFASecretError):
            self.service.decrypt_secret(tampered)

    def test_builds_parseable_totp_provisioning_uri(self):
        uri = self.service.provisioning_uri(self.secret, "admin_test")
        parsed = pyotp.parse_uri(uri)

        self.assertEqual(parsed.secret, self.secret)
        self.assertEqual(parsed.issuer, "Poultry IoT Test")
        self.assertEqual(parsed.name, "admin_test")

    def test_accepts_current_code_and_rejects_replay(self):
        code = pyotp.TOTP(self.secret).at(self.now)
        matched_step = self.service.verify_code(
            self.secret,
            code,
            now=self.now,
        )

        self.assertIsNotNone(matched_step)
        self.assertIsNone(
            self.service.verify_code(
                self.secret,
                code,
                now=self.now,
                last_verified_at=matched_step,
            )
        )

    def test_accepts_previous_window_once(self):
        previous_time = self.now - timedelta(seconds=30)
        code = pyotp.TOTP(self.secret).at(previous_time)

        matched_step = self.service.verify_code(
            self.secret,
            code,
            now=self.now,
        )

        self.assertIsNotNone(matched_step)
        self.assertLess(matched_step, self.now.replace(tzinfo=None))

    def test_rejects_invalid_code_formats(self):
        for code in ("", "12345", "1234567", "abcdef", "12 456"):
            with self.subTest(code=code):
                self.assertIsNone(
                    self.service.verify_code(
                        self.secret,
                        code,
                        now=self.now,
                    )
                )


if __name__ == "__main__":
    unittest.main()
