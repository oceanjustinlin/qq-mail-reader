"""Secure local storage for QQ Mail credentials."""

import json
import os
import re
import secrets
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import keyring
from keyring.errors import KeyringError

from .errors import ConfigurationError

SERVICE_NAME = "qq-mail-reader"
DEFAULT_CONFIG_PATH = Path.home() / "Library" / "Application Support" / SERVICE_NAME / "config.json"

_EMAIL_PATTERN = re.compile(
    r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+"
    r"(?:\.[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+)*@"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)+"
)


@dataclass(frozen=True)
class Credentials:
    """QQ Mail login address and authorization code."""

    email: str
    auth_code: str = field(repr=False)


class KeyringBackend(Protocol):
    """Minimal keyring interface used for credential storage."""

    def set_password(self, service_name: str, username: str, password: str) -> None:
        """Store a password."""

    def get_password(self, service_name: str, username: str) -> str | None:
        """Retrieve a password, if present."""


def validate_email(email: str) -> str:
    """Validate and return a complete email address."""
    if not isinstance(email, str) or not _EMAIL_PATTERN.fullmatch(email):
        raise ConfigurationError("QQ Mail email address is invalid.")
    return email


def mask_email(email: str) -> str:
    """Return an email address suitable for safe display."""
    local, separator, domain = email.partition("@")
    if not separator or not local or not domain:
        return "***"
    if len(local) <= 4:
        masked_local = f"{local[0]}***"
    else:
        masked_local = f"{local[:2]}***{local[-2:]}"
    return f"{masked_local}@{domain}"


class CredentialStore:
    """Load and persist QQ Mail credentials without writing secrets to disk."""

    def __init__(
        self,
        config_path: Path | None = None,
        environ: Mapping[str, str] | None = None,
        keyring_backend: KeyringBackend = keyring,
    ) -> None:
        self.config_path = config_path or DEFAULT_CONFIG_PATH
        self.environ = environ if environ is not None else os.environ
        self.keyring_backend = keyring_backend

    def load(self) -> Credentials:
        """Load credentials from a complete environment pair or saved storage."""
        environment_credentials = self._environment_credentials()
        if environment_credentials is not None:
            return environment_credentials

        email = self._load_saved_email()
        if email is None:
            raise ConfigurationError("QQ Mail credentials are not configured.")
        try:
            auth_code = self.keyring_backend.get_password(SERVICE_NAME, email)
        except KeyringError:
            raise ConfigurationError("QQ Mail credential storage is unavailable.") from None
        if not auth_code:
            raise ConfigurationError("QQ Mail authorization code is not configured.")
        return Credentials(email=email, auth_code=auth_code)

    def save(self, email: str, auth_code: str) -> None:
        """Save an email locally and its authorization code in the keyring."""
        email = validate_email(email)
        if not isinstance(auth_code, str) or not auth_code.strip():
            raise ConfigurationError("QQ Mail authorization code is required.")
        try:
            self.keyring_backend.set_password(SERVICE_NAME, email, auth_code)
        except KeyringError:
            raise ConfigurationError("QQ Mail credential storage is unavailable.") from None
        self._write_saved_email(email)

    def configured_email(self) -> str | None:
        """Return the configured email address without reading its secret."""
        environment_credentials = self._environment_credentials()
        if environment_credentials is not None:
            return environment_credentials.email
        return self._load_saved_email(allow_missing=True)

    def _environment_credentials(self) -> Credentials | None:
        email = self.environ.get("QQ_MAIL_ADDRESS")
        auth_code = self.environ.get("QQ_MAIL_AUTH_CODE")
        if not email or not auth_code:
            return None
        return Credentials(email=validate_email(email), auth_code=auth_code)

    def _load_saved_email(self, *, allow_missing: bool = False) -> str | None:
        try:
            contents = self.config_path.read_text(encoding="utf-8")
            data = json.loads(contents)
        except FileNotFoundError as error:
            if allow_missing:
                return None
            raise ConfigurationError("QQ Mail credentials are not configured.") from error
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            raise ConfigurationError("QQ Mail credential configuration is invalid.") from None
        if not isinstance(data, dict) or not isinstance(data.get("email"), str):
            raise ConfigurationError("QQ Mail credential configuration is invalid.")
        try:
            return validate_email(data["email"])
        except ConfigurationError as error:
            raise ConfigurationError("QQ Mail credential configuration is invalid.") from error

    def _write_saved_email(self, email: str) -> None:
        directory = self.config_path.parent
        temporary_path: Path | None = None
        try:
            directory.mkdir(parents=True, exist_ok=True)
            directory.chmod(0o700)
            temporary_path = directory / f".{self.config_path.name}.{secrets.token_hex(16)}.tmp"
            descriptor = os.open(temporary_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as temporary_file:
                json.dump({"email": email}, temporary_file)
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            temporary_path.chmod(0o600)
            os.replace(temporary_path, self.config_path)
        except OSError as error:
            raise ConfigurationError("Unable to save QQ Mail credentials.") from error
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass
