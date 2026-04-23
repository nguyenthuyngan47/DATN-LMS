# -*- coding: utf-8 -*-

from odoo.http import request
import json


def make_json_response(
    data,
    status_code=200,
    allow_methods="GET, POST, PUT, DELETE, OPTIONS",
    allow_headers="Content-Type, Authorization, X-Requested-With",
):
    """Helper tạo JSON response với CORS headers thống nhất."""
    headers = [
        ("Content-Type", "application/json"),
        ("Access-Control-Allow-Origin", "*"),
        ("Access-Control-Allow-Methods", allow_methods),
        ("Access-Control-Allow-Headers", allow_headers),
    ]
    return request.make_response(json.dumps(data), headers=headers, status=status_code)


def handle_cors_preflight(
    allow_methods="GET, POST, PUT, DELETE, OPTIONS",
    allow_headers="Content-Type, Authorization, X-Requested-With",
    max_age="3600",
):
    """Helper xử lý CORS preflight request (OPTIONS)."""
    headers = [
        ("Access-Control-Allow-Origin", "*"),
        ("Access-Control-Allow-Methods", allow_methods),
        ("Access-Control-Allow-Headers", allow_headers),
        ("Access-Control-Max-Age", max_age),
    ]
    return request.make_response("", headers=headers)
