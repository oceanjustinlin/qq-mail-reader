"""Tests for the interactive, local credential setup command."""

import importlib.util
import warnings
from pathlib import Path

import pytest

from qq_mail_reader.credentials import CredentialStore
from qq_mail_reader.errors import ConfigurationError

SCRIPT = Path(__file__).parents[1] / "scripts" / "configure_credentials.py"


def load_script():
    spec = importlib.util.spec_from_file_location("configure_credentials", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_main_uses_hidden_secret_and_never_prints_it(monkeypatch, capsys) -> None:
    module = load_script()
    saved = {}

    class FakeStore:
        def save(self, email: str, auth_code: str) -> None:
            saved.update(email=email, auth_code=auth_code)

    monkeypatch.setattr(module, "CredentialStore", FakeStore)
    monkeypatch.setattr("builtins.input", lambda prompt: "  user@qq.com  ")
    monkeypatch.setattr(module.getpass, "getpass", lambda prompt: "secret-code")

    assert module.main() == 0
    assert saved == {"email": "user@qq.com", "auth_code": "secret-code"}
    output = capsys.readouterr()
    assert "secret-code" not in output.out + output.err
    assert "user@qq.com" not in output.out + output.err
    assert "u***@qq.com" in output.out


@pytest.mark.parametrize(
    ("email", "auth_code"),
    [
        ("", "secret-code"),
        ("user", "secret-code"),
        ("user@qq.com", ""),
        ("user@qq.com", "  "),
    ],
)
def test_invalid_input_is_rejected_without_writing_secret(
    monkeypatch, capsys, tmp_path: Path, email: str, auth_code: str
) -> None:
    module = load_script()

    class FakeKeyring:
        def set_password(self, service: str, username: str, password: str) -> None:
            pytest.fail("invalid input must not reach Keychain")

    config_path = tmp_path / "config.json"
    monkeypatch.setattr(
        module,
        "CredentialStore",
        lambda: CredentialStore(config_path=config_path, keyring_backend=FakeKeyring()),
    )
    monkeypatch.setattr("builtins.input", lambda prompt: email)
    monkeypatch.setattr(module.getpass, "getpass", lambda prompt: auth_code)

    assert module.main() == 1
    output = capsys.readouterr()
    assert "Configuration failed" in output.out
    assert "secret-code" not in output.out + output.err
    assert not config_path.exists()


def test_configuration_error_cannot_reveal_secret(monkeypatch, capsys) -> None:
    module = load_script()

    class FakeStore:
        def save(self, email: str, auth_code: str) -> None:
            raise ConfigurationError(f"backend exposed {auth_code}")

    monkeypatch.setattr(module, "CredentialStore", FakeStore)
    monkeypatch.setattr("builtins.input", lambda prompt: "user@qq.com")
    monkeypatch.setattr(module.getpass, "getpass", lambda prompt: "secret-code")

    assert module.main() == 1
    output = capsys.readouterr()
    assert "Configuration failed" in output.out
    assert "secret-code" not in output.out + output.err


def test_getpass_echo_fallback_is_blocked_before_reading_or_saving(monkeypatch, capsys) -> None:
    module = load_script()

    class FakeStore:
        def save(self, email: str, auth_code: str) -> None:
            pytest.fail("insecure input must not be saved")

    def insecure_getpass(prompt: str) -> str:
        warnings.warn("cannot disable echo", module.getpass.GetPassWarning, stacklevel=2)
        pytest.fail("getpass fallback must not read echoed input")

    monkeypatch.setattr(module, "CredentialStore", FakeStore)
    monkeypatch.setattr("builtins.input", lambda prompt: "user@qq.com")
    monkeypatch.setattr(module.getpass, "getpass", insecure_getpass)

    assert module.main() == 1
    output = capsys.readouterr()
    assert "hidden input" in output.out.lower()
    assert "user@qq.com" not in output.out + output.err
    assert "cannot disable echo" not in output.out + output.err


@pytest.mark.parametrize("raised", [EOFError, KeyboardInterrupt])
def test_cancelled_input_exits_without_a_traceback(monkeypatch, capsys, raised) -> None:
    module = load_script()

    def cancel(prompt: str) -> str:
        raise raised

    monkeypatch.setattr("builtins.input", cancel)
    assert module.main() == 1
    output = capsys.readouterr()
    assert "cancelled" in output.out.lower()
    assert not output.err
