# -*- coding: utf-8 -*-
"""Nạp bắt buộc file .env ở thư mục gốc repo LMS (cùng cấp với addon ``lms``)."""

import logging
import os
from pathlib import Path

_logger = logging.getLogger(__name__)

_DOTENV_LOADED = False


def load_lms_dotenv():
    """
    Gọi một lần khi import module ``lms``: **bắt buộc** có file .env.

    - Mặc định: ``<repo_LMS>/.env``
    - Hoặc: ``LMS_ENV_FILE`` = đường dẫn tuyệt đối tới .env

    :raises RuntimeError: thiếu python-dotenv, hoặc không tìm thấy file .env.
    """
    global _DOTENV_LOADED
    if _DOTENV_LOADED:
        return
    _DOTENV_LOADED = True

    try:
        from dotenv import load_dotenv
    except ImportError as e:
        raise RuntimeError(
            'LMS bắt buộc cài python-dotenv: pip install python-dotenv'
        ) from e

    custom = os.environ.get('LMS_ENV_FILE', '').strip()
    if custom:
        p = Path(custom)
        if not p.is_file():
            raise RuntimeError(f'LMS bắt buộc có file .env (LMS_ENV_FILE không tồn tại): {custom}')
        load_dotenv(p, override=False)
        _logger.info('LMS: loaded dotenv from LMS_ENV_FILE=%s', custom)
        return

    # tools/ -> lms/ -> parent = "repo" host (LMS/) hoặc Docker: /mnt/extra-addons
    lms_root = Path(__file__).resolve().parent.parent
    repo_root = lms_root.parent
    candidates = [
        repo_root / '.env',
        lms_root / '.env',
    ]
    for env_path in candidates:
        if env_path.is_file():
            load_dotenv(env_path, override=False)
            _logger.info('LMS: loaded dotenv from %s', env_path)
            return

    raise RuntimeError(
        'LMS bắt buộc có file .env. Đã thử: '
        + ', '.join(str(p) for p in candidates)
        + '. Docker: mount ./.env vào /mnt/extra-addons/.env (xem docker-compose.yml) '
        'hoặc đặt LMS_ENV_FILE=/đường/dẫn/.env trong container.'
    )
