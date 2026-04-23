# -*- coding: utf-8 -*-

from odoo import http
from odoo.http import request
from odoo.exceptions import AccessDenied
import json
import logging

from .base_controller import make_json_response, handle_cors_preflight

_logger = logging.getLogger(__name__)


class AuthController(http.Controller):

    @http.route('/lms/dang-ky/giang-vien', type='http', auth='public')
    def lms_register_instructor_landing(self, **kwargs):
        """Trang đăng nhập: link «giảng viên» — mặc định sang /web/signup (cần module auth_signup)."""
        return request.redirect('/web/signup?lms_register=lecturer', local=True)

    @http.route('/lms/dang-ky/hoc-sinh', type='http', auth='public')
    def lms_register_student_landing(self, **kwargs):
        """Trang đăng nhập: link «học sinh» — mặc định sang /web/signup (cần module auth_signup)."""
        return request.redirect('/web/signup?lms_register=student', local=True)

    @http.route('/lms/api/login', type='http', auth='public', methods=['POST', 'OPTIONS'], csrf=False)
    def login(self, **kwargs):
        """API endpoint để đăng nhập (public, không cần auth)"""
        if request.httprequest.method == 'OPTIONS':
            return handle_cors_preflight(
                allow_methods='POST, OPTIONS',
                allow_headers='Content-Type',
            )

        try:
            data = json.loads(request.httprequest.data.decode('utf-8'))
            if isinstance(data, dict) and 'params' in data:
                data = data['params']

            email = data.get('email', '').strip()
            password = data.get('password', '')
            # Luôn dùng database hiện tại của Odoo để tránh sai DB do client gửi lên
            database = request.env.cr.dbname

            if not email or '@' not in email:
                return make_json_response({
                    'success': False,
                    'error': 'Email không hợp lệ'
                }, allow_methods='POST, OPTIONS', allow_headers='Content-Type')

            if not password:
                return make_json_response({
                    'success': False,
                    'error': 'Vui lòng nhập mật khẩu'
                }, allow_methods='POST, OPTIONS', allow_headers='Content-Type')

            uid = False
            try:
                uid = request.session.authenticate(database, email, password)
            except AccessDenied:
                _logger.warning(f'Login failed: wrong credentials for {email}')
                uid = False
            except Exception as e_auth:
                _logger.error(f'Login error while authenticating {email}: {str(e_auth)}', exc_info=True)
                return make_json_response(
                    {'success': False, 'error': 'Lỗi hệ thống khi đăng nhập. Vui lòng thử lại sau.'},
                    allow_methods='POST, OPTIONS',
                    allow_headers='Content-Type',
                )

            if uid:
                user = request.env['res.users'].sudo().browse(uid)
                student = request.env['lms.student'].sudo().search([
                    ('user_id', '=', uid)
                ], limit=1)

                session_id = None
                if hasattr(request.session, 'sid'):
                    session_id = request.session.sid
                elif hasattr(request.session, 'session_id'):
                    session_id = getattr(request.session, 'session_id', None)

                result = {
                    'success': True,
                    'uid': uid,
                    'session_id': session_id,
                    'user': {
                        'id': user.id,
                        'name': user.name,
                        'email': user.email or user.login,
                        'login': user.login,
                        'partner_id': user.partner_id.id if user.partner_id else None,
                    }
                }

                if student:
                    result['student'] = {
                        'id': student.id,
                        'name': student.name,
                        'email': student.email,
                        'phone': student.phone or '',
                        'current_level': student.current_level,
                        'learning_goals': student.learning_goals or '',
                        'desired_skills': student.desired_skills or '',
                        'total_courses': student.total_courses,
                        'completed_courses': student.completed_courses,
                        'average_score': student.average_score,
                        'is_active': student.is_active,
                        'user_id': student.user_id.id if student.user_id else None,
                    }
                    _logger.info(f'Login successful: {email} (UID: {uid}, Student ID: {student.id})')
                else:
                    result['student'] = None
                    _logger.warning(f'Login successful but no student found for {email} (UID: {uid})')
            else:
                result = {
                    'success': False,
                    'error': 'Email hoặc mật khẩu không đúng'
                }
                _logger.warning(f'Login failed: {email}')

            return make_json_response(
                result,
                allow_methods='POST, OPTIONS',
                allow_headers='Content-Type',
            )

        except Exception as e:
            _logger.error(f'Login error: {str(e)}', exc_info=True)
            return make_json_response(
                {'success': False, 'error': f'Lỗi đăng nhập: {str(e)}'},
                allow_methods='POST, OPTIONS',
                allow_headers='Content-Type',
            )

    @http.route('/lms/api/register', type='http', auth='public', methods=['POST', 'OPTIONS'], csrf=False)
    def register(self, **kwargs):
        """API endpoint để đăng ký tài khoản mới (public, không cần auth)"""
        if request.httprequest.method == 'OPTIONS':
            return handle_cors_preflight(
                allow_methods='POST, OPTIONS',
                allow_headers='Content-Type',
            )

        try:
            data = json.loads(request.httprequest.data.decode('utf-8'))
            if isinstance(data, dict) and 'params' in data:
                data = data['params']

            name = data.get('name', '').strip()
            email = data.get('email', '').strip()
            password = data.get('password', '')
            phone = data.get('phone', '').strip()
            current_level = data.get('current_level', 'beginner')

            if not name or len(name) < 2:
                return make_json_response(
                    {'success': False, 'error': 'Họ và tên phải có ít nhất 2 ký tự'},
                    allow_methods='POST, OPTIONS',
                    allow_headers='Content-Type',
                )

            if not email or '@' not in email:
                return make_json_response(
                    {'success': False, 'error': 'Email không hợp lệ'},
                    allow_methods='POST, OPTIONS',
                    allow_headers='Content-Type',
                )

            if not password or len(password) < 6:
                return make_json_response(
                    {'success': False, 'error': 'Mật khẩu phải có ít nhất 6 ký tự'},
                    allow_methods='POST, OPTIONS',
                    allow_headers='Content-Type',
                )

            if current_level not in ['beginner', 'intermediate', 'advanced']:
                current_level = 'beginner'

            existing_user = request.env['res.users'].sudo().search([
                ('login', '=', email)
            ], limit=1)

            if existing_user:
                return make_json_response(
                    {'success': False, 'error': 'Email đã được sử dụng'},
                    allow_methods='POST, OPTIONS',
                    allow_headers='Content-Type',
                )

            partner = None
            user = None
            student = None

            try:
                partner = request.env['res.partner'].sudo().create({
                    'name': name,
                    'email': email,
                    'phone': phone,
                    'is_company': False,
                })

                student_group = request.env.ref('lms.group_lms_user', raise_if_not_found=False)
                if not student_group:
                    student_group = request.env['res.groups'].sudo().search([
                        ('category_id.name', '=', 'LMS'),
                        ('name', 'ilike', 'Student')
                    ], limit=1)

                user_vals = {
                    'name': name,
                    'login': email,
                    'email': email,
                    'partner_id': partner.id,
                }
                if student_group:
                    user_vals['groups_id'] = [(4, student_group.id)]

                user = request.env['res.users'].sudo().with_context(
                    no_reset_password=True
                ).create(user_vals)

                # Set password: dùng write('password') để Odoo xử lý đúng format cho authenticate()
                try:
                    user.sudo().with_context(no_reset_password=True).write({'password': password})
                except Exception:
                    crypt_context = request.env['res.users']._crypt_context()
                    hashed = crypt_context.hash(password)
                    user.sudo().write({'password_crypt': hashed})

                request.env.cr.flush()

                student = request.env['lms.student'].sudo().create({
                    'name': name,
                    'email': email,
                    'phone': phone,
                    'current_level': current_level,
                    'is_active': True,
                    'user_id': user.id,
                })

                _logger.info(f'User registered: {email} (User ID: {user.id}, Student ID: {student.id})')

            except Exception as e_create:
                _logger.error(f'Register create error: {e_create}', exc_info=True)
                # Rollback: xoá các object đã tạo theo thứ tự ngược
                for obj, label in [(student, 'student'), (user, 'user'), (partner, 'partner')]:
                    if obj:
                        try:
                            obj.sudo().unlink()
                        except Exception:
                            pass
                return make_json_response(
                    {'success': False, 'error': f'Lỗi tạo tài khoản: {str(e_create)}'},
                    allow_methods='POST, OPTIONS',
                    allow_headers='Content-Type',
                )

            return make_json_response(
                {'success': True, 'user_id': user.id, 'student_id': student.id},
                allow_methods='POST, OPTIONS',
                allow_headers='Content-Type',
            )

        except Exception as e:
            _logger.error(f'Registration error: {str(e)}', exc_info=True)
            return make_json_response(
                {'success': False, 'error': f'Lỗi đăng ký: {str(e)}'},
                allow_methods='POST, OPTIONS',
                allow_headers='Content-Type',
            )