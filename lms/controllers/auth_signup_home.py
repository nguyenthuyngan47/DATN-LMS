# -*- coding: utf-8 -*-

from odoo.addons.auth_signup.controllers.main import AuthSignupHome as AuthSignupHomeBase


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
