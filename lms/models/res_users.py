# -*- coding: utf-8 -*-

import logging

from odoo import api, fields, models
from odoo.tools.mail import email_normalize

_logger = logging.getLogger(__name__)
_LMS_SILENT_MAIL_CTX = {
    'no_reset_password': True,
    'mail_create_nolog': True,
    'mail_notrack': True,
    'tracking_disable': True,
    'skip_lms_welcome_mail': True,
}
_LMS_PROFILE_SYNC_CTX = {
    'mail_create_nolog': True,
    'mail_create_nosubscribe': True,
    'mail_notrack': True,
    'tracking_disable': True,
}

class ResUsers(models.Model):
    _inherit = 'res.users'
    state = fields.Selection(selection_add=[('pending_approval', 'Pending Approval')])
    lms_signup_channel = fields.Selection(
        selection=[
            ('student', 'Học viên'),
            ('lecturer', 'Giảng viên'),
        ],
        string='Kênh đăng ký LMS',
        readonly=True,
        copy=False,
    )
    certificate_link = fields.Text(
        string='Link Document',
        copy=False,
        help='Link chứng chỉ, CV, lịch sử công việc,... (đăng ký giảng viên).',
    )
    lms_welcome_mail_sent = fields.Boolean(
        string='LMS Welcome Mail Sent',
        default=False,
        copy=False,
        help='Đánh dấu đã gửi mail chào mừng giảng viên để tránh gửi lặp.',
    )

    @api.depends_context('lang')
    @api.depends('groups_id', 'lms_signup_channel')
    def _compute_state(self):
        super()._compute_state()
        for user in self:
            if user.lms_signup_channel == 'lecturer':
                user.state = 'active' if user.has_group('lms.group_lms_instructor') else 'pending_approval'
            elif user.lms_signup_channel == 'student':
                user.state = 'active' if user.has_group('lms.group_lms_user') else 'pending_approval'

    @api.model
    def _signup_create_user(self, values):
        """
        Đăng ký công khai: mặc định Odoo tạo user kiểu Portal (template).
        - lms_register == 'student': chuyển sang user type Public (base.group_public).
        - lms_register == 'lecturer' hoặc không có: giữ Portal; lưu certificate_link.
        """
        vals = dict(values or {})
        lms_register = vals.pop('lms_register', None)
        doc_link = vals.pop('lms_lecturer_document', None)
        if lms_register in ('lecturer', 'student'):
            user = super(
                ResUsers, self.with_context(**_LMS_SILENT_MAIL_CTX)
            )._signup_create_user(vals)
        else:
            user = super()._signup_create_user(vals)
        if not user:
            return user

        if lms_register == 'student':
            w = {'lms_signup_channel': 'student', 'state': 'pending_approval'}
            portal = self.env.ref('base.group_portal')
            public = self.env.ref('base.group_public')
            if user.has_group('base.group_portal'):
                w['groups_id'] = [(3, portal.id), (4, public.id)]
            user.sudo().with_context(**_LMS_SILENT_MAIL_CTX).write(w)
        elif lms_register == 'lecturer':
            user.sudo().with_context(**_LMS_SILENT_MAIL_CTX).write({
                'lms_signup_channel': 'lecturer',
                'certificate_link': (doc_link or '').strip() or False,
                'state': 'pending_approval',
            })
        return user

    def _lms_is_internal_for_profile_sync(self):
        """User nội bộ (backend): không portal/public; ưu tiên ``base.group_user``."""
        self.ensure_one()
        if self.has_group('base.group_portal') or self.has_group('base.group_public'):
            return False
        if self.has_group('base.group_user'):
            return True
        # Đã gán nhóm LMS nhưng cache/group_user chưa khớp ngay sau write (Odoo 18)
        if self.has_group('lms.group_lms_instructor') or self.has_group('lms.group_lms_user'):
            return True
        if 'share' in self._fields:
            return not self.share
        return False

    def _lms_sync_profile_records(self):
        """
        Khi user là Internal và được gán nhóm LMS Instructor / Student,
        tạo ``lms.lecturer`` hoặc ``lms.student`` nếu chưa có (gắn ``user_id`` + email).
        """
        Lecturer = self.env['lms.lecturer'].sudo().with_context(**_LMS_PROFILE_SYNC_CTX)
        Student = self.env['lms.student'].sudo().with_context(**_LMS_PROFILE_SYNC_CTX)
        # Đẩy groups_id xuống DB trước khi đọc lại (tránh cache Many2many lệch sau write).
        if hasattr(self.env, 'flush_all'):
            self.env.flush_all()
        # Xóa cache nhóm sau write để has_group khớp (form Odoo 18 không luôn gửi groups_id trong vals).
        self.invalidate_recordset(['groups_id', 'share', 'email', 'login', 'name'])
        fresh = self.env['res.users'].sudo().browse(self.ids)
        for user in fresh:
            if not user._lms_is_internal_for_profile_sync():
                continue
            email_norm = email_normalize(user.email or user.login or '')
            if not email_norm:
                _logger.warning(
                    'LMS: bỏ qua đồng bộ hồ sơ — user %s không có email/login hợp lệ',
                    user.id,
                )
                continue
            display_name = (user.name or '').strip() or email_norm
            # Một user: ưu tiên hồ sơ giảng viên nếu có nhóm Instructor; không thì Student.
            if user.has_group('lms.group_lms_instructor'):
                if not Lecturer.search([('user_id', '=', user.id)], limit=1):
                    Lecturer.create({
                        'user_id': user.id,
                        'full_name': display_name,
                        'email': email_norm,
                    })
            elif user.has_group('lms.group_lms_user'):
                if not Student.search([('user_id', '=', user.id)], limit=1):
                    Student.create({
                        'user_id': user.id,
                        'name': display_name,
                        'email': email_norm,
                    })

    @api.model_create_multi
    def create(self, vals_list):
        users = super().create(vals_list)
        if not self.env.context.get('skip_lms_profile_sync'):
            users._lms_sync_profile_records()
        return users

    def write(self, vals):
        skip_welcome = self.env.context.get('skip_lms_welcome_mail')
        tracks = {}
        should_check_welcome = not skip_welcome
        instructor_group = self.env.ref('lms.group_lms_instructor', raise_if_not_found=False)
        student_group = self.env.ref('lms.group_lms_user', raise_if_not_found=False)
        should_check_welcome = should_check_welcome and bool(instructor_group or student_group)
        if should_check_welcome:
            for user in self:
                tracks[user.id] = {
                    'old_has_instructor': user.has_group('lms.group_lms_instructor'),
                    'old_has_student': user.has_group('lms.group_lms_user'),
                    'old_welcome_sent': user.lms_welcome_mail_sent,
                    'channel': user.lms_signup_channel,
                }

        res = super().write(vals)
        if self.env.context.get('skip_lms_profile_sync'):
            return res
        # Luôn thử đồng bộ: form Odoo 18 có thể không gửi groups_id trong vals (lưu quyền theo bước khác).
        self._lms_sync_profile_records()
        
        if should_check_welcome:
            lecturer_template = self.env.ref('lms.email_template_lecturer_welcome', raise_if_not_found=False)
            student_template = self.env.ref('lms.email_template_student_welcome', raise_if_not_found=False)
            for user in self:
                t = tracks.get(user.id, {})
                if t.get('old_welcome_sent') or user.lms_welcome_mail_sent:
                    continue

                template = None
                if (
                    t.get('channel') == 'lecturer'
                    and not t.get('old_has_instructor')
                    and user.has_group('lms.group_lms_instructor')
                ):
                    template = lecturer_template
                elif (
                    t.get('channel') == 'student'
                    and not t.get('old_has_student')
                    and user.has_group('lms.group_lms_user')
                ):
                    template = student_template

                if template:
                    sent_ok = False
                    try:
                        template.send_mail(user.id, force_send=True)
                        sent_ok = True
                    except Exception:
                        _logger.exception('LMS: gửi welcome mail thất bại cho user %s', user.id)
                    if sent_ok:
                        user.sudo().with_context(skip_lms_welcome_mail=True).write({
                            'lms_welcome_mail_sent': True,
                        })
                elif t.get('channel') in ('lecturer', 'student'):
                    _logger.info('LMS: không tìm thấy mail template welcome cho user %s', user.id)
        return res
