#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Lấy GOOGLE_CALENDAR_OAUTH_REFRESH_TOKEN cho LMS (chế độ GOOGLE_CALENDAR_AUTH_MODE=oauth_refresh).

Chuẩn bị (Google Cloud Console):
  - Bật Google Calendar API
  - OAuth 2.0 Client ID loại "Desktop app"
  - Copy Client ID + Client Secret vào .env

Cài gói (một lần, trên máy dev):
  pip install google-auth-oauthlib google-auth python-dotenv

Chạy (từ thư mục gốc repo DATN-LMS):
  python scripts/google_calendar_oauth_setup.py

Sau đó dán dòng in ra vào .env:
  GOOGLE_CALENDAR_OAUTH_REFRESH_TOKEN=...
Rồi restart Odoo / container.
"""
from __future__ import annotations

import argparse
import os
import sys

# Khớp lms/services/google_calendar_client.py
GOOGLE_CALENDAR_SCOPE = ['https://www.googleapis.com/auth/calendar']


def _repo_root() -> str:
    return os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))


def _load_dotenv(path: str | None) -> None:
    env_path = path or os.path.join(_repo_root(), '.env')
    if not os.path.isfile(env_path):
        return
    try:
        from dotenv import load_dotenv

        load_dotenv(env_path)
        print('Loaded env from:', env_path, file=sys.stderr)
    except ImportError:
        print('Tip: pip install python-dotenv to auto-read .env', file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(
        description='Obtain Google Calendar OAuth refresh token (GOOGLE_CALENDAR_OAUTH_REFRESH_TOKEN).'
    )
    parser.add_argument(
        '--env-file',
        metavar='PATH',
        help='Path to .env (default: .env in repo root).',
    )
    parser.add_argument('--client-id', help='Override GOOGLE_OAUTH_CLIENT_ID')
    parser.add_argument('--client-secret', help='Override GOOGLE_OAUTH_CLIENT_SECRET')
    parser.add_argument(
        '--no-open-browser',
        action='store_true',
        help='Print URL only; open it manually in the browser.',
    )
    args = parser.parse_args()

    _load_dotenv(args.env_file)

    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        sys.stderr.write('Missing packages. Run: pip install google-auth-oauthlib google-auth\n')
        return 1

    client_id = (args.client_id or os.environ.get('GOOGLE_OAUTH_CLIENT_ID') or '').strip()
    client_secret = (args.client_secret or os.environ.get('GOOGLE_OAUTH_CLIENT_SECRET') or '').strip()
    if not client_id or not client_secret:
        sys.stderr.write(
            'Set GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET in .env '
            'or pass --client-id / --client-secret.\n'
        )
        return 1

    client_config = {
        'installed': {
            'client_id': client_id,
            'client_secret': client_secret,
            'auth_uri': 'https://accounts.google.com/o/oauth2/auth',
            'token_uri': 'https://oauth2.googleapis.com/token',
            'redirect_uris': ['http://localhost'],
        }
    }

    flow = InstalledAppFlow.from_client_config(client_config, scopes=GOOGLE_CALENDAR_SCOPE)
    print('Opening browser for Google sign-in (Calendar scope)...', file=sys.stderr)

    creds = flow.run_local_server(
        port=0,
        open_browser=not args.no_open_browser,
        access_type='offline',
        prompt='consent',
        authorization_prompt_message='Sign in and allow Calendar access for LMS.',
        success_message='Done. You can close this tab and return to the terminal.',
    )

    if not creds.refresh_token:
        sys.stderr.write(
            'Google did not return a refresh_token.\n'
            'Try: https://myaccount.google.com/permissions — remove this app, then run again.\n'
        )
        return 1

    print()
    print('--- Add or update in .env ---')
    print()
    print('GOOGLE_CALENDAR_OAUTH_REFRESH_TOKEN=%s' % creds.refresh_token)
    print()
    print('Keep the same GOOGLE_OAUTH_CLIENT_ID / GOOGLE_OAUTH_CLIENT_SECRET used above.')
    print('Restart Odoo after saving .env.')
    print()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
