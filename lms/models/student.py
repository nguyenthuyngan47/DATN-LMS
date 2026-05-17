# -*- coding: utf-8 -*-
"""
Mọi bản ghi lms.student mới (form, Import CSV, RPC) đều đi qua ORM ``create()`` —
nếu không gán ``user_id``, hệ thống tự tạo ``res.users`` (login = email), tương tự ``lms.lecturer``.
"""

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, ValidationError

from . import face_embedding_utils
from odoo.tools.mail import email_normalize

_DEFAULT_STUDENT_AUTO_PASSWORD = "123456"

# Trạng thái đăng ký khóa (lms.student.course) — thứ tự theo luồng nghiệp vụ.
ENROLLMENT_STATUS_SELECTION = [
    ('pending', 'Pending'),
    ('approved', 'Approved'),
    ('learning', 'Learning'),
    ('completed', 'Completed'),
    ('rejected', 'Rejected'),
]

# Trạng thái không chiếm chỗ trong lớp (đồng bộ khái niệm với schema ERD / giới hạn max_student).
ENROLLMENT_STATUSES_EXCLUDED_FROM_CAPACITY = frozenset({'rejected', 'cancelled'})


class Student(models.Model):
    _name = 'lms.student'
    _description = 'Student'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char(string='Student Name', required=True, tracking=True)
    email = fields.Char(string='Email', required=True, tracking=True)
    
    @api.constrains('email')
    def _check_email(self):
        """Định dạng email theo chuẩn Odoo (odoo.tools.mail.email_normalize)."""
        for record in self:
            if not (record.email or '').strip():
                continue
            email_norm = email_normalize(record.email)
            if not email_norm:
                raise ValidationError(
                    _('Invalid email. Use standard email format (including manual entry or import).')
                )
            dup = self.search(
                [
                    ('id', '!=', record.id),
                    ('email', '=ilike', email_norm),
                ],
                limit=1,
            )
            if dup:
                raise ValidationError(_('Email already exists. Please use a different email.'))
    phone = fields.Char(string='Phone')
    image_1920 = fields.Image(string='Avatar', max_width=1920, max_height=1920)
    gender = fields.Selection(
        [('male', 'Male'), ('female', 'Female'), ('other', 'Other')],
        string='Gender',
        tracking=True,
    )
    date_of_birth = fields.Date(string='Date of Birth', tracking=True)
    address = fields.Char(string='Address', tracking=True)
    
    # Thông tin đầu vào
    current_level = fields.Selection([
        ('beginner', 'Beginner'),
        ('intermediate', 'Intermediate'),
        ('advanced', 'Advanced'),
    ], string='Current Level', default='beginner', required=True, tracking=True)
    manual_level_lock = fields.Boolean(
        string='Lock Level Manually',
        default=False,
        help='When enabled, the system will not auto-override current_level from average_score.',
    )
    
    learning_goals = fields.Text(string='Learning Goals', tracking=True)
    desired_skills = fields.Text(string='Desired Skills', tracking=True)

    face_embedding_json = fields.Text(string='Face Embedding (JSON)', copy=False)
    face_embedding_registered_at = fields.Datetime(string='Face Registered At', readonly=True, copy=False)
    face_enrollment_mount_html = fields.Html(
        string=' ',
        compute='_compute_face_enrollment_mount_html',
        sanitize=False,
    )

    # Quan hệ
    enrolled_courses_ids = fields.One2many(
        'lms.student.course', 'student_id', string='Enrolled Courses'
    )
    learning_history_ids = fields.One2many(
        'lms.learning.history', 'student_id', string='Learning History'
    )
    roadmap_ids = fields.One2many(
        'lms.roadmap', 'student_id', string='Suggested Roadmaps'
    )
    current_course_registration_status = fields.Selection(
        ENROLLMENT_STATUS_SELECTION,
        string='Registration Status (Current Course)',
        compute='_compute_current_course_registration_status',
        compute_sudo=True,
        search='_search_current_course_registration_status',
        inverse='_inverse_current_course_registration_status',
    )
    user_id = fields.Many2one(
        'res.users',
        string='User Account',
        required=False,
        ondelete='cascade',
        index=True,
        help='Leave empty: each time a record is created (including CSV import), the system auto-creates a res.users with login = email.',
    )
    username = fields.Char(
        string='Username',
        related='user_id.login',
        store=True,
        readonly=True,
    )
    last_login = fields.Datetime(
        string='Last Login',
        related='user_id.login_date',
        store=True,
        readonly=True,
    )
    is_instructor_restricted = fields.Boolean(
        string='Instructor Edit Restricted',
        compute='_compute_is_instructor_restricted',
    )
    is_current_user_account = fields.Boolean(
        string='Current User Account',
        compute='_compute_current_user_highlight',
        search='_search_is_current_user_account',
    )
    current_user_marker = fields.Char(
        string='Account Marker',
        compute='_compute_current_user_highlight',
    )

    # Thống kê
    total_courses = fields.Integer(string='Total Courses', compute='_compute_statistics', store=True)
    completed_courses = fields.Integer(string='Completed Courses', compute='_compute_statistics', store=True)
    average_score = fields.Float(string='Average Score', compute='_compute_statistics', store=True, digits=(16, 2))
    learning_progress = fields.Float(
        string='Learning Progress (%)',
        compute='_compute_statistics',
        store=True,
        digits=(16, 2),
    )
    learning_status = fields.Selection(
        [
            ('not_started', 'Not Started'),
            ('in_progress', 'Active'),
            ('inactive', 'Inactive'),
            ('completed', 'Completed'),
        ],
        string='Learning Status',
        compute='_compute_statistics',
        store=True,
    )
    total_study_time = fields.Float(string='Total Study Time (minutes)', compute='_compute_statistics', store=True, digits=(16, 2))
    last_activity_date = fields.Date(string='Last Activity', compute='_compute_statistics', store=True, index=True)
    
    # Trạng thái
    is_active = fields.Boolean(string='Active', default=True, tracking=True)
    inactive_days = fields.Integer(string='Inactive Days', compute='_compute_inactive_days', store=True, index=True)

    _sql_constraints = [
        ('student_user_unique', 'unique(user_id)', 'Each user account can only be linked to one student.'),
    ]

    @api.depends('user_id')
    def _compute_current_user_highlight(self):
        uid = self.env.uid
        for student in self:
            is_self = bool(student.user_id and student.user_id.id == uid)
            student.is_current_user_account = is_self
            student.current_user_marker = _('You') if is_self else ''

    def _search_is_current_user_account(self, operator, value):
        if operator not in ('=', '!='):
            raise ValidationError(_('Invalid search on current user account.'))
        is_match = bool(value)
        if operator == '!=':
            is_match = not is_match
        if is_match:
            return [('user_id', '=', self.env.uid)]
        return ['|', ('user_id', '=', False), ('user_id', '!=', self.env.uid)]

    @api.model
    def _needs_auto_student_user(self, vals):
        """True khi chưa có user hợp lệ (form, CSV, API đều truyền vals qua create)."""
        uid = vals.get('user_id')
        if uid in (False, None, '', 0):
            return True
        if isinstance(uid, str) and not uid.strip():
            return True
        return False

    @api.model
    def _prepare_student_user_on_create(self, vals):
        """Gán vals['user_id'] trước super().create — mọi insert ORM đều đi qua đây."""
        if not self._needs_auto_student_user(vals):
            return
        display_name = (vals.get('name') or '').strip()
        if not display_name:
            raise ValidationError(
                _('Student name is required to create or assign a login account.')
            )
        email_norm = email_normalize(vals.get('email'))
        if not email_norm:
            raise ValidationError(
                _('Invalid or empty email. Use standard email format (including CSV import).')
            )
        vals['email'] = email_norm
        login = email_norm
        Users = self.env['res.users'].sudo()
        existing = Users.search([('login', '=', login)], limit=1)
        if existing:
            if self.sudo().search_count([('user_id', '=', existing.id)]):
                raise ValidationError(
                    _('Email/login is already used by another student: %s') % login
                )
            vals['user_id'] = existing.id
            return
        student_group = self.env.ref('lms.group_lms_user', raise_if_not_found=False)
        internal_group = self.env.ref('base.group_user')
        group_ids = [internal_group.id]
        if student_group:
            group_ids.append(student_group.id)
        company = self.env.company
        user = Users.with_context(no_reset_password=True).create(
            {
                'name': display_name,
                'login': login,
                'email': email_norm,
                'company_id': company.id,
                'company_ids': [(6, 0, [company.id])],
                'groups_id': [(6, 0, group_ids)],
            }
        )
        user.write({'password': _DEFAULT_STUDENT_AUTO_PASSWORD})
        phone = (vals.get('phone') or '').strip()
        if phone:
            user.partner_id.sudo().write({'phone': phone})
        vals['user_id'] = user.id

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            self._prepare_student_user_on_create(vals)
            if vals.get('user_id') and not (vals.get('name') or '').strip():
                user = self.env['res.users'].browse(vals['user_id'])
                resolved = user._lms_resolve_person_name()
                if resolved:
                    vals['name'] = resolved
        return super().create(vals_list)

    @api.depends('face_embedding_json', 'create_date', 'write_date')
    def _compute_face_enrollment_mount_html(self):
        for rec in self:
            if isinstance(rec.id, int) and rec.id:
                registered = '1' if rec.face_embedding_json else '0'
                rec.face_enrollment_mount_html = (
                    '<div class="o_lms_student_face_root" data-lms-role="enroll" '
                    'data-student-id="%s" data-lms-face-registered="%s"></div>'
                    % (rec.id, registered)
                )
            else:
                rec.face_enrollment_mount_html = (
                    '<p class="text-muted">Save the student profile first, then return to this tab to register the face template.</p>'
                )

    def _normalize_face_image_base64(self, image_base64):
        """Strip data-URL prefix; return raw base64 for Image fields."""
        if not image_base64 or not isinstance(image_base64, str):
            return False
        raw = image_base64.strip()
        if raw.startswith('data:'):
            raw = raw.split(',', 1)[-1].strip()
        return raw or False

    def _sync_user_avatar_from_face_image(self):
        """Copy student avatar to linked Odoo user (navbar / profile)."""
        for student in self:
            if student.user_id and student.image_1920:
                student.user_id.sudo().write({'image_1920': student.image_1920})

    def _apply_face_capture_image(self, image_base64):
        """Save webcam JPEG as student avatar and sync to res.users."""
        image_b64 = self._normalize_face_image_base64(image_base64)
        if not image_b64:
            return
        self.write({'image_1920': image_b64})
        self._sync_user_avatar_from_face_image()

    def action_save_face_embedding_json(self, embedding_json, image_base64=None):
        """RPC/JS: save face embedding (student only); optional photo → avatar."""
        self.ensure_one()
        if self.user_id != self.env.user:
            raise AccessError(_('Only students can register their own face template.'))
        if not isinstance(embedding_json, str) or not embedding_json.strip():
            raise ValidationError(_('Missing embedding data.'))
        self.write(
            {
                'face_embedding_json': embedding_json.strip(),
                'face_embedding_registered_at': fields.Datetime.now(),
            }
        )
        self._apply_face_capture_image(image_base64)
        return {'lms_face_result': True, 'message': _('Face template saved.')}

    def write(self, vals):
        """Chuẩn hóa email nếu có; quyền chỉnh sửa được kiểm soát chủ yếu ở UI/rule."""
        if 'email' in vals and vals.get('email'):
            email_norm = email_normalize(vals['email'])
            if not email_norm:
                raise ValidationError(
                    _('Invalid email. Use standard email format (including manual entry or import).')
                )
            vals = dict(vals, email=email_norm)
        if 'face_embedding_json' in vals:
            privileged = self.env.user.has_group('base.group_system') or self.env.user.has_group(
                'lms.group_lms_manager'
            )
            if not privileged:
                for rec in self:
                    if rec.user_id != self.env.user:
                        raise AccessError(_('Only students can update their own face template.'))
            raw = vals.get('face_embedding_json')
            if raw:
                if len(raw) > 65536:
                    raise ValidationError(_('Embedding data is too large.'))
                if not face_embedding_utils.parse_embedding(raw):
                    raise ValidationError(
                        _('Invalid embedding (requires exactly %s floating point numbers).')
                        % face_embedding_utils.FACE_EMBEDDING_DIM
                    )
        return super().write(vals)

    def _compute_current_course_registration_status(self):
        """Trạng thái đăng ký của học sinh theo course_id trong context."""
        course_id = self.env.context.get('course_id') or self.env.context.get('active_id')
        if not course_id:
            for rec in self:
                rec.current_course_registration_status = False
            return
        enrollments = self.env['lms.student.course'].sudo().search(
            [('student_id', 'in', self.ids), ('course_id', '=', course_id)]
        )
        by_student = {e.student_id.id: e.status for e in enrollments}
        for rec in self:
            rec.current_course_registration_status = by_student.get(rec.id) or False

    def _search_current_course_registration_status(self, operator, value):
        """
        Cho phép filter/search theo trạng thái đăng ký của khóa học hiện tại
        (course_id lấy từ context của action mở từ nút "Học viên").
        """
        course_id = self.env.context.get('course_id') or self.env.context.get('active_id')
        if not course_id:
            return [('id', '=', 0)]
        enrollments = self.env['lms.student.course'].sudo().search(
            [('course_id', '=', course_id)]
        )
        student_ids = enrollments.filtered(lambda e: e.status == value).mapped('student_id').ids
        if operator in ('=', '=='):
            return [('id', 'in', student_ids or [0])]
        if operator in ('!=', '<>'):
            return [('id', 'not in', student_ids)]
        # Fallback an toàn cho các operator khác không dùng trong filter hiện tại.
        return [('id', '=', 0)]

    def _inverse_current_course_registration_status(self):
        """Cho phép đổi trạng thái trực tiếp trên form sinh viên theo course trong context."""
        for rec in self:
            if not rec.current_course_registration_status:
                continue
            self._set_current_course_status(
                rec.current_course_registration_status,
                students=rec,
                notify=False,
            )

    def _set_current_course_status(self, new_status, students=None, notify=True):
        """Đổi trạng thái đăng ký theo khóa học hiện tại cho nhiều sinh viên."""
        students = students or self
        course_id = self.env.context.get('course_id') or self.env.context.get('active_id')
        if not course_id:
            if not notify:
                return False
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Update Status'),
                    'message': _('Can only be used when opened from the Course Students screen.'),
                    'type': 'warning',
                    'sticky': False,
                },
            }
        enrollments = self.env['lms.student.course'].sudo().search(
            [('student_id', 'in', students.ids), ('course_id', '=', course_id)]
        )
        updated = enrollments.filtered(lambda e: e.status != new_status)
        if updated:
            updated.write({'status': new_status})
        if not notify:
            return True
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Update Status'),
                'message': _(
                    'Updated status "%s" for %s students.'
                ) % (
                    dict(self._fields['current_course_registration_status'].selection).get(new_status, new_status),
                    len(updated),
                ),
                'type': 'success',
                'sticky': False,
                'next': {'type': 'ir.actions.client', 'tag': 'reload'},
            },
        }

    def _compute_is_instructor_restricted(self):
        """
        Giáo viên chỉ được chỉnh trạng thái đăng ký (trong flow theo khóa học),
        không được sửa các thông tin khác của hồ sơ sinh viên.
        """
        user = self.env.user
        restricted = (
            user.has_group('lms.group_lms_instructor')
            and not user.has_group('lms.group_lms_manager')
            and not user.has_group('base.group_system')
        )
        for rec in self:
            rec.is_instructor_restricted = restricted

    def action_set_course_status_pending(self):
        return self._set_current_course_status('pending')

    def action_set_course_status_approved(self):
        return self._set_current_course_status('approved')

    def action_set_course_status_rejected(self):
        return self._set_current_course_status('rejected')

    def action_set_course_status_learning(self):
        return self._set_current_course_status('learning')

    def action_set_course_status_completed(self):
        return self._set_current_course_status('completed')

    @api.depends(
        'learning_history_ids',
        'learning_history_ids.date',
        'learning_history_ids.study_duration',
        'enrolled_courses_ids',
        'enrolled_courses_ids.status',
        'enrolled_courses_ids.final_score',
        'enrolled_courses_ids.progress',
    )
    def _compute_statistics(self):
        today = fields.Date.today()
        for record in self:
            record.total_courses = len(record.enrolled_courses_ids)
            record.completed_courses = len(record.enrolled_courses_ids.filtered(lambda x: x.status == 'completed'))
            
            # Chỉ tính điểm trung bình từ các khóa đã hoàn thành.
            completed_enrollments = record.enrolled_courses_ids.filtered(lambda x: x.status == 'completed')
            scores = completed_enrollments.mapped('final_score')
            scores = [s for s in scores if s is not None and s is not False]
            record.average_score = (sum(scores) / len(scores)) if scores else 0.0
            record.learning_progress = (
                sum(record.enrolled_courses_ids.mapped('progress')) / len(record.enrolled_courses_ids)
            ) if record.enrolled_courses_ids else 0.0
            
            # Tính tổng thời gian học
            record.total_study_time = sum(record.learning_history_ids.mapped('study_duration'))
            
            # Ngày hoạt động cuối
            if record.learning_history_ids:
                dates = record.learning_history_ids.mapped('date')
                # Lọc bỏ các giá trị None/False
                valid_dates = [d for d in dates if d]
                if valid_dates:
                    max_datetime = max(valid_dates)
                    # Chuyển đổi Datetime sang Date
                    if hasattr(max_datetime, 'date'):
                        record.last_activity_date = max_datetime.date()
                    else:
                        record.last_activity_date = max_datetime
                else:
                    record.last_activity_date = False
            else:
                record.last_activity_date = False

            # Quy ước trạng thái theo nghiệp vụ:
            # 1) 0 tiến độ (ví dụ 0/5) => Chưa hoạt động
            # 2) 100% hoặc completed đủ số khóa => Hoàn thành
            # 3) Có tiến độ và có hoạt động <= 7 ngày => Đang hoạt động
            # 4) Có tiến độ nhưng > 7 ngày không hoạt động => Không hoạt động
            # Rule nghiệp vụ chốt:
            # - 0/x (chưa hoàn thành khóa nào) => Chưa hoạt động.
            if record.total_courses == 0 or record.completed_courses == 0:
                record.learning_status = 'not_started'
            elif record.completed_courses == record.total_courses or record.learning_progress >= 100.0:
                record.learning_status = 'completed'
            else:
                is_recent = bool(
                    record.last_activity_date
                    and (today - record.last_activity_date).days <= 7
                )
                record.learning_status = 'in_progress' if is_recent else 'inactive'

            # Boolean cũ giữ lại để filter nhanh: chỉ "Đang hoạt động" mới True.
            record.is_active = record.learning_status == 'in_progress'

    @api.model
    def _classify_level_by_score(self, score):
        """Phân loại trình độ theo điểm trung bình trên thang 10."""
        if score is None:
            return 'beginner'
        normalized = score
        if normalized > 10:
            normalized = normalized / 10.0
        normalized = max(0.0, min(10.0, normalized))
        if normalized < 5.0:
            return 'beginner'
        if normalized < 8.0:
            return 'intermediate'
        return 'advanced'
    
    @api.depends('last_activity_date')
    def _compute_inactive_days(self):
        """Tính số ngày không hoạt động và gửi email nhắc nhở nếu > 7 ngày"""
        today = fields.Date.today()
        for record in self:
            old_inactive_days = record.inactive_days
            if record.last_activity_date:
                record.inactive_days = (today - record.last_activity_date).days
            else:
                record.inactive_days = 0
            
            # Gửi email nhắc nhở nếu không hoạt động > 7 ngày (chỉ gửi 1 lần)
            if record.inactive_days > 7 and old_inactive_days <= 7 and record.email:
                template = self.env.ref('lms.email_template_inactive_reminder', raise_if_not_found=False)
                if template:
                    template.send_mail(record.id, force_send=True)
    
    def action_view_roadmaps(self):
        """Mở danh sách roadmap của học viên."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Roadmap',
            'res_model': 'lms.roadmap',
            'view_mode': 'kanban,list,form',
            'domain': [('student_id', '=', self.id)],
            'context': {'default_student_id': self.id},
        }

    def action_refresh_statistics(self):
        """Tính lại thống kê (dùng sau import SQL hoặc đồng bộ dữ liệu)."""
        if not self:
            return True
        self._compute_statistics()
        # Trình độ là field thường — cập nhật bằng write (tránh gán trong compute của field khác).
        for record in self:
            new_level = record._classify_level_by_score(record.average_score)
            if not record.manual_level_lock and record.current_level != new_level:
                record.write({'current_level': new_level})
        self._compute_inactive_days()
        # Đẩy các trường compute có store ra DB (sau import SQL / gọi compute tay).
        stored_stats = [
            'total_courses',
            'completed_courses',
            'average_score',
            'learning_progress',
            'learning_status',
            'total_study_time',
            'last_activity_date',
            'inactive_days',
        ]
        self.flush_recordset(stored_stats)
        return True


class StudentCourse(models.Model):
    _name = 'lms.student.course'
    _description = 'Student Course Enrollment'
    _rec_name = 'course_id'

    student_id = fields.Many2one('lms.student', string='Student', required=True, ondelete='cascade')
    course_id = fields.Many2one(
        'lms.course', string='Course', required=True, ondelete='cascade'
    )
    
    enrollment_date = fields.Date(string='Enrollment Date', default=fields.Date.today, required=True)
    start_date = fields.Date(string='Start Date')
    end_date = fields.Date(
        string='End Date',
        help='Planned end of the study period for this enrollment (often copied from the course).',
    )
    completion_date = fields.Date(string='Completion Date')
    
    status = fields.Selection(
        ENROLLMENT_STATUS_SELECTION,
        string='Status',
        default='pending',
        tracking=True,
    )

    _sql_constraints = [
        ('student_course_unique', 'unique(student_id, course_id)', 'Student has already enrolled in this course!'),
    ]

    @api.model
    def _should_enforce_prerequisite_rules(self):
        return not self.env['lms.course']._user_may_bypass_prerequisite_rules()

    def _check_prerequisites_for_enrollment(self):
        for rec in self:
            if rec.status == 'rejected':
                continue
            message = rec.course_id._prerequisite_error_message(rec.student_id)
            if message:
                raise ValidationError(message)

    progress = fields.Float(
        string='Progress (%)',
        compute='_compute_progress',
        store=True,
        digits=(16, 2),
    )
    final_score = fields.Float(string='Final Score', digits=(16, 2))

    lesson_progress_ids = fields.One2many(
        'lms.student.lesson.progress',
        'enrollment_id',
        string='Lesson Progress',
    )
    learning_history_ids = fields.One2many(
        'lms.learning.history', 'student_course_id', string='Learning History'
    )

    @api.depends(
        'student_id',
        'course_id',
        'course_id.lesson_ids',
        'lesson_progress_ids.face_checked_in',
        'lesson_progress_ids.progress_percent',
        'lesson_progress_ids.watched_seconds',
        'lesson_progress_ids.lesson_id',
        'learning_history_ids.status',
        'learning_history_ids.lesson_id',
    )
    def _compute_progress(self):
        """Completed lessons / total lessons in course (same rules as lesson Attendance Status)."""
        Progress = self.env['lms.student.lesson.progress'].sudo()
        for enrollment in self:
            student = enrollment.student_id
            course = enrollment.course_id
            lessons = course.lesson_ids if course else self.env['lms.lesson']
            if not student or not lessons:
                enrollment.progress = 0.0
                continue
            progress_map = {
                row.lesson_id.id: row
                for row in Progress.search([
                    ('student_id', '=', student.id),
                    ('lesson_id', 'in', lessons.ids),
                ])
            }
            done_lesson_ids = {
                lesson.id
                for lesson in lessons
                if progress_map.get(lesson.id) and progress_map[lesson.id].is_lesson_completed()
            }
            for history in enrollment.learning_history_ids:
                if (
                    history.status == 'completed'
                    and history.lesson_id
                    and history.lesson_id.id in lessons.ids
                ):
                    done_lesson_ids.add(history.lesson_id.id)
            enrollment.progress = min(100.0, (len(done_lesson_ids) / len(lessons)) * 100.0)

    @api.model
    def _enrollment_status_counts_toward_capacity(cls, status):
        return status not in ENROLLMENT_STATUSES_EXCLUDED_FROM_CAPACITY

    @api.constrains('start_date', 'end_date')
    def _check_enrollment_start_before_end(self):
        for rec in self:
            if rec.start_date and rec.end_date and rec.start_date > rec.end_date:
                raise ValidationError(_('Enrollment start date must be on or before the end date.'))

    @api.constrains('start_date', 'completion_date')
    def _check_enrollment_start_before_completion(self):
        for rec in self:
            if rec.start_date and rec.completion_date and rec.start_date > rec.completion_date:
                raise ValidationError(_('Enrollment start date must be on or before the completion date.'))

    @api.constrains('course_id', 'status')
    def _check_course_seat_capacity(self):
        for rec in self:
            if not rec.course_id:
                continue
            cap = rec.course_id.max_student
            if not cap or cap < 1:
                continue
            domain = [
                ('course_id', '=', rec.course_id.id),
                ('status', 'not in', list(ENROLLMENT_STATUSES_EXCLUDED_FROM_CAPACITY)),
            ]
            if rec.id:
                domain.append(('id', '!=', rec.id))
            other = self.search_count(domain)
            need_seat = self._enrollment_status_counts_toward_capacity(rec.status)
            if need_seat and other + 1 > cap:
                raise ValidationError(
                    _('Course "%s" is full (maximum %s students).') % (rec.course_id.name, cap)
                )

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        if records and records._should_enforce_prerequisite_rules():
            records._check_prerequisites_for_enrollment()
        records.mapped('student_id').action_refresh_statistics()
        return records

    def write(self, vals):
        if self._should_enforce_prerequisite_rules() and vals.get('status') in (
            'pending',
            'approved',
            'learning',
        ):
            for rec in self:
                check_student = rec.student_id
                check_course = rec.course_id
                if vals.get('course_id'):
                    check_course = self.env['lms.course'].browse(vals['course_id'])
                if vals.get('student_id'):
                    check_student = self.env['lms.student'].browse(vals['student_id'])
                message = check_course._prerequisite_error_message(check_student)
                if message:
                    raise ValidationError(message)
        students = self.mapped('student_id')
        res = super().write(vals)
        (students | self.mapped('student_id')).action_refresh_statistics()
        return res

    def unlink(self):
        students = self.mapped('student_id')
        res = super().unlink()
        if not self.env.context.get('skip_lms_statistics_refresh'):
            students.action_refresh_statistics()
        return res

    def action_bulk_approve_pending(self):
        """Duyệt hàng loạt đăng ký đang chờ (pending → approved)."""
        pending = self.filtered(lambda r: r.status == 'pending')
        if not pending:
            raise ValidationError(_('No pending enrollments in the selected list.'))
        pending.write({'status': 'approved'})
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Approve Enrollments'),
                'message': _('Approved %(count)s enrollment(s).', count=len(pending)),
                'type': 'success',
                'sticky': False,
                'next': {'type': 'ir.actions.client', 'tag': 'reload'},
            },
        }

    def action_bulk_set_learning(self):
        """Chuyển đăng ký đã duyệt sang đang học (approved → learning)."""
        approved = self.filtered(lambda r: r.status == 'approved')
        if not approved:
            raise ValidationError(_('No approved enrollments in the selected list.'))
        if approved._should_enforce_prerequisite_rules():
            approved._check_prerequisites_for_enrollment()
        approved.write({'status': 'learning'})
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Start Learning'),
                'message': _('Set %(count)s enrollment(s) to Learning.', count=len(approved)),
                'type': 'success',
                'sticky': False,
                'next': {'type': 'ir.actions.client', 'tag': 'reload'},
            },
        }

    @api.model
    def action_merge_duplicate_enrollments(self):
        """
        Gộp các bản ghi đăng ký trùng (cùng student_id + course_id).
        Giữ bản id nhỏ nhất; chuyển lịch sử sang bản giữ; gộp ngày/điểm/trạng thái hợp lý.
        """
        SC = self.sudo()
        groups = {}
        for sc in SC.search([]):
            if not sc.student_id or not sc.course_id:
                continue
            key = (sc.student_id.id, sc.course_id.id)
            groups.setdefault(key, []).append(sc)
        merged = 0
        History = self.env['lms.learning.history'].sudo()
        skip_ctx = {'skip_lms_statistics_refresh': True, 'skip_lms_student_course_relink': True}
        status_rank = {
            'completed': 6,
            'learning': 5,
            'approved': 4,
            'pending': 3,
            'rejected': 2,
        }

        def pick_status(a, b):
            return a if status_rank.get(a or '', 0) >= status_rank.get(b or '', 0) else b

        for key, rows in groups.items():
            if len(rows) <= 1:
                continue
            rows.sort(key=lambda r: r.id)
            keep = rows[0]
            for dup in rows[1:]:
                History.search([('student_course_id', '=', dup.id)]).with_context(**skip_ctx).write(
                    {'student_course_id': keep.id}
                )
                vals = {}
                if dup.enrollment_date and (not keep.enrollment_date or dup.enrollment_date < keep.enrollment_date):
                    vals['enrollment_date'] = dup.enrollment_date
                if dup.start_date and (not keep.start_date or dup.start_date < keep.start_date):
                    vals['start_date'] = dup.start_date
                if dup.end_date and (not keep.end_date or dup.end_date > keep.end_date):
                    vals['end_date'] = dup.end_date
                if dup.completion_date and (not keep.completion_date or dup.completion_date > keep.completion_date):
                    vals['completion_date'] = dup.completion_date
                fs_keep = keep.final_score or 0
                fs_dup = dup.final_score or 0
                if fs_dup > fs_keep:
                    vals['final_score'] = fs_dup
                new_st = pick_status(keep.status, dup.status)
                if new_st != keep.status:
                    vals['status'] = new_st
                if vals:
                    keep.with_context(skip_lms_statistics_refresh=True).write(vals)
                dup.with_context(skip_lms_statistics_refresh=True).unlink()
                merged += 1
        return merged


