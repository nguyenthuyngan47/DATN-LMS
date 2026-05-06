# -*- coding: utf-8 -*-
"""Tiện ích so khớp embedding khuôn mặt (vector JSON) — đơn giản cho đồ án LMS."""

from __future__ import annotations

import json
import math
from typing import Optional

# Phải khớp số chiều sinh ở lms/static/src/js/lms_face_mount.js
FACE_EMBEDDING_DIM = 128
COSINE_MATCH_THRESHOLD = 0.88


def parse_embedding(json_str: str | bool | None) -> Optional[list[float]]:
    if not json_str or not isinstance(json_str, str):
        return None
    s = json_str.strip()
    if not s:
        return None
    try:
        vec = json.loads(s)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(vec, list) or len(vec) != FACE_EMBEDDING_DIM:
        return None
    try:
        return [float(x) for x in vec]
    except (TypeError, ValueError):
        return None


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na <= 0 or nb <= 0:
        return 0.0
    return dot / (na * nb)
