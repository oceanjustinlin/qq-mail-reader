"""Tests for local QQ Mail credential storage."""

import json
import stat

import keyring.errors
import pytest

from qq_mail_reader.credentials import Credentials, CredentialStore, mask_email, validate_email
from qq_mail_reader.errors import ConfigurationError


class FakeKeyring:
    """In-memory keyring used to keep credential tests isolated."""

    def __init__(self) -> None:
        self.passwords: dict[tuple[str, str], str] = {}

    def set_password(self, service: str, username: str, password: str) -> None:
        self.passwords[(service, username)] = password

    def get_password(self, service: str, username: str) -> str | None:
        return self.passwords.get((service, username))


def make_store(tmp_path, *, environ=None, keyring_backend=None) -> CredentialStore:
    return CredentialStore(
        config_path=tmp_path / "credentials" / "config.json",
        environ={} if environ is None else environ,
        keyring_backend=keyring_backend or FakeKeyring(),
    )


def test_complete_environment_pair_overrides_saved_credentials(tmp_path) -> None:
    keyring = FakeKeyring()
    store = make_store(
        tmp_path,
        environ={"QQ_MAIL_ADDRESS": "env@example.com", "QQ_MAIL_AUTH_CODE": "env-code"},
        keyring_backend=keyring,
    )
    store.save("saved@example.com", "saved-code")

    assert store.load().email == "env@example.com"
    assert store.load().auth_code == "env-code"


def test_incomplete_environment_pair_falls_back_to_saved_credentials(tmp_path) -> None:
    store = make_store(
        tmp_path,
        environ={"QQ_MAIL_ADDRESS": "env@example.com"},
    )
    store.save("saved@example.com", "saved-code")

    assert store.load().email == "saved@example.com"
    assert store.load().auth_code == "saved-code"


