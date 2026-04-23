# -*- coding: utf-8 -*-

from odoo import http
from odoo.http import request
import json

from .base_controller import make_json_response, handle_cors_preflight


class RoadmapController(http.Controller):

    @http.route('/lms/roadmap/generate', type='http', auth='user', methods=['POST', 'OPTIONS'], csrf=False)
    def generate_roadmap(self, **kwargs):
        """API endpoint để tạo roadmap"""
        if request.httprequest.method == 'OPTIONS':
            return handle_cors_preflight(
                allow_methods='POST, OPTIONS',
                allow_headers='Content-Type, Authorization',
            )

        try:
            data = json.loads(request.httprequest.data.decode('utf-8'))
            if isinstance(data, dict) and 'params' in data:
                data = data['params']

            student_id = data.get('student_id')
            if not student_id:
                student = request.env['lms.student'].search([
                    ('user_id', '=', request.env.user.id)
                ], limit=1)
                if student:
                    student_id = student.id
                else:
                    result = {
                        'success': False,
                        'message': 'Không tìm thấy thông tin học viên'
                    }
                    return make_json_response(
                        result,
                        allow_methods='POST, OPTIONS',
                        allow_headers='Content-Type, Authorization',
                    )

            ai_analysis = request.env['lms.ai.analysis']
            roadmap = ai_analysis.generate_roadmap(student_id)

            result = {
                'success': True,
                'roadmap_id': roadmap.id,
                'message': 'Roadmap đã được tạo thành công',
            }

            return make_json_response(
                result,
                allow_methods='POST, OPTIONS',
                allow_headers='Content-Type, Authorization',
            )
        except Exception as e:
            result = {
                'success': False,
                'message': str(e),
            }
            return make_json_response(
                result,
                allow_methods='POST, OPTIONS',
                allow_headers='Content-Type, Authorization',
            )

    @http.route('/lms/roadmap/<int:roadmap_id>', type='http', auth='user', methods=['GET'])
    def get_roadmap(self, roadmap_id):
        """Lấy thông tin roadmap"""
        roadmap = request.env['lms.roadmap'].browse(roadmap_id)
        if not roadmap.exists():
            return request.not_found()

        return request.render('lms.roadmap_template', {
            'roadmap': roadmap,
        })
