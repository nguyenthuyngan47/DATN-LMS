# -*- coding: utf-8 -*-
import logging

import werkzeug
from werkzeug.urls import url_encode

from odoo import _, http
from odoo.addons.auth_signup.controllers.main import AuthSignupHome as AuthSignupHomeBase
from odoo.addons.auth_signup.models.res_users import SignupError
from odoo.exceptions import UserError
from odoo.http import request

_logger = logging.getLogger(__name__)

class AuthSignupHome(AuthSignupHomeBase):
    """Truyền lms_register (giảng viên / học sinh) vào signup → xử lý ở res.users._signup_create_user."""

    def _prepare_signup_values(self, qcontext):
        values = super()._prepare_signup_values(qcontext)
        lr = (qcontext.get('lms_register') or '').strip()
        if lr in ('student', 'lecturer'):
            values['lms_register'] = lr
        if lr == 'lecturer':
            values['lms_lecturer_document'] = (qcontext.get('lms_lecturer_document') or '').strip()
        return values
    @http.route('/web/signup', type='http', auth='public', website=True, sitemap=False)
    def web_auth_signup(self, *args, **kw):
        qcontext = self.get_auth_signup_qcontext()

        if not qcontext.get('token') and not qcontext.get('signup_enabled'):
            raise werkzeug.exceptions.NotFound()

        if 'error' not in qcontext and request.httprequest.method == 'POST':
            try:
                if not request.env['ir.http']._verify_request_recaptcha_token('signup'):
                    raise UserError(_('Suspicious activity detected by Google reCaptcha.'))

                self.do_signup(qcontext)

                # Set user to public if they were not signed in by do_signup (mfa enabled)
                if request.session.uid is None:
                    public_user = request.env.ref('base.public_user')
                    request.update_env(user=public_user)

                # IMPORTANT: Không gửi mail mặc định của Odoo ở bước signup.
                pending_channel = (qcontext.get('lms_register') or '').strip()
                if pending_channel in ('student', 'lecturer'):
                    return request.redirect('/lms/signup/pending?channel=%s' % pending_channel, local=True)
                return request.redirect('/web/login', local=True)
            except UserError as e:
                qcontext['error'] = e.args[0]
            except (SignupError, AssertionError) as e:
                existing_user = (
                    request.env['res.users']
                    .sudo()
                    .search([('login', '=', qcontext.get('login'))], limit=1)
                )
                if existing_user and existing_user.lms_signup_channel in ('student', 'lecturer'):
                    return request.redirect(
                        '/lms/signup/pending?channel=%s' % existing_user.lms_signup_channel,
                        local=True,
                    )
                if existing_user:
                    qcontext['error'] = _(
                        'Email này đã được sử dụng. Vui lòng dùng email khác hoặc đăng nhập.'
                    )
                else:
                    _logger.warning('%s', e)
                    qcontext['error'] = _(
                        'Không thể tạo tài khoản mới. Vui lòng kiểm tra lại thông tin và thử lại.'
                    )
        elif 'signup_email' in qcontext:
            user = (
                request.env['res.users']
                .sudo()
                .search([('email', '=', qcontext.get('signup_email')), ('state', '!=', 'new')], limit=1)
            )
            if user:
                return request.redirect('/web/login?%s' % url_encode({'login': user.login, 'redirect': '/web'}))

        response = request.render('auth_signup.signup', qcontext)
        response.headers['X-Frame-Options'] = 'SAMEORIGIN'
        response.headers['Content-Security-Policy'] = "frame-ancestors 'self'"
        return response

    @http.route('/lms/signup/pending', type='http', auth='public', website=True)
    def lms_signup_pending(self, **kwargs):
        channel = (kwargs.get('channel') or '').strip()
        role_text = 'giảng viên' if channel == 'lecturer' else 'học sinh'
        icp = request.env['ir.config_parameter'].sudo()
        company = request.env.company.sudo()
        admin_contact_name = icp.get_param('lms.admin_contact_name') or company.name or 'LMS Admin'
        admin_contact_email = icp.get_param('lms.admin_contact_email') or company.email or ''
        admin_contact_phone = icp.get_param('lms.admin_contact_phone') or company.phone or ''
        return request.render('lms.signup_pending_approval', {
            'lms_pending_channel': channel,
            'lms_pending_role_text': role_text,
            'lms_admin_contact_name': admin_contact_name,
            'lms_admin_contact_email': admin_contact_email,
            'lms_admin_contact_phone': admin_contact_phone,
        })
