#!/usr/bin/env python3
"""Interactively save QQ Mail credentials in the local Keychain."""

from __future__ import annotations

import getpass
import warnings

from qq_mail_reader.credentials import CredentialStore, mask_email
from qq_mail_reader.errors import ConfigurationError


def main() -> int:
    """Prompt locally for a QQ Mail address and authorization code."""
    print("Configure read-only QQ Mail IMAP access.")
    print("Use the QQ Mail authorization code, not your QQ account password.")
    try:
        email = input("Full QQ Mail address: ").strip()
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            auth_code = getpass.getpass("QQ Mail authorization code: ")
    except getpass.GetPassWarning:
        print("Configuration failed: hidden input is unavailable. Use an interactive terminal.")
        return 1
    except (EOFError, KeyboardInterrupt):
        print("Configuration cancelled.")
        return 1

    try:
        CredentialStore().save(email, auth_code)
    except ConfigurationError:
        print("Configuration failed. Check the address, authorization code, and Keychain.")
        return 1

    print(f"Saved authorization code in Keychain for {mask_email(email)}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
