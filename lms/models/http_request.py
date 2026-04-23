# -*- coding: utf-8 -*-

from odoo import http
from odoo.http import request
import logging

_logger = logging.getLogger(__name__)


class HttpRequest(http.Request):
    """Override HttpRequest để thêm CORS headers tự động"""

    def make_response(self, *args, **kw):
        """Override make_response để thêm CORS headers"""
        response = super().make_response(*args, **kw)
        
        # Thêm CORS headers cho các API endpoints
        if hasattr(self, 'httprequest') and self.httprequest:
            path = self.httprequest.path
            if path.startswith('/lms/api/'):
                response.headers['Access-Control-Allow-Origin'] = '*'
                response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, DELETE, OPTIONS'
                response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization, X-Requested-With'
        
        return response
