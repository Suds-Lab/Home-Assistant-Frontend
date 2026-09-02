#!/usr/bin/env python3
"""Mint a Telegram session string for Control Center - run this ONCE on your OWN
computer, then paste the printed session string into the Control Center admin
"Telegram" tab (the "session string" field).

Nothing here talks to Control Center or to us: it logs your account in directly
with Telegram and prints the resulting session. Your phone number and the login
code stay on this machine.

Recommended: use a DEDICATED Telegram account that is only a member of the log
channels (nothing personal to lose), and put a 2FA password on it.

Setup (on your machine, not the add-on):
    pip install telethon
    python telegram_login.py

You'll need api_id and api_hash from https://my.telegram.org/apps. The script
asks for your phone, the code Telegram sends you, and your 2FA password if set.

To revoke later: Telegram -> Settings -> Devices -> terminate the "Control Center"
session. That instantly kills whatever session string you generated here.
"""
from telethon.sync import TelegramClient
from telethon.sessions import StringSession


def main():
    print("Telegram session string generator for Control Center\n")
    api_id = int(input("api_id (from my.telegram.org): ").strip())
    api_hash = input("api_hash: ").strip()

    # device_model matches the add-on so the session is easy to spot/revoke in
    # Telegram -> Settings -> Devices.
    with TelegramClient(StringSession(), api_id, api_hash,
                        device_model="Control Center", app_version="cc",
                        system_version="HA add-on") as client:
        session = client.session.save()
        print("\n" + "=" * 60)
        print("SESSION STRING (paste this into Control Center, keep it secret):\n")
        print(session)
        print("=" * 60)


if __name__ == "__main__":
    main()
