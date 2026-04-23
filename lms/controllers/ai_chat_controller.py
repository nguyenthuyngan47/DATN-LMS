# -*- coding: utf-8 -*-

import json
import logging

import requests
from odoo import http
from odoo.http import request

from ..services import groq_client
from ..services.groq_client import GroqConfigError

from .base_controller import handle_cors_preflight, make_json_response

_logger = logging.getLogger(__name__)


class LmsAiChatController(http.Controller):
    """API chat Groq cho user đã đăng nhập (session Odoo)."""

    @http.route('/lms/api/ai/chat', type='http', auth='user', methods=['POST', 'OPTIONS'], csrf=False)
    def lms_ai_chat(self, **kwargs):
        if request.httprequest.method == 'OPTIONS':
            return handle_cors_preflight(
                allow_methods='POST, OPTIONS',
                allow_headers='Content-Type',
            )

        try:
            raw = request.httprequest.data
            if not raw:
                return make_json_response(
                    {'success': False, 'error': 'Thiếu body JSON'},
                    status_code=400,
                    allow_methods='POST, OPTIONS',
                    allow_headers='Content-Type',
                )
            body = json.loads(raw.decode('utf-8'))
        except json.JSONDecodeError:
            return make_json_response(
                {'success': False, 'error': 'JSON không hợp lệ'},
                status_code=400,
                allow_methods='POST, OPTIONS',
                allow_headers='Content-Type',
            )

        if isinstance(body, dict) and 'params' in body:
            body = body['params']

        messages = body.get('messages')
        temperature = body.get('temperature')
        max_tokens = body.get('max_tokens')
        if temperature is not None:
            temperature = float(temperature)
        if max_tokens is not None:
            max_tokens = int(max_tokens)

        try:
            text = groq_client.chat_completion(
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        except GroqConfigError as e:
            _logger.warning('Groq config: %s', e)
            return make_json_response(
                {'success': False, 'error': str(e)},
                status_code=503,
                allow_methods='POST, OPTIONS',
                allow_headers='Content-Type',
            )
        except ValueError as e:
            return make_json_response(
                {'success': False, 'error': str(e)},
                status_code=400,
                allow_methods='POST, OPTIONS',
                allow_headers='Content-Type',
            )
        except requests.RequestException as e:
            _logger.warning('Groq HTTP error: %s', e)
            return make_json_response(
                {'success': False, 'error': 'Không kết nối được tới Groq hoặc bị từ chối.'},
                status_code=502,
                allow_methods='POST, OPTIONS',
                allow_headers='Content-Type',
            )
        except Exception as e:
            _logger.exception('Groq chat error')
            return make_json_response(
                {'success': False, 'error': 'Lỗi khi gọi AI. Vui lòng thử lại sau.'},
                status_code=502,
                allow_methods='POST, OPTIONS',
                allow_headers='Content-Type',
            )

        return make_json_response(
            {
                'success': True,
                'message': text,
                'model': groq_client.get_groq_model(),
            },
            allow_methods='POST, OPTIONS',
            allow_headers='Content-Type',
        )
