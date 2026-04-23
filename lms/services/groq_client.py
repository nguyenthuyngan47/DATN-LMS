# -*- coding: utf-8 -*-
"""Gọi Groq OpenAI-compatible Chat Completions — mọi cấu hình chỉ từ biến môi trường (.env)."""

import json
import logging
import os
from typing import Any, List, Optional

import requests

_logger = logging.getLogger(__name__)

# Tên biến môi trường bắt buộc (dùng cho thông báo lỗi / tài liệu)
REQUIRED_ENV_VARS = (
    'GROQ_API_KEY',
    'GROQ_MODEL',
    'GROQ_CHAT_URL',
    'GROQ_MAX_MESSAGES',
    'GROQ_MAX_MESSAGE_CHARS',
    'GROQ_REQUEST_TIMEOUT',
    'GROQ_DEFAULT_TEMPERATURE',
    'GROQ_DEFAULT_MAX_TOKENS',
    'GROQ_TEMPERATURE_MIN',
    'GROQ_TEMPERATURE_MAX',
    'GROQ_MAX_OUTPUT_TOKENS_CAP',
)


class GroqConfigError(Exception):
    """Thiếu hoặc sai cấu hình AI trong biến môi trường."""


def _missing_env_names():
    return [name for name in REQUIRED_ENV_VARS if not (os.environ.get(name) or '').strip()]


def _env_str(name: str) -> str:
    v = (os.environ.get(name) or '').strip()
    if not v:
        raise GroqConfigError(f'Thiếu biến môi trường bắt buộc: {name}')
    return v


def _env_int(name: str) -> int:
    raw = _env_str(name)
    try:
        return int(raw)
    except ValueError as e:
        raise GroqConfigError(f'{name} phải là số nguyên, nhận được: {raw!r}') from e


def _env_float(name: str) -> float:
    raw = _env_str(name)
    try:
        return float(raw)
    except ValueError as e:
        raise GroqConfigError(f'{name} phải là số thực, nhận được: {raw!r}') from e


def ensure_groq_env():
    """Kiểm tra đủ biến bắt buộc; raise GroqConfigError nếu thiếu."""
    missing = _missing_env_names()
    if missing:
        raise GroqConfigError(
            'Thiếu biến trong .env (hoặc môi trường): ' + ', '.join(missing)
        )


def get_groq_config():
    """Đọc toàn bộ cấu hình Groq từ ENV (raise nếu thiếu/sai)."""
    ensure_groq_env()
    t_min = _env_float('GROQ_TEMPERATURE_MIN')
    t_max = _env_float('GROQ_TEMPERATURE_MAX')
    if t_min > t_max:
        raise GroqConfigError('GROQ_TEMPERATURE_MIN không được lớn hơn GROQ_TEMPERATURE_MAX.')

    max_msg = _env_int('GROQ_MAX_MESSAGES')
    max_chars = _env_int('GROQ_MAX_MESSAGE_CHARS')
    timeout = _env_int('GROQ_REQUEST_TIMEOUT')
    default_max_tok = _env_int('GROQ_DEFAULT_MAX_TOKENS')
    cap = _env_int('GROQ_MAX_OUTPUT_TOKENS_CAP')

    if max_msg < 1:
        raise GroqConfigError('GROQ_MAX_MESSAGES phải >= 1')
    if max_chars < 1:
        raise GroqConfigError('GROQ_MAX_MESSAGE_CHARS phải >= 1')
    if timeout < 1:
        raise GroqConfigError('GROQ_REQUEST_TIMEOUT phải >= 1 (giây)')
    if default_max_tok < 1:
        raise GroqConfigError('GROQ_DEFAULT_MAX_TOKENS phải >= 1')
    if cap < 1:
        raise GroqConfigError('GROQ_MAX_OUTPUT_TOKENS_CAP phải >= 1')

    return {
        'api_key': _env_str('GROQ_API_KEY'),
        'model': _env_str('GROQ_MODEL'),
        'chat_url': _env_str('GROQ_CHAT_URL'),
        'max_messages': max_msg,
        'max_message_chars': max_chars,
        'request_timeout': timeout,
        'default_temperature': _env_float('GROQ_DEFAULT_TEMPERATURE'),
        'default_max_tokens': default_max_tok,
        'temperature_min': t_min,
        'temperature_max': t_max,
        'max_output_tokens_cap': cap,
    }


def get_groq_model() -> str:
    """Model đang cấu hình (để trả về JSON); raise nếu thiếu ENV."""
    return get_groq_config()['model']


def get_groq_defaults():
    """Nhiệt độ / max_tokens mặc định khi client không gửi."""
    cfg = get_groq_config()
    return cfg['default_temperature'], cfg['default_max_tokens']


def _normalize_messages(raw: Any, cfg: dict) -> List[dict]:
    if not isinstance(raw, list):
        return []
    lim = cfg['max_messages']
    mchars = cfg['max_message_chars']
    out = []
    for item in raw[:lim]:
        if not isinstance(item, dict):
            continue
        role = (item.get('role') or '').strip().lower()
        content = item.get('content')
        if role not in ('system', 'user', 'assistant'):
            continue
        if content is None:
            continue
        text = str(content).strip()
        if not text:
            continue
        if len(text) > mchars:
            text = text[:mchars]
        out.append({'role': role, 'content': text})
    return out


def chat_completion(
    messages: List[dict],
    *,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
) -> str:
    """
    Gửi ``messages`` (OpenAI format) tới Groq, trả nội dung assistant (text).

    :raises GroqConfigError: thiếu/sai biến môi trường
    :raises requests.HTTPError: lỗi HTTP từ Groq
    :raises ValueError: phản hồi không hợp lệ
    """
    cfg = get_groq_config()

    if temperature is None:
        temperature = cfg['default_temperature']
    if max_tokens is None:
        max_tokens = cfg['default_max_tokens']

    t_min, t_max = cfg['temperature_min'], cfg['temperature_max']
    temperature = max(t_min, min(t_max, float(temperature)))

    cap = cfg['max_output_tokens_cap']
    max_tokens = max(1, min(cap, int(max_tokens)))

    msgs = _normalize_messages(messages, cfg)
    if not msgs:
        raise ValueError('Danh sách messages rỗng hoặc không hợp lệ.')

    payload = {
        'model': cfg['model'],
        'messages': msgs,
        'temperature': temperature,
        'max_tokens': max_tokens,
    }

    resp = requests.post(
        cfg['chat_url'],
        headers={
            'Authorization': f'Bearer {cfg["api_key"]}',
            'Content-Type': 'application/json',
        },
        data=json.dumps(payload),
        timeout=cfg['request_timeout'],
    )

    if not resp.ok:
        body = resp.text[:500] if resp.text else ''
        _logger.warning('Groq HTTP %s: %s', resp.status_code, body)
        resp.raise_for_status()

    data = resp.json()
    choices = data.get('choices') or []
    if not choices:
        raise ValueError('Groq trả về không có choices.')
    msg = (choices[0].get('message') or {})
    content = msg.get('content')
    if content is None or not str(content).strip():
        raise ValueError('Groq trả về nội dung rỗng.')
    return str(content).strip()
