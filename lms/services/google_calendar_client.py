# -*- coding: utf-8 -*-
"""Google Calendar API client: service account hoặc OAuth refresh (user chủ lịch)."""

import json
import logging
import os
import uuid
from typing import Dict, Optional
from urllib.parse import quote

from google.auth.transport.requests import AuthorizedSession
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials as OAuthCredentials

_logger = logging.getLogger(__name__)

REQUIRED_ENV_VARS = (
    'GOOGLE_CALENDAR_ENABLED',
    'GOOGLE_CALENDAR_AUTH_MODE',
    'GOOGLE_CALENDAR_ID',
    'GOOGLE_CALENDAR_TIMEZONE',
    'GOOGLE_CALENDAR_API_BASE_URL',
)
GOOGLE_CALENDAR_SCOPE = 'https://www.googleapis.com/auth/calendar'


def _path_segment(raw: str) -> str:
    """Calendar/event id trong URL phải percent-encode (ví dụ ``@`` → ``%40``)."""
    return quote(str(raw).strip(), safe='')


class GoogleCalendarConfigError(Exception):
    """Thiếu hoặc sai cấu hình Google Calendar."""


def _env_str(name: str, default: Optional[str] = None) -> str:
    value = os.environ.get(name)
    if value is None:
        if default is not None:
            return default
        raise GoogleCalendarConfigError(f'Thiếu biến môi trường bắt buộc: {name}')
    value = value.strip()
    if not value and default is None:
        raise GoogleCalendarConfigError(f'Thiếu biến môi trường bắt buộc: {name}')
    return value or (default or '')


def _env_int(name: str, default: Optional[int] = None) -> int:
    raw = _env_str(name, str(default) if default is not None else None)
    try:
        return int(raw)
    except ValueError as e:
        raise GoogleCalendarConfigError(f'{name} phải là số nguyên, nhận được: {raw!r}') from e


def _env_bool(name: str, default: Optional[bool] = None) -> bool:
    raw = _env_str(name, None if default is None else ('1' if default else '0')).lower()
    if raw in ('1', 'true', 'yes', 'on'):
        return True
    if raw in ('0', 'false', 'no', 'off'):
        return False
    raise GoogleCalendarConfigError(f'{name} phải là kiểu bool 0/1/true/false, nhận được: {raw!r}')


def get_google_calendar_config() -> Dict[str, object]:
    for name in REQUIRED_ENV_VARS:
        _env_str(name)

    enabled = _env_bool('GOOGLE_CALENDAR_ENABLED')
    auth_mode = _env_str('GOOGLE_CALENDAR_AUTH_MODE')
    if auth_mode not in ('service_account', 'oauth_refresh'):
        raise GoogleCalendarConfigError(
            'GOOGLE_CALENDAR_AUTH_MODE phải là service_account hoặc oauth_refresh, '
            f'nhận được: {auth_mode}'
        )

    config: Dict[str, object] = {
        'enabled': enabled,
        'auth_mode': auth_mode,
        'calendar_id': _env_str('GOOGLE_CALENDAR_ID'),
        'timezone': _env_str('GOOGLE_CALENDAR_TIMEZONE'),
        'api_base_url': _env_str('GOOGLE_CALENDAR_API_BASE_URL').rstrip('/'),
        'service_account_json': (os.environ.get('GOOGLE_SERVICE_ACCOUNT_JSON') or '').strip(),
        'service_account_file': (os.environ.get('GOOGLE_SERVICE_ACCOUNT_FILE') or '').strip(),
        'oauth_client_id': (os.environ.get('GOOGLE_OAUTH_CLIENT_ID') or '').strip(),
        'oauth_client_secret': (os.environ.get('GOOGLE_OAUTH_CLIENT_SECRET') or '').strip(),
        'oauth_refresh_token': (os.environ.get('GOOGLE_CALENDAR_OAUTH_REFRESH_TOKEN') or '').strip(),
        'oauth_token_uri': _env_str(
            'GOOGLE_OAUTH_TOKEN_URI', 'https://oauth2.googleapis.com/token'
        ),
        'include_instructor': _env_bool('GOOGLE_CALENDAR_INCLUDE_INSTRUCTOR', True),
        'include_students': _env_bool('GOOGLE_CALENDAR_INCLUDE_STUDENTS', True),
        'student_statuses': [
            s.strip() for s in _env_str('GOOGLE_CALENDAR_STUDENT_STATUSES', 'learning').split(',') if s.strip()
        ],
        'send_updates': _env_str('GOOGLE_CALENDAR_SEND_UPDATES', 'all'),
        'create_meet_link': _env_bool('GOOGLE_CALENDAR_CREATE_MEET_LINK', True),
        'include_meeting_url_in_description': _env_bool(
            'GOOGLE_CALENDAR_INCLUDE_MEETING_URL_IN_DESCRIPTION', True
        ),
        'request_timeout': _env_int('GOOGLE_CALENDAR_REQUEST_TIMEOUT', 60),
        'max_attendees': _env_int('GOOGLE_CALENDAR_MAX_ATTENDEES', 500),
    }

    if auth_mode == 'service_account':
        if not config['service_account_json'] and not config['service_account_file']:
            raise GoogleCalendarConfigError(
                'service_account: cần GOOGLE_SERVICE_ACCOUNT_FILE hoặc GOOGLE_SERVICE_ACCOUNT_JSON.'
            )
    else:
        if not config['oauth_client_id'] or not config['oauth_client_secret']:
            raise GoogleCalendarConfigError(
                'oauth_refresh: cần GOOGLE_OAUTH_CLIENT_ID và GOOGLE_OAUTH_CLIENT_SECRET.'
            )
        if not config['oauth_refresh_token']:
            raise GoogleCalendarConfigError(
                'oauth_refresh: cần GOOGLE_CALENDAR_OAUTH_REFRESH_TOKEN '
                '(lấy bằng scripts/google_calendar_oauth_setup.py).'
            )

    return config


