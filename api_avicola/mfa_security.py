import re
from datetime import datetime, timezone

import pyotp
from cryptography.fernet import Fernet, InvalidToken


TOTP_CODE_PATTERN = re.compile(r"^[0-9]{6}$")
TOTP_SECRET_PATTERN = re.compile(r"^[A-Z2-7]{32,}$")


class MFAConfigurationError(ValueError):
    """Raised when MFA cannot start with the supplied configuration."""


class MFASecretError(ValueError):
    """Raised when an encrypted MFA secret cannot be safely used."""


class MFAService:
    def __init__(self, encryption_key, issuer_name, valid_window=1):
        normalized_key = (encryption_key or "").strip()
        normalized_issuer = (issuer_name or "").strip()

        if not normalized_key:
            raise MFAConfigurationError("MFA_ENCRYPTION_KEY no definida.")

        if not normalized_issuer:
            raise MFAConfigurationError("MFA_ISSUER no definido.")

        if not isinstance(valid_window, int) or not 0 <= valid_window <= 2:
            raise MFAConfigurationError("Ventana TOTP fuera del rango permitido.")

        try:
            self._fernet = Fernet(normalized_key.encode("ascii"))
        except (TypeError, ValueError, UnicodeEncodeError) as exc:
            raise MFAConfigurationError(
                "MFA_ENCRYPTION_KEY no es una llave Fernet válida."
            ) from exc

        self.issuer_name = normalized_issuer[:80]
        self.valid_window = valid_window

    @staticmethod
    def generate_secret():
        return pyotp.random_base32()

    @staticmethod
    def _validate_secret(secret):
        normalized_secret = (secret or "").strip().upper()

        if not TOTP_SECRET_PATTERN.fullmatch(normalized_secret):
            raise MFASecretError("Secreto TOTP inválido.")

        return normalized_secret

    def encrypt_secret(self, secret):
        normalized_secret = self._validate_secret(secret)
        return self._fernet.encrypt(
            normalized_secret.encode("ascii")
        ).decode("ascii")

    def decrypt_secret(self, encrypted_secret):
        if not isinstance(encrypted_secret, str) or not encrypted_secret.strip():
            raise MFASecretError("Secreto MFA cifrado ausente.")

        try:
            secret = self._fernet.decrypt(
                encrypted_secret.strip().encode("ascii")
            ).decode("ascii")
        except (
            InvalidToken,
            TypeError,
            ValueError,
            UnicodeDecodeError,
            UnicodeEncodeError,
        ) as exc:
            raise MFASecretError("No fue posible descifrar el secreto MFA.") from exc

        return self._validate_secret(secret)

    def provisioning_uri(self, secret, account_name):
        normalized_secret = self._validate_secret(secret)
        normalized_account = (account_name or "").strip()

        if not normalized_account:
            raise MFASecretError("La cuenta MFA no puede estar vacía.")

        return pyotp.TOTP(normalized_secret).provisioning_uri(
            name=normalized_account[:120],
            issuer_name=self.issuer_name,
        )

    def verify_code(self, secret, code, now=None, last_verified_at=None):
        normalized_secret = self._validate_secret(secret)
        normalized_code = (code or "").strip()

        if not TOTP_CODE_PATTERN.fullmatch(normalized_code):
            return None

        current_time = now or datetime.now(timezone.utc)
        if current_time.tzinfo is None:
            current_time = current_time.replace(tzinfo=timezone.utc)
        else:
            current_time = current_time.astimezone(timezone.utc)

        previous_step = last_verified_at
        if previous_step and previous_step.tzinfo is not None:
            previous_step = previous_step.astimezone(timezone.utc).replace(
                tzinfo=None
            )

        totp = pyotp.TOTP(normalized_secret)
        base_counter = totp.timecode(current_time)

        for offset in range(-self.valid_window, self.valid_window + 1):
            candidate = totp.at(current_time, counter_offset=offset)

            if not pyotp.utils.strings_equal(normalized_code, candidate):
                continue

            matched_counter = base_counter + offset
            matched_step = datetime.fromtimestamp(
                matched_counter * totp.interval,
                tz=timezone.utc,
            ).replace(tzinfo=None)

            if previous_step and matched_step <= previous_step:
                return None

            return matched_step

        return None
