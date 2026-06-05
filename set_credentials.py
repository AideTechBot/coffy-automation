#!/usr/bin/env python3
"""One-time setup: store TP-Link Kasa credentials in KDE Wallet via keyring."""
import getpass
import sys

import keyring

SERVICE = "coffy"


def main() -> int:
    print(f"Using keyring backend: {keyring.get_keyring()}")
    print()
    print("Enter the TP-Link / Kasa account credentials for the coffee plug.")
    print("These get stored in KDE Wallet (encrypted, unlocked with your session).")
    print()

    email = input("TP-Link email:    ").strip()
    password = getpass.getpass("TP-Link password: ")

    if not email or not password:
        print("Both fields required. Aborting.", file=sys.stderr)
        return 1

    keyring.set_password(SERVICE, "tplink_email", email)
    keyring.set_password(SERVICE, "tplink_password", password)

    print()
    print(f"Saved under keyring service '{SERVICE}'.")
    print("Verify with: secret-tool search service coffy   (or kwalletmanager GUI)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
