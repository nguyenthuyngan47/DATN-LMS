# -*- coding: utf-8 -*-

from odoo import http
from odoo.http import request
import json
import logging

from .base_controller import make_json_response, handle_cors_preflight

_logger = logging.getLogger(__name__)

_CORS = dict(allow_methods='POST, OPTIONS', allow_headers='Content-Type')


class DataController(http.Controller):

    # ─────────────────────────────────────────────────────────────
    #  /lms/api/roadmaps
    # ─────────────────────────────────────────────────────────────
    @http.route('/lms/api/roadmaps', type='http', auth='public',
                methods=['POST', 'OPTIONS'], csrf=False)
    def get_roadmaps(self, **kwargs):
        if request.httprequest.method == 'OPTIONS':
            return handle_cors_preflight(**_CORS)

        try:
            json.loads(request.httprequest.data.decode('utf-8'))

            roadmaps = request.env['lms.roadmap'].sudo().search([], order='name asc')

            result = []
            for rm in roadmaps:
                result.append({
                    'id': rm.id,
                    'name': rm.name,
                    'description': rm.description or '',
                    'level': rm.level if hasattr(rm, 'level') else '',
                    'total_courses': len(rm.course_ids) if hasattr(rm, 'course_ids') else 0,
                    'is_active': rm.active if hasattr(rm, 'active') else True,
                })

            return make_json_response({'success': True, 'roadmaps': result}, **_CORS)

        except Exception as e:
            _logger.error(f'get_roadmaps error: {e}', exc_info=True)
            return make_json_response({'success': False, 'error': str(e)}, **_CORS)

    # ─────────────────────────────────────────────────────────────
    #  /lms/api/courses/enrolled
    # ─────────────────────────────────────────────────────────────
    @http.route('/lms/api/courses/enrolled', type='http', auth='public',
                methods=['POST', 'OPTIONS'], csrf=False)
    def get_enrolled_courses(self, **kwargs):
        if request.httprequest.method == 'OPTIONS':
            return handle_cors_preflight(**_CORS)

        try:
            data = json.loads(request.httprequest.data.decode('utf-8'))
            student_id = data.get('student_id')

            if not student_id:
                return make_json_response(
                    {'success': False, 'error': 'student_id là bắt buộc'}, **_CORS)

            enrollments = request.env['lms.student.course'].sudo().search([
                ('student_id', '=', student_id),
            ], order='create_date desc')

            courses = []
            for enroll in enrollments:
                course = enroll.course_id
                courses.append({
                    'id': course.id,
                    'name': course.name,
                    'description': course.description or '',
                    'level': course.level if hasattr(course, 'level') else '',
                    'duration': course.duration if hasattr(course, 'duration') else 0,
                    'enrollment_id': enroll.id,
                    'enrollment_date': str(enroll.create_date) if enroll.create_date else '',
                    'completion_rate': enroll.completion_rate if hasattr(enroll, 'completion_rate') else 0,
                    'status': enroll.status if hasattr(enroll, 'status') else 'pending',
                })

            return make_json_response({'success': True, 'courses': courses}, **_CORS)

        except Exception as e:
            _logger.error(f'get_enrolled_courses error: {e}', exc_info=True)
            return make_json_response({'success': False, 'error': str(e)}, **_CORS)

    # ─────────────────────────────────────────────────────────────
    #  /lms/api/progress
    # ─────────────────────────────────────────────────────────────
    @http.route('/lms/api/progress', type='http', auth='public',
                methods=['POST', 'OPTIONS'], csrf=False)
    def get_progress(self, **kwargs):
        if request.httprequest.method == 'OPTIONS':
            return handle_cors_preflight(**_CORS)

        try:
            data = json.loads(request.httprequest.data.decode('utf-8'))
            student_id = data.get('student_id')

            if not student_id:
                return make_json_response(
                    {'success': False, 'error': 'student_id là bắt buộc'}, **_CORS)

            enrollments = request.env['lms.student.course'].sudo().search([
                ('student_id', '=', student_id),
            ])

            progress = []
            for enroll in enrollments:
                # Lấy lịch sử học gần nhất
                last_history = request.env['lms.learning.history'].sudo().search([
                    ('student_id', '=', student_id),
                    ('course_id', '=', enroll.course_id.id),
                ], order='date desc', limit=1)

                progress.append({
                    'course_id': enroll.course_id.id,
                    'course_name': enroll.course_id.name,
                    'completion_rate': enroll.completion_rate if hasattr(enroll, 'completion_rate') else 0,
                    'score': enroll.score if hasattr(enroll, 'score') else 0,
                    'status': enroll.status if hasattr(enroll, 'status') else 'pending',
                    'last_activity': str(last_history.date) if last_history and last_history.date else '',
                    'completed_lessons': enroll.completed_lessons if hasattr(enroll, 'completed_lessons') else 0,
                    'total_lessons': enroll.course_id.total_lessons if hasattr(enroll.course_id, 'total_lessons') else 0,
                })

            return make_json_response({'success': True, 'progress': progress}, **_CORS)

        except Exception as e:
            _logger.error(f'get_progress error: {e}', exc_info=True)
            return make_json_response({'success': False, 'error': str(e)}, **_CORS)