def _load_service_account_info(config: Dict[str, object]) -> Dict[str, object]:
    if config['service_account_file']:
        path = str(config['service_account_file'])
        if not os.path.isfile(path):
            raise GoogleCalendarConfigError(f'Không tìm thấy file service account: {path}')
        with open(path, 'r', encoding='utf-8') as fp:
            return json.load(fp)
    try:
        return json.loads(str(config['service_account_json']))
    except json.JSONDecodeError as e:
        raise GoogleCalendarConfigError('GOOGLE_SERVICE_ACCOUNT_JSON không phải JSON hợp lệ.') from e


def _authorized_session(config: Dict[str, object]) -> AuthorizedSession:
    mode = config['auth_mode']
    if mode == 'service_account':
        info = _load_service_account_info(config)
        credentials = service_account.Credentials.from_service_account_info(
            info,
            scopes=[GOOGLE_CALENDAR_SCOPE],
        )
        return AuthorizedSession(credentials)

    if mode == 'oauth_refresh':
        credentials = OAuthCredentials(
            token=None,
            refresh_token=str(config['oauth_refresh_token']),
            token_uri=str(config['oauth_token_uri']),
            client_id=str(config['oauth_client_id']),
            client_secret=str(config['oauth_client_secret']),
            scopes=[GOOGLE_CALENDAR_SCOPE],
        )
        return AuthorizedSession(credentials)

    raise GoogleCalendarConfigError(f'Chế độ auth không hỗ trợ: {mode}')


def _request(method: str, url: str, *, params=None, json_body=None) -> Dict[str, object]:
    config = get_google_calendar_config()
    if not config['enabled']:
        raise GoogleCalendarConfigError('Google Calendar đang tắt (GOOGLE_CALENDAR_ENABLED=0).')
    session = _authorized_session(config)
    response = session.request(
        method=method,
        url=url,
        params=params,
        json=json_body,
        timeout=config['request_timeout'],
    )
    if not response.ok:
        body = response.text[:500] if response.text else ''
        _logger.warning('Google Calendar HTTP %s: %s', response.status_code, body)
        response.raise_for_status()
    if response.status_code == 204:
        return {}
    return response.json()


def get_calendar() -> Dict[str, object]:
    config = get_google_calendar_config()
    cid = _path_segment(str(config['calendar_id']))
    url = f"{config['api_base_url']}/calendars/{cid}"
    return _request('GET', url)


def create_event(payload: Dict[str, object]) -> Dict[str, object]:
    config = get_google_calendar_config()
    cid = _path_segment(str(config['calendar_id']))
    url = f"{config['api_base_url']}/calendars/{cid}/events"
    params = {
        'sendUpdates': config['send_updates'],
    }
    if config['create_meet_link']:
        params['conferenceDataVersion'] = 1
    body = dict(payload)
    if config['create_meet_link']:
        body.setdefault(
            'conferenceData',
            {
                'createRequest': {
                    'requestId': uuid.uuid4().hex,
                    'conferenceSolutionKey': {'type': 'hangoutsMeet'},
                }
            },
        )
    else:
        body.pop('conferenceData', None)
    return _request('POST', url, params=params, json_body=body)


def update_event(event_id: str, payload: Dict[str, object]) -> Dict[str, object]:
    config = get_google_calendar_config()
    cid = _path_segment(str(config['calendar_id']))
    eid = _path_segment(str(event_id))
    url = f"{config['api_base_url']}/calendars/{cid}/events/{eid}"
    params = {
        'sendUpdates': config['send_updates'],
    }
    body = dict(payload)
    if config['create_meet_link']:
        params['conferenceDataVersion'] = 1
    else:
        body.pop('conferenceData', None)
    return _request('PATCH', url, params=params, json_body=body)


def delete_event(event_id: str) -> None:
    config = get_google_calendar_config()
    cid = _path_segment(str(config['calendar_id']))
    eid = _path_segment(str(event_id))
    url = f"{config['api_base_url']}/calendars/{cid}/events/{eid}"
    params = {'sendUpdates': config['send_updates']}
    _request('DELETE', url, params=params)