def test_save_stores_only_email_in_private_config_and_secret_in_keyring(tmp_path) -> None:
    keyring = FakeKeyring()
    store = make_store(tmp_path, keyring_backend=keyring)
    store.save("reader@example.com", "secret-code")
    config_path = tmp_path / "credentials" / "config.json"

    assert json.loads(config_path.read_text()) == {"email": "reader@example.com"}
    assert keyring.get_password("qq-mail-reader", "reader@example.com") == "secret-code"
    assert stat.S_IMODE(config_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(config_path.parent.stat().st_mode) == 0o700


def test_missing_config_raises_safe_configuration_error(tmp_path) -> None:
    store = make_store(tmp_path)

    with pytest.raises(ConfigurationError, match="not configured"):
        store.load()


def test_missing_keyring_secret_raises_safe_configuration_error(tmp_path) -> None:
    store = make_store(tmp_path)
    config_path = tmp_path / "credentials" / "config.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(json.dumps({"email": "reader@example.com"}))

    with pytest.raises(ConfigurationError, match="authorization code is not configured"):
        store.load()


class FailingKeyring(FakeKeyring):
    def get_password(self, service: str, username: str) -> str | None:
        raise keyring.errors.KeyringError("backend leaked secret-code")


def test_keyring_errors_do_not_expose_backend_details_or_secrets(tmp_path) -> None:
    store = make_store(tmp_path, keyring_backend=FailingKeyring())
    config_path = tmp_path / "credentials" / "config.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(json.dumps({"email": "reader@example.com"}))

    with pytest.raises(ConfigurationError) as error:
        store.load()

    assert "secret-code" not in str(error.value)
    assert "backend leaked" not in str(error.value)
    assert error.value.__cause__ is None


def test_save_rejects_empty_auth_code(tmp_path) -> None:
    with pytest.raises(ConfigurationError, match="authorization code"):
        make_store(tmp_path).save("reader@example.com", "")


def test_credentials_repr_and_str_do_not_expose_auth_code() -> None:
    value = Credentials("user@qq.com", "top-secret")

    assert "top-secret" not in repr(value)
    assert "top-secret" not in str(value)


@pytest.mark.parametrize(
    "email",
    [
        "missing-at",
        "@example.com",
        "reader@",
        "a\x00b@example.com",
        "a@.example.com",
        "a@example..com",
        ".abc@example.com",
        "abc.@example.com",
        "a@-example.com",
        "a@example-.com",
        "abc@examplecom",
    ],
)
def test_validate_email_rejects_invalid_addresses(email: str) -> None:
    with pytest.raises(ConfigurationError, match="email address"):
        validate_email(email)


@pytest.mark.parametrize(
    "email",
    ["reader@example.com", "reader@qq.com", "reader@vip.qq.com", "reader@foxmail.com"],
)
def test_validate_email_accepts_normal_mail_addresses(email: str) -> None:
    assert validate_email(email) == email


def test_mask_email_preserves_edges_of_normal_local_part() -> None:
    assert mask_email("12345678@qq.com") == "12***78@qq.com"


def test_mask_email_does_not_preserve_a_four_character_local_part() -> None:
    assert mask_email("abcd@qq.com") == "a***@qq.com"


@pytest.mark.parametrize("email", ["a@qq.com", "ab@qq.com", "abc@qq.com"])
def test_mask_email_safely_masks_short_local_parts(email: str) -> None:
    masked = mask_email(email)

    assert masked.endswith("@qq.com")
    assert "***" in masked
    assert masked != email


def test_configured_email_only_uses_environment_for_complete_pair(tmp_path) -> None:
    store = make_store(
        tmp_path,
        environ={"QQ_MAIL_ADDRESS": "env@example.com", "QQ_MAIL_AUTH_CODE": "env-code"},
    )
    assert store.configured_email() == "env@example.com"

    incomplete_store = make_store(
        tmp_path,
        environ={"QQ_MAIL_ADDRESS": "env@example.com"},
    )
    incomplete_store.save("saved@example.com", "saved-code")
    assert incomplete_store.configured_email() == "saved@example.com"

    absent_store = make_store(tmp_path / "absent")
    assert absent_store.configured_email() is None


def test_configured_email_returns_none_for_missing_read_without_exists_probe(tmp_path) -> None:
    path_type = type(tmp_path)

    class MissingReadPath(path_type):
        def read_text(self, *args, **kwargs) -> str:
            raise FileNotFoundError("missing")

        def exists(self) -> bool:
            raise AssertionError("configured_email must not probe exists after a missing read")

    config_path = MissingReadPath(tmp_path / "credentials" / "config.json")
    store = CredentialStore(config_path=config_path, environ={}, keyring_backend=FakeKeyring())

    assert store.configured_email() is None


def test_configured_email_maps_inaccessible_config_to_safe_error_without_exists_probe(
    tmp_path,
) -> None:
    path_type = type(tmp_path)

    class InaccessiblePath(path_type):
        def read_text(self, *args, **kwargs) -> str:
            raise PermissionError("permission details")

        def exists(self) -> bool:
            raise AssertionError("configured_email must not probe exists after a failed read")

    config_path = InaccessiblePath(tmp_path / "credentials" / "config.json")
    store = CredentialStore(config_path=config_path, environ={}, keyring_backend=FakeKeyring())

    with pytest.raises(ConfigurationError, match="invalid") as error:
        store.configured_email()

    assert "permission details" not in str(error.value)


def test_configured_email_maps_corrupt_config_to_safe_error_without_exists_probe(tmp_path) -> None:
    path_type = type(tmp_path)

    class CorruptPath(path_type):
        def read_text(self, *args, **kwargs) -> str:
            return "not-json"

        def exists(self) -> bool:
            raise AssertionError("configured_email must not probe exists after corrupt data")

    config_path = CorruptPath(tmp_path / "credentials" / "config.json")
    store = CredentialStore(config_path=config_path, environ={}, keyring_backend=FakeKeyring())

    with pytest.raises(ConfigurationError, match="invalid"):
        store.configured_email()


def test_failed_atomic_replace_preserves_existing_config_and_cleans_temp(
    tmp_path, monkeypatch
) -> None:
    store = make_store(tmp_path)
    config_path = tmp_path / "credentials" / "config.json"
    store.save("old@example.com", "old-code")

    def fail_replace(source, destination) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr("qq_mail_reader.credentials.os.replace", fail_replace)

    with pytest.raises(ConfigurationError, match="Unable to save"):
        store.save("new@example.com", "new-code")

    assert json.loads(config_path.read_text()) == {"email": "old@example.com"}
    assert list(config_path.parent.glob("*.tmp")) == []


def test_malformed_config_raises_configuration_error(tmp_path) -> None:
    store = make_store(tmp_path)
    config_path = tmp_path / "credentials" / "config.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text("not-json")

    with pytest.raises(ConfigurationError, match="invalid"):
        store.load()


def test_invalid_utf8_config_raises_safe_configuration_error(tmp_path) -> None:
    store = make_store(tmp_path)
    config_path = tmp_path / "credentials" / "config.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_bytes(b"\xff")

    with pytest.raises(ConfigurationError) as error:
        store.load()

    assert "invalid" in str(error.value)
    assert "utf-8" not in str(error.value)
    assert error.value.__cause__ is None
