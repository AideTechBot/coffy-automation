# coffy

KDE system-tray app to toggle a Kasa EP10 smart plug that powers my coffee
machine. Left-click toggles, right-click for an explicit ON/OFF/Refresh menu.

> [!WARNING]
> This is a personal tool. Almost none of the code is meaningfully
> tested, there are no unit tests, no integration tests, and the cloud
> client was reverse-engineered from packet captures of the Kasa Android
> app. It works on my machine, against my account, with my one plug. If
> you run it, expect to debug it.

## Why the cloud?

The EP10 received a firmware update that switched local auth to TP-Link's
KLAP scheme, and the public KLAP implementations don't work against this
device anymore (see [`ref_ep10_klap_broken`][klap] for context). So this
goes through the TP-Link Cloud V2 API instead: slower, less private, but
it actually controls the plug.

[klap]: https://github.com/python-kasa/python-kasa/issues

## Layout

| File | Purpose |
| --- | --- |
| `coffy.py` | PyQt6 tray app, the entry point |
| `tplink_cloud.py` | Async TP-Link Cloud V2 client (login, MFA, refresh, passthrough) |
| `tplink-ca-chain.pem` | CA bundle pinned by the cloud client |
| `set_credentials.py` | One-time helper to stash email/password in KDE Wallet |
| `coffy.desktop` | Autostart entry |
| `flake.nix` | Nix package + dev shell |

## Setup

```sh
# 1. Store TP-Link account credentials in KDE Wallet
nix run .#set-credentials

# 2. Tell coffy which plug to control
mkdir -p ~/.config/coffy
echo 'PLUG_DEVICE_ID = "<your plug deviceId>"' > ~/.config/coffy/config.py

# 3. Run it
nix run .
```

The `deviceId` is the value TP-Link's cloud uses, not the MAC. The easiest
way to find it is to log into the Kasa app once, then dig it out of an
HTTPS proxy capture, or temporarily add a `getDeviceList` call to
`tplink_cloud.py` and print the result.

## MFA

If 2FA is enabled on the TP-Link account, login will fail with
`MFARequired` and the tray will show "disconnected". There is no MFA UI
in this app. Turn off 2FA or rotate the refresh token in by hand:

```sh
secret-tool store --label="coffy refresh" service coffy username tplink_refresh_token
```

## Autostart

Drop `coffy.desktop` into `~/.config/autostart/` (edit the absolute path
inside it first).
