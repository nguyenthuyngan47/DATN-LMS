# -*- coding: utf-8 -*-

from dateutil.relativedelta import relativedelta
import os

from odoo import _, api, fields, models
from odoo.api import NewId
from odoo.exceptions import UserError, ValidationError

from ..services import google_calendar_sync
from . import face_embedding_utils
from .student import ENROLLMENT_STATUSES_EXCLUDED_FROM_CAPACITY


class CourseCategory(models.Model):
    _name = 'lms.course.category'
    _description = 'Course Category'
    _order = 'sequence, name'

    name = fields.Char(string='Category Name', required=True)
    sequence = fields.Integer(string='Sequence', default=10)
    description = fields.Text(string='Description')
    course_ids = fields.One2many('lms.course', 'category_id', string='Courses')


class CourseLevel(models.Model):
    _name = 'lms.course.level'
    _description = 'Course Level'
    _order = 'sequence, name'

    name = fields.Char(string='Level Name', required=True)
    sequence = fields.Integer(string='Sequence', default=10)
    description = fields.Text(string='Description')
    course_ids = fields.One2many('lms.course', 'level_id', string='Courses')


class CourseTag(models.Model):
    _name = 'lms.course.tag'
    _description = 'Course Tag'
    _order = 'name'

    name = fields.Char(string='Tag Name', required=True)
    color = fields.Integer(string='Color')
    course_ids = fields.Many2many('lms.course', 'course_tag_rel', 'tag_id', 'course_id', string='Courses')


class Course(models.Model):
    _name = 'lms.course'
    _description = 'Course'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char(string='Course Name', required=True, tracking=True)
    # mail.tracking không hỗ trợ field Html, chỉ giữ hiển thị nội dung.
    description = fields.Html(string='Description')
    image_1920 = fields.Image(string='Course Image', max_width=1920, max_height=1920)
    
    # Phân loại
    category_id = fields.Many2one('lms.course.category', string='Category', required=True, tracking=True)
    level_id = fields.Many2one('lms.course.level', string='Level', required=True, tracking=True)
    tag_ids = fields.Many2many('lms.course.tag', 'course_tag_rel', 'course_id', 'tag_id', string='Tags')
    
    # Thông tin khóa học
    instructor_id = fields.Many2one('res.users', string='Instructor', tracking=True)
    duration_hours = fields.Float(string='Duration (hours)', digits=(16, 2), tracking=True)
    max_student = fields.Integer(
        string='Max Students',
        default=15,
        help='Maximum concurrent enrollments counting pending, approved, learning, and completed '
             '(rejected/cancelled do not use a seat).',
    )
    start_date = fields.Date(string='Start Date', tracking=True)
    end_date = fields.Date(string='End Date', tracking=True)
    # VND không dùng phần thập phân -> lưu số nguyên để tránh hiển thị 100,000.00
    price = fields.Integer(string='Cost (VND)', default=0, tracking=True)
    contact_payment = fields.Text(string='Instructor Contact', tracking=True)
    prerequisite_ids = fields.Many2many(
        'lms.course', 'course_prerequisite_rel', 'course_id', 'prerequisite_id',
        string='Prerequisites'
    )
    
    # Nội dung
    lesson_ids = fields.One2many('lms.lesson', 'course_id', string='Lessons')
    total_lessons = fields.Integer(string='Total Lessons', compute='_compute_total_lessons', store=True)
    
    # Thống kê
    enrolled_students_count = fields.Integer(string='Enrolled Students', compute='_compute_enrolled_students', store=True)
    average_rating = fields.Float(string='Average Rating', digits=(16, 2))
    show_register_button = fields.Boolean(
        string='Show Register Button',
        compute='_compute_current_user_registration_state',
    )
    show_cancel_button = fields.Boolean(
        string='Show Cancel Button',
        compute='_compute_current_user_registration_state',
    )
    show_learning_content_tabs = fields.Boolean(
        string='Show Learning Tabs',
        compute='_compute_current_user_registration_state',
    )
    is_student_course_readonly = fields.Boolean(
        string='Course Form Read-only (Student)',
        compute='_compute_is_student_course_readonly',
    )

    # Trạng thái
    state = fields.Selection([
        ('draft', 'Draft'),
        ('published', 'Published'),
        ('archived', 'Archived'),
    ], string='Status', default='draft', tracking=True)
    
    is_active = fields.Boolean(string='Active', default=True)

    @api.model
    def default_get(self, fields_list):
        """Giáo viên (không phải Admin LMS/Settings) tạo khóa mới: mặc định instructor là chính họ (cần cho ir.rule ghi)."""
        res = super().default_get(fields_list)
        if 'instructor_id' in fields_list and not res.get('instructor_id'):
            user = self.env.user
            if user.has_group('lms.group_lms_instructor') and not (
                user.has_group('lms.group_lms_manager') or user.has_group('base.group_system')
            ):
                res['instructor_id'] = user.id
        return res

    @api.model
    def _sanitize_price_in_vals(self, vals):
        """Chuẩn hóa price từ form/import/API: rỗng -> 0, kiểu khác -> int."""
        if 'price' not in vals:
            vals['price'] = 0
            return vals
        raw = vals.get('price')
        if raw in (None, False, ''):
            vals['price'] = 0
            return vals
        try:
            vals['price'] = int(raw)
        except (TypeError, ValueError) as e:
            raise ValidationError('Course cost must be an integer (VND).') from e
        return vals

    @api.model_create_multi
    def create(self, vals_list):
        vals_list = [self._sanitize_price_in_vals(dict(vals)) for vals in vals_list]
        return super().create(vals_list)

    def write(self, vals):
        vals = self._sanitize_price_in_vals(dict(vals)) if 'price' in vals else vals
        return super().write(vals)

    @api.constrains('duration_hours')
    def _check_duration_hours(self):
        """Kiểm tra thời lượng khóa học không được âm"""
        for record in self:
            if record.duration_hours and record.duration_hours < 0:
                raise ValueError('Course duration cannot be negative')

    @api.constrains('start_date', 'end_date')
    def _check_course_start_end_dates(self):
        for record in self:
            if record.start_date and record.end_date and record.start_date > record.end_date:
                raise ValidationError(_('Course start date must be on or before the end date.'))

    @api.constrains('price')
    def _check_price_non_negative(self):
        for record in self:
            if record.price is not None and record.price < 0:
                raise ValueError('Course cost cannot be negative')

    @api.constrains('max_student')
    def _check_max_student_minimum(self):
        for record in self:
            if record.max_student in (False, None):
                continue
            if record.max_student < 1:
                raise ValidationError(_('When set, max students must be at least 1.'))

    @api.constrains('max_student', 'student_course_ids', 'student_course_ids.status')
    def _check_max_student_vs_occupied(self):
        for record in self:
            cap = record.max_student
            if not cap:
                continue
            occupied = len(
                record.student_course_ids.filtered(
                    lambda e: e.status not in ENROLLMENT_STATUSES_EXCLUDED_FROM_CAPACITY
                )
            )
            if occupied > cap:
                raise ValidationError(
                    _(
                        'This course already has %(occ)s active student(s); max students '
                        'cannot be set below %(occ)s.',
                    )
                    % {'occ': occupied}
                )

    @api.constrains('prerequisite_ids')
    def _check_prerequisite_cycle(self):
        """Kiểm tra prerequisite không được tạo vòng lặp"""
        for record in self:
            if record.id in record.prerequisite_ids.ids:
                raise ValueError('A course cannot be a prerequisite of itself')
            # Kiểm tra vòng lặp gián tiếp (đệ quy)
            visited = set()
            to_check = list(record.prerequisite_ids.ids)
            while to_check:
                prereq_id = to_check.pop()
                if prereq_id == record.id:
                    raise ValueError('Circular dependency detected in prerequisites. A course cannot have prerequisites leading back to itself.')
                if prereq_id in visited:
                    continue
                visited.add(prereq_id)
                prereq_course = self.browse(prereq_id)
                if prereq_course.exists():
                    to_check.extend(prereq_course.prerequisite_ids.ids)

    def _get_unmet_prerequisite_courses(self, student):
        """Khóa tiên quyết chưa hoàn thành (status completed) của sinh viên."""
        self.ensure_one()
        if not student or not self.prerequisite_ids:
            return self.env['lms.course']
        completed_ids = set(
            self.env['lms.student.course']
            .sudo()
            .search(
                [
                    ('student_id', '=', student.id),
                    ('course_id', 'in', self.prerequisite_ids.ids),
                    ('status', '=', 'completed'),
                ]
            )
            .mapped('course_id')
            .ids
        )
        return self.prerequisite_ids.filtered(lambda c: c.id not in completed_ids)

    @api.model
    def _format_prerequisite_course_names(self, courses):
        return ', '.join(courses.mapped('name')) if courses else ''

    def _prerequisite_error_message(self, student):
        """Thông báo lỗi khi chưa đủ khóa tiên quyết."""
        self.ensure_one()
        missing = self._get_unmet_prerequisite_courses(student)
        if not missing:
            return False
        return _(
            'You must complete the following prerequisite course(s) before taking "%(course)s": %(prereqs)s'
        ) % {
            'course': self.name,
            'prereqs': self._format_prerequisite_course_names(missing),
        }

    def _raise_if_prerequisites_unmet(self, student):
        self.ensure_one()
        message = self._prerequisite_error_message(student)
        if message:
            raise UserError(message)

    @api.model
    def _user_may_bypass_prerequisite_rules(self):
        user = self.env.user
        return (
            self.env.context.get('skip_prerequisite_check')
            or user.has_group('base.group_system')
            or user.has_group('lms.group_lms_manager')
            or user.has_group('lms.group_lms_instructor')
        )

    def _renormalize_lesson_sequences(self):
        """Đánh lại sequence 1, 2, 3... theo thứ tự hiển thị trên khóa học."""
        Lesson = self.env['lms.lesson'].with_context(skip_lesson_sequence_renormalize=True)
        for course in self:
            lessons = course.lesson_ids.sorted(key=lambda l: (l.sequence, l.id))
            for index, lesson in enumerate(lessons, start=1):
                if lesson.sequence != index:
                    Lesson.browse(lesson.id).write({'sequence': index})

    @api.depends('lesson_ids')
    def _compute_total_lessons(self):
        for record in self:
            record.total_lessons = len(record.lesson_ids)
    
    @api.depends('student_course_ids', 'student_course_ids.status')
    def _compute_enrolled_students(self):
        for record in self:
            record.enrolled_students_count = len(
                record.student_course_ids.filtered(
                    lambda e: e.status not in ENROLLMENT_STATUSES_EXCLUDED_FROM_CAPACITY
                )
            )

    def _get_occupied_seat_count(self):
        self.ensure_one()
        return len(
            self.student_course_ids.filtered(
                lambda e: e.status not in ENROLLMENT_STATUSES_EXCLUDED_FROM_CAPACITY
            )
        )

    @api.depends(
        'state',
        'is_active',
        'prerequisite_ids',
        'student_course_ids',
        'student_course_ids.status',
        'instructor_id',
        'max_student',
    )
    def _compute_current_user_registration_state(self):
        """Điều khiển hiển thị nút đăng ký/hủy theo user hiện tại trên form course."""
        user = self.env.user
        is_admin_user = user.has_group('base.group_system') or user.has_group('lms.group_lms_manager')
        is_instructor_user = user.has_group('lms.group_lms_instructor')
        is_student_user = user.has_group('lms.group_lms_user')

        if is_admin_user:
            for record in self:
                record.show_register_button = False
                record.show_cancel_button = False
                record.show_learning_content_tabs = True
            return

        if is_instructor_user and not is_student_user:
            for record in self:
                record.show_register_button = False
                record.show_cancel_button = False
                record.show_learning_content_tabs = bool(record.instructor_id and record.instructor_id.id == user.id)
            return

        if not is_student_user:
            for record in self:
                record.show_register_button = False
                record.show_cancel_button = False
                record.show_learning_content_tabs = False
            return

        student = self.env['lms.student'].sudo().search([('user_id', '=', user.id)], limit=1)
        if not student:
            for record in self:
                record.show_register_button = False
                record.show_cancel_button = False
                record.show_learning_content_tabs = False
            return

        enrolled_ids = set(
            self.env['lms.student.course'].sudo().search([
                ('student_id', '=', student.id),
                ('course_id', 'in', self.ids),
                ('status', '!=', 'rejected'),
            ]).mapped('course_id').ids
        )
        for record in self:
            is_enrolled = record.id in enrolled_ids
            cap = record.max_student or 0
            occupied = record._get_occupied_seat_count()
            has_available_seats = not cap or occupied < cap
            # Khớp action_register_courses: chỉ published + đang hoạt động mới cho đăng ký mới.
            # Thiếu tiên quyết vẫn hiện nút; bấm đăng ký sẽ báo lỗi trong action_register_courses.
            record.show_register_button = (
                not is_enrolled
                and record.state == 'published'
                and record.is_active
                and has_available_seats
            )
            record.show_cancel_button = is_enrolled
            is_learning = self.env['lms.student.course'].sudo().search_count(
                [
                    ('student_id', '=', student.id),
                    ('course_id', '=', record.id),
                    ('status', '=', 'learning'),
                ]
            )
            record.show_learning_content_tabs = bool(is_learning)

    @api.depends()
    def _compute_is_student_course_readonly(self):
        """Chỉ tài khoản thuần học sinh (không phải GV/Admin) — không chỉnh sửa dữ liệu khóa học."""
        user = self.env.user
        readonly = user.has_group('lms.group_lms_user') and not (
            user.has_group('lms.group_lms_instructor')
            or user.has_group('lms.group_lms_manager')
            or user.has_group('base.group_system')
        )
        for record in self:
            record.is_student_course_readonly = readonly

    student_course_ids = fields.One2many('lms.student.course', 'course_id', string='Enrolled Students')
    
    def action_publish(self):
        """Xuất bản khóa học"""
        self.write({'state': 'published'})
        return True

    def action_register_courses(self):
        """
        Đăng ký 1 hoặc nhiều khóa học cho user đang đăng nhập.
        Dùng chung cho form (1 khóa) và list action (nhiều khóa).
        """
        user = self.env.user
        if not user.has_group('lms.group_lms_user'):
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Course Registration'),
                    'message': _('Only student accounts can register for courses.'),
                    'type': 'warning',
                    'sticky': False,
                    'next': {'type': 'ir.actions.client', 'tag': 'reload'},
                },
            }

        student = self.env['lms.student'].sudo().search([('user_id', '=', user.id)], limit=1)
        if not student:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Course Registration'),
                    'message': _('Your account is not linked to a student profile.'),
                    'type': 'warning',
                    'sticky': False,
                    'next': {'type': 'ir.actions.client', 'tag': 'reload'},
                },
            }

        StudentCourse = self.env['lms.student.course'].sudo()
        created_names = []
        duplicate_names = []
        blocked_names = []
        full_names = []
        bypass_prereq = self._user_may_bypass_prerequisite_rules()

        for course in self:
            # Chỉ cho đăng ký khóa học đang hoạt động và đã xuất bản.
            if course.state != 'published' or not course.is_active:
                blocked_names.append(course.name)
                continue
            if not bypass_prereq:
                course._raise_if_prerequisites_unmet(student)
            cap = course.max_student or 0
            if cap and course._get_occupied_seat_count() >= cap:
                full_names.append(course.name)
                continue
            existed = StudentCourse.search(
                [('student_id', '=', student.id), ('course_id', '=', course.id)],
                limit=1,
            )
            if existed:
                duplicate_names.append(course.name)
                continue
            StudentCourse.create(
                {
                    'student_id': student.id,
                    'course_id': course.id,
                    'status': 'pending',
                    'enrollment_date': fields.Date.today(),
                    'start_date': course.start_date,
                    'end_date': course.end_date,
                    'final_score': False,
                }
            )
            created_names.append(course.name)

        lines = []
        if created_names:
            lines.append(_('Successfully registered: %s') % ', '.join(created_names))
        if duplicate_names:
            for name in duplicate_names:
                lines.append(_('You have already registered for course %s') % name)
        if blocked_names:
            lines.append(
                _('Cannot register (not published or inactive): %s')
                % ', '.join(blocked_names)
            )
        if full_names:
            lines.append(
                _('Cannot register because the following course(s) are full (max students reached): %s')
                % ', '.join(full_names)
            )
        if not lines:
            lines.append(_('No courses were processed.'))

        if len(self) == 1 and not created_names:
            raise UserError('\n'.join(lines))

        has_issues = bool(duplicate_names or blocked_names or full_names)
        notif_type = 'success' if created_names and not has_issues else 'warning'
        params = {
            'title': _('Course Registration'),
            'message': '\n'.join(lines),
            'type': notif_type,
            'sticky': notif_type != 'success',
        }
        if created_names:
            params['next'] = {'type': 'ir.actions.client', 'tag': 'reload'}
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': params,
        }

    def action_cancel_course_registration(self):
        """Hủy đăng ký bằng cách xóa bản ghi enrollment hiện tại."""
        self.ensure_one()
        student = self.env['lms.student'].sudo().search([('user_id', '=', self.env.user.id)], limit=1)
        enrollments = self.env['lms.student.course'].sudo().search([
            ('student_id', '=', student.id),
            ('course_id', '=', self.id),
        ])
        enrollments.unlink()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Cancel Registration'),
                'message': _('Course registration for %s has been cancelled.') % self.name,
                'type': 'success',
                'sticky': False,
                'next': {'type': 'ir.actions.client', 'tag': 'reload'},
            },
        }


class Lesson(models.Model):
    _name = 'lms.lesson'
    _description = 'Lesson'
    _order = 'sequence, id, name'

    @api.model
    def _default_end_datetime(self):
        return fields.Datetime.now() + relativedelta(hours=1)

    @api.model
    def _default_sequence(self):
        course_id = self.env.context.get('default_course_id')
        if not course_id:
            return 1
        return self._next_sequence_for_course(int(course_id))

    @api.model
    def _next_sequence_for_course(self, course_id):
        last = self.search([('course_id', '=', course_id)], order='sequence desc, id desc', limit=1)
        return (last.sequence or 0) + 1 if last else 1

    name = fields.Char(string='Lesson Name', required=True)
    sequence = fields.Integer(
        string='Sequence',
        default=_default_sequence,
        required=True,
    )
    lesson_type = fields.Selection(
        [
            ('video', 'Video Lecture'),
            ('online', 'Online'),
        ],
        string='Lesson Type',
        required=True,
        default='online',
    )
    description = fields.Html(string='Description')
    
    course_id = fields.Many2one('lms.course', string='Course', required=True, ondelete='cascade')
    course_form_readonly = fields.Boolean(
        string='Lesson Form Read-only (by Course)',
        related='course_id.is_student_course_readonly',
        readonly=True,
    )

    def _get_ordered_lessons_in_course(self):
        self.ensure_one()
        return self.course_id.lesson_ids.sorted(key=lambda l: (l.sequence, l.id))

    def _get_previous_lessons(self):
        self.ensure_one()
        ordered = self._get_ordered_lessons_in_course()
        if self not in ordered:
            return ordered.browse()
        return ordered[: ordered.ids.index(self.id)]

    def _is_completed_for_student(self, student):
        self.ensure_one()
        progress = self.env['lms.student.lesson.progress'].sudo().search(
            [('student_id', '=', student.id), ('lesson_id', '=', self.id)],
            limit=1,
        )
        return bool(progress and progress.status == 'done')

    def _previous_lesson_incomplete_message(self, student):
        """Sinh viên phải hoàn thành các bài trước (theo sequence trên khóa học)."""
        self.ensure_one()
        if not student or self.env['lms.course']._user_may_bypass_prerequisite_rules():
            return False
        for previous in self._get_previous_lessons():
            if not previous._is_completed_for_student(student):
                return _(
                    'You must complete lesson "%(lesson)s" before starting "%(current)s".'
                ) % {'lesson': previous.name, 'current': self.name}
        return False

    def _raise_if_previous_lessons_incomplete(self, student):
        message = self._previous_lesson_incomplete_message(student)
        if message:
            raise UserError(message)

    @api.depends_context('uid')
    def _compute_current_user_lesson_locked(self):
        student = self.env['lms.student'].sudo().search(
            [('user_id', '=', self.env.user.id)], limit=1
        )
        for lesson in self:
            message = lesson._previous_lesson_incomplete_message(student) if student else False
            lesson.current_user_lesson_locked = bool(message)
            lesson.current_user_lesson_lock_message = message or False

    def action_open_lesson_full(self):
        """Mở form bài học trên cửa sổ chính (nút trên list one2many; không dùng JS)."""
        self.ensure_one()
        if isinstance(self.id, NewId):
            raise UserError(_('Please save the course (and new lessons) before opening details.'))
        student = self.env['lms.student'].sudo().search([('user_id', '=', self.env.user.id)], limit=1)
        if student and self.course_form_readonly:
            self._raise_if_previous_lessons_incomplete(student)
        return {
            'type': 'ir.actions.act_window',
            'name': _('Lesson'),
            'res_model': 'lms.lesson',
            'res_id': self.id,
            'view_mode': 'form',
            'views': [(False, 'form')],
            'target': 'current',
            'context': dict(
                self.env.context,
                form_view_ref='lms.view_lms_lesson_course_tab_form',
            ),
        }

    # Tài liệu học
    video_attachment = fields.Binary(string='Video File', attachment=True)
    video_filename = fields.Char(string='Video Filename')
    pdf_attachment = fields.Binary(string='PDF File', attachment=True)
    pdf_filename = fields.Char(string='PDF Filename')
    video_preview_html = fields.Html(
        string='Video Preview',
        compute='_compute_video_preview_html',
        sanitize=False,
    )
    video_upload_hint_html = fields.Html(
        string='Video Upload Hint',
        compute='_compute_video_upload_hint_html',
        sanitize=False,
    )
    video_upload_hint = fields.Char(
        string='Video Recommendation',
        compute='_compute_video_upload_hint',
    )

    @staticmethod
    def _get_max_video_upload_mb():
        raw = (os.environ.get('LMS_MAX_VIDEO_UPLOAD_MB') or '').strip()
        if not raw:
            return 500
        try:
            value = int(raw)
        except ValueError:
            return 500
        return max(1, value)

    @classmethod
    def _base64_size_bytes(cls, b64_value):
        if not b64_value:
            return 0
        if isinstance(b64_value, bytes):
            b64_value = b64_value.decode('utf-8', errors='ignore')
        text = ''.join(str(b64_value).split())
        if not text:
            return 0
        padding = 0
        if text.endswith('=='):
            padding = 2
        elif text.endswith('='):
            padding = 1
        return (len(text) * 3 // 4) - padding

    @api.constrains('video_attachment')
    def _check_video_attachment_size(self):
        max_mb = self._get_max_video_upload_mb()
        max_bytes = max_mb * 1024 * 1024
        for lesson in self:
            size_bytes = self._base64_size_bytes(lesson.video_attachment)
            if size_bytes > max_bytes:
                raise ValidationError(
                    _(
                        'Video file exceeds the allowed size (%sMB). '
                        'Please compress the video or choose a smaller file.'
                    )
                    % max_mb
                )

    # Thời lượng
    duration_minutes = fields.Integer(string='Duration (minutes)')
    start_datetime = fields.Datetime(string='Start Time', required=True, default=fields.Datetime.now)
    end_datetime = fields.Datetime(string='End Time', required=True, default=_default_end_datetime)
    meeting_url = fields.Char(string='Google Meet Link')
    calendar_event_id = fields.Many2one(
        'calendar.event',
        string='Odoo Calendar Event',
        ondelete='set null',
        copy=False,
    )
    state = fields.Selection(
        [
            ('draft', 'Draft'),
            ('scheduled', 'Scheduled'),
            ('done', 'Completed'),
            ('cancelled', 'Cancelled'),
        ],
        string='Status',
        default='draft',
        required=True,
        copy=False,
    )
    calendar_color = fields.Integer(
        string='Calendar Color',
        compute='_compute_calendar_color',
        store=False,
    )
    calendar_sync_status = fields.Selection(
        [
            ('not_synced', 'Not Synced'),
            ('synced', 'Synced'),
            ('error', 'Error'),
        ],
        string='Calendar Sync Status',
        default='not_synced',
        copy=False,
    )
    calendar_sync_error = fields.Text(string='Calendar Sync Error', copy=False)
    google_event_id = fields.Char(string='Google Event ID', copy=False, readonly=True)
    google_event_html_link = fields.Char(string='Google Event Link', copy=False, readonly=True)
    attendance_notice_sent = fields.Boolean(
        string='Attendance Notice Sent',
        default=False,
        copy=False,
    )
    progress_ids = fields.One2many(
        'lms.student.lesson.progress', 'lesson_id', string='Student Progress'
    )
    current_user_progress_percent = fields.Float(
        string='Current Student Progress (%)',
        compute='_compute_current_user_progress',
        digits=(16, 2),
    )
    current_user_status = fields.Selection(
        [
            ('not_started', 'Not Started'),
            ('in_progress', 'In Progress'),
            ('done', 'Completed'),
        ],
        string='Current Student Status',
        compute='_compute_current_user_progress',
    )
    current_user_watched_seconds = fields.Integer(
        string='Watched Time (seconds)',
        compute='_compute_current_user_progress',
    )
    current_user_last_position_seconds = fields.Integer(
        string='Last Watch Position (seconds)',
        compute='_compute_current_user_progress',
    )
    current_user_lesson_progress_label = fields.Char(
        string='My Status',
        compute='_compute_current_user_lesson_progress_label',
    )
    current_user_lesson_locked = fields.Boolean(
        string='Locked for Current Student',
        compute='_compute_current_user_lesson_locked',
    )
    current_user_lesson_lock_message = fields.Char(
        string='Lock Reason',
        compute='_compute_current_user_lesson_locked',
    )
    current_user_face_checked_in = fields.Boolean(
        string='Face Checked In',
        compute='_compute_current_user_progress',
    )
    face_lesson_attendance_mount_html = fields.Html(
        string='Face Attendance',
        compute='_compute_face_lesson_attendance_mount_html',
        sanitize=False,
    )

    @staticmethod
    def _calc_end_datetime(start_datetime, duration_minutes):
        """Tính giờ kết thúc theo giờ bắt đầu + thời lượng (phút)."""
        if not start_datetime:
            return False
        start_dt = fields.Datetime.to_datetime(start_datetime)
        duration = max(0, int(duration_minutes or 0))
        return start_dt + relativedelta(minutes=duration)

    @api.onchange('start_datetime', 'duration_minutes')
    def _onchange_schedule_fields(self):
        for lesson in self:
            lesson.end_datetime = lesson._calc_end_datetime(
                lesson.start_datetime,
                lesson.duration_minutes,
            )

    @api.constrains('duration_minutes')
    def _check_duration_minutes_non_negative(self):
        for lesson in self:
            if lesson.duration_minutes is not None and lesson.duration_minutes < 0:
                raise ValidationError(_('Duration (minutes) cannot be negative.'))

    @api.depends('state')
    def _compute_calendar_color(self):
        # Odoo calendar color index
        color_map = {
            'scheduled': 10,  # xanh lá
            'cancelled': 1,   # đỏ
            'done': 3,        # xanh dương
            'draft': 0,
        }
        for lesson in self:
            lesson.calendar_color = color_map.get(lesson.state, 0)

    @api.depends(
        'progress_ids',
        'progress_ids.watched_seconds',
        'progress_ids.last_position_seconds',
        'progress_ids.progress_percent',
        'progress_ids.status',
        'progress_ids.video_duration_seconds',
        'progress_ids.face_checked_in',
    )
    def _compute_current_user_progress(self):
        student = self.env['lms.student'].sudo().search([('user_id', '=', self.env.user.id)], limit=1)
        for lesson in self:
            progress = self.env['lms.student.lesson.progress']
            if student:
                progress = self.env['lms.student.lesson.progress'].sudo().search(
                    [('student_id', '=', student.id), ('lesson_id', '=', lesson.id)],
                    limit=1,
                )
            lesson.current_user_progress_percent = progress.progress_percent if progress else 0.0
            lesson.current_user_status = progress.status if progress else 'not_started'
            lesson.current_user_watched_seconds = progress.watched_seconds if progress else 0
            lesson.current_user_last_position_seconds = (
                progress.last_position_seconds if progress else 0
            )
            lesson.current_user_face_checked_in = bool(progress.face_checked_in) if progress else False

    @api.depends('create_date', 'write_date', 'course_form_readonly', 'course_id')
    def _compute_face_lesson_attendance_mount_html(self):
        for lesson in self:
            if not lesson.course_form_readonly:
                lesson.face_lesson_attendance_mount_html = ''
                continue
            if isinstance(lesson.id, int) and lesson.id:
                lesson.face_lesson_attendance_mount_html = (
                    '<div class="o_lms_lesson_face_root" data-lms-role="attend" data-lesson-id="%s"></div>'
                    % lesson.id
                )
            else:
                lesson.face_lesson_attendance_mount_html = ''

    @api.model
    def _user_is_pure_student(self, user=None):
        user = user or self.env.user
        return user.has_group('lms.group_lms_user') and not (
            user.has_group('lms.group_lms_instructor')
            or user.has_group('lms.group_lms_manager')
            or user.has_group('base.group_system')
        )

    @api.depends(
        'progress_ids',
        'progress_ids.status',
        'progress_ids.student_id',
        'progress_ids.face_checked_in',
        'lesson_type',
        'course_id',
        'course_id.student_course_ids.status',
    )
    def _compute_current_user_lesson_progress_label(self):
        student = self.env['lms.student'].sudo().search([('user_id', '=', self.env.user.id)], limit=1)
        Progress = self.env['lms.student.lesson.progress']
        status_sel = Progress._fields['status'].selection
        if callable(status_sel):
            status_sel = status_sel(Progress)
        selection_labels = dict(status_sel)
        pure_student = self._user_is_pure_student()
        StudentCourse = self.env['lms.student.course'].sudo()
        for lesson in self:
            if not pure_student:
                total = StudentCourse.search_count(
                    [
                        ('course_id', '=', lesson.course_id.id),
                        ('status', '=', 'learning'),
                    ]
                )
                if lesson.lesson_type == 'online':
                    completed = len(lesson.progress_ids.filtered('face_checked_in'))
                else:
                    completed = len(lesson.progress_ids.filtered(lambda p: p.status == 'done'))
                lesson.current_user_lesson_progress_label = '%s/%s' % (completed, total)
                continue
            if not student:
                lesson.current_user_lesson_progress_label = False
                continue
            progress = lesson.progress_ids.filtered(lambda p: p.student_id.id == student.id)[:1]
            if not progress:
                lesson.current_user_lesson_progress_label = _('Not Started')
            else:
                lesson.current_user_lesson_progress_label = selection_labels.get(
                    progress.status, progress.status
                ) or ''

    def action_update_current_user_progress(self, watched_seconds, last_position_seconds, video_duration_seconds=None):
        self.ensure_one()
        student = self.env['lms.student'].sudo().search([('user_id', '=', self.env.user.id)], limit=1)
        if not student:
            raise ValidationError(_('Student profile not found for the current account.'))
        progress = self.env['lms.student.lesson.progress'].sudo().get_or_create_progress(student, self)
        vals = {
            'watched_seconds': max(progress.watched_seconds, int(watched_seconds or 0)),
            'last_position_seconds': max(0, int(last_position_seconds or 0)),
        }
        if video_duration_seconds is not None:
            vals['video_duration_seconds'] = max(progress.video_duration_seconds or 0, int(video_duration_seconds or 0))
        progress.write(vals)
        return True

    def action_lesson_face_attendance(self, embedding_json, image_base64=None):
        """Face check-in (once per lesson per student). Optional photo syncs to user avatar."""
        self.ensure_one()
        if self.lesson_type != 'online':
            raise UserError(_('Only online lessons require face attendance.'))
        if not isinstance(embedding_json, str) or not embedding_json.strip():
            raise ValidationError(_('Missing photo data (embedding).'))
        student = self.env['lms.student'].sudo().search([('user_id', '=', self.env.user.id)], limit=1)
        if not student:
            raise ValidationError(_('Student profile not found.'))
        if not student.face_embedding_json:
            raise UserError(_('Please register a face template on the student profile before checking in.'))
        enrolled = self.env['lms.student.course'].sudo().search_count(
            [
                ('student_id', '=', student.id),
                ('course_id', '=', self.course_id.id),
                ('status', '=', 'learning'),
            ]
        )
        if not enrolled:
            raise UserError(_('Your course registration is not in Learning status.'))
        ref = face_embedding_utils.parse_embedding(student.face_embedding_json)
        probe = face_embedding_utils.parse_embedding(embedding_json)
        if not ref or not probe:
            raise ValidationError(_('Invalid face data.'))
        sim = face_embedding_utils.cosine_similarity(ref, probe)
        if sim < face_embedding_utils.COSINE_MATCH_THRESHOLD:
            raise UserError(
                _('Face mismatch (similarity %.0f%%). Please try again with better lighting.')
                % (sim * 100)
            )
        progress = self.env['lms.student.lesson.progress'].sudo().get_or_create_progress(student, self)
        if progress.face_checked_in:
            raise UserError(_('You have already checked in for this lesson.'))
        progress.sudo().write(
            {
                'face_checked_in': True,
                'face_checked_in_at': fields.Datetime.now(),
            }
        )
        student._apply_face_capture_image(image_base64)
        try:
            self._google_calendar_add_attendee_after_attendance(student)
        except Exception as e:  # noqa: BLE001
            raise UserError(
                _('Attendance recorded but could not add you to the Google Meet attendee list: %s')
                % str(e)
            ) from e
        return {
            'lms_face_result': True,
            'message': _('Attendance recorded successfully.'),
            'progress_status': progress.status,
        }

    def _lesson_attendance_url(self):
        self.ensure_one()
        base_url = (self.get_base_url() or '').rstrip('/')
        return '%s/web#id=%s&model=lms.lesson&view_type=form' % (base_url, self.id)

    def _notify_learning_students_for_attendance(self):
        template = self.env.ref('lms.email_template_lesson_attendance_link', raise_if_not_found=False)
        if not template:
            raise UserError(_('Attendance email template not found (email_template_lesson_attendance_link).'))

        StudentCourse = self.env['lms.student.course'].sudo()
        for lesson in self:
            if lesson.lesson_type != 'online' or lesson.state != 'scheduled' or lesson.attendance_notice_sent:
                continue

            enrollments = StudentCourse.search(
                [
                    ('course_id', '=', lesson.course_id.id),
                    ('status', '=', 'learning'),
                ]
            )
            attendance_url = lesson._lesson_attendance_url()
            mail_ctx = dict(self.env.context, attendance_url=attendance_url)

            for student in enrollments.mapped('student_id'):
                email_to = (student.email or student.user_id.email or student.user_id.login or '').strip()
                if email_to:
                    template.with_context(mail_ctx).send_mail(
                        lesson.id,
                        force_send=True,
                        email_values={'email_to': email_to},
                    )

            lesson._google_calendar_apply_updates({'attendance_notice_sent': True})

    def _google_calendar_add_attendee_after_attendance(self, student):
        self.ensure_one()
        if self.lesson_type != 'online' or not self.google_event_id:
            return
        attendee_email = (student.email or student.user_id.email or student.user_id.login or '').strip()
        if not attendee_email:
            raise ValidationError(_('Student does not have an email to add to the Google Meet attendee list.'))
        google_calendar_sync.add_lesson_attendee(self, attendee_email, student.name)

    @staticmethod
    def _guess_video_mime(name_or_url):
        value = (name_or_url or '').lower()
        if value.endswith('.mp4') or '.mp4?' in value:
            return 'video/mp4'
        if value.endswith('.webm') or '.webm?' in value:
            return 'video/webm'
        if value.endswith('.ogg') or value.endswith('.ogv') or '.ogg?' in value or '.ogv?' in value:
            return 'video/ogg'
        if value.endswith('.mov') or '.mov?' in value:
            return 'video/quicktime'
        if value.endswith('.m4v') or '.m4v?' in value:
            return 'video/x-m4v'
        if value.endswith('.mkv') or '.mkv?' in value:
            return 'video/x-matroska'
        return 'video/mp4'

    @api.depends('video_attachment', 'video_filename')
    def _compute_video_preview_html(self):
        for lesson in self:
            html = '<p class="text-muted"><i>No video available for direct viewing.</i></p>'
            if lesson.video_attachment and lesson.id:
                stream_url = '/web/content/%s?model=lms.lesson&field=video_attachment&download=false' % lesson.id
                mime = self._guess_video_mime(lesson.video_filename or '')
                html = (
                    '<video class="lms-video-tracker" data-lms-lesson-id="%s" controls '
                    'controlsList="nodownload noplaybackrate" disablePictureInPicture '
                    'playsinline preload="metadata" oncontextmenu="return false;" '
                    'style="width:100%%;max-width:900px;">'
                    '<source src="%s" type="%s"/>'
                    'Your browser does not support direct video playback.'
                    '</video>'
                ) % (lesson.id, stream_url, mime)
            lesson.video_preview_html = html

    @api.depends('video_attachment', 'video_filename')
    def _compute_video_upload_hint_html(self):
        max_mb = self._get_max_video_upload_mb()
        text_hint = (
            'Recommended: prefer MP4 (or WebM/OGG), video size should be under %sMB.'
        ) % max_mb
        hint = (
            '<p style="margin:6px 0 0 0;color:#6b7280;font-size:12px;">'
            '%s'
            '</p>'
        ) % text_hint
        for lesson in self:
            lesson.video_upload_hint_html = hint

    @api.depends('video_attachment', 'video_filename')
    def _compute_video_upload_hint(self):
        max_mb = self._get_max_video_upload_mb()
        text_hint = 'Video recommendation: prefer MP4 (or WebM/OGG), video size should be under %sMB.' % max_mb
        for lesson in self:
            lesson.video_upload_hint = text_hint

    def _google_calendar_apply_updates(self, vals):
        return self.with_context(skip_google_calendar_sync=True).write(vals)

    def _google_calendar_sync_if_needed(self):
        for lesson in self:
            if lesson.state != 'scheduled' or lesson.lesson_type != 'online':
                continue
            try:
                vals = google_calendar_sync.sync_lesson_event(lesson)
            except Exception as e:  # noqa: BLE001
                lesson._google_calendar_apply_updates({
                    'calendar_sync_status': 'error',
                    'calendar_sync_error': str(e),
                })
                continue
            lesson._google_calendar_apply_updates(vals)

    def _google_calendar_unsync(self, *, clear_meeting_url=False):
        for lesson in self:
            if lesson.google_event_id:
                try:
                    google_calendar_sync.delete_lesson_event(lesson)
                except Exception as e:  # noqa: BLE001
                    lesson._google_calendar_apply_updates({
                        'calendar_sync_status': 'error',
                        'calendar_sync_error': str(e),
                    })
                    continue
            vals = {
                'google_event_id': False,
                'google_event_html_link': False,
                'calendar_sync_status': 'not_synced',
                'calendar_sync_error': False,
            }
            if clear_meeting_url:
                vals['meeting_url'] = False
            lesson._google_calendar_apply_updates(vals)

    @api.model_create_multi
    def create(self, vals_list):
        Course = self.env['lms.course'].sudo()
        ctx_course_id = self.env.context.get('default_course_id')
        course_next_seq = {}
        for vals in vals_list:
            start_dt = vals.get('start_datetime') or fields.Datetime.now()
            duration = vals.get('duration_minutes', 0)
            vals['end_datetime'] = self._calc_end_datetime(start_dt, duration)
            # Tạo inline từ one2many thường truyền course_id qua context default_course_id.
            course_id = vals.get('course_id') or ctx_course_id
            if not course_id:
                continue
            course = Course.browse(int(course_id))
            if course.exists() and course.state != 'published':
                raise ValidationError(
                    _('Lessons can only be created when the course is in "Published" status.')
                )
            course_id = int(course_id)
            if self.env.context.get('skip_lesson_sequence_assign'):
                continue
            if course_id not in course_next_seq:
                course_next_seq[course_id] = self._next_sequence_for_course(course_id)
            vals['sequence'] = course_next_seq[course_id]
            course_next_seq[course_id] += 1
        lessons = super().create(vals_list)
        if not self.env.context.get('skip_lesson_sequence_renormalize'):
            lessons.mapped('course_id')._renormalize_lesson_sequences()
        if not self.env.context.get('skip_google_calendar_sync'):
            lessons._google_calendar_sync_if_needed()
            lessons._notify_learning_students_for_attendance()
        return lessons

    def write(self, vals):
        if self.env.context.get('skip_google_calendar_sync'):
            res = super().write(vals)
            if not self.env.context.get('skip_lesson_sequence_renormalize') and (
                'sequence' in vals or 'course_id' in vals
            ):
                self.mapped('course_id')._renormalize_lesson_sequences()
            return res

        if any(key in vals for key in ('start_datetime', 'duration_minutes', 'end_datetime')):
            if 'start_datetime' in vals:
                start_dt = vals.get('start_datetime')
            else:
                start_dt = self.start_datetime if len(self) == 1 else None

            if 'duration_minutes' in vals:
                duration = vals.get('duration_minutes')
            else:
                duration = self.duration_minutes if len(self) == 1 else None

            if len(self) == 1:
                vals['end_datetime'] = self._calc_end_datetime(start_dt, duration)

        was_syncable = {
            lesson.id: lesson.state == 'scheduled' and lesson.lesson_type == 'online'
            for lesson in self
        }
        res = super().write(vals)

        if len(self) > 1 and any(key in vals for key in ('start_datetime', 'duration_minutes', 'end_datetime')):
            need_resync = self.env['lms.lesson']
            for lesson in self:
                computed_end = self._calc_end_datetime(lesson.start_datetime, lesson.duration_minutes)
                if lesson.end_datetime != computed_end:
                    lesson.with_context(skip_google_calendar_sync=True).write({'end_datetime': computed_end})
                    need_resync |= lesson
            if need_resync:
                need_resync.filtered(
                    lambda l: l.state == 'scheduled' and l.lesson_type == 'online'
                )._google_calendar_sync_if_needed()

        status_changed = 'state' in vals
        lesson_type_changed = 'lesson_type' in vals
        sync_relevant = {'name', 'description', 'start_datetime', 'end_datetime', 'course_id', 'meeting_url'}
        if status_changed or lesson_type_changed:
            became_unsyncable = self.filtered(
                lambda l: was_syncable.get(l.id)
                and not (l.state == 'scheduled' and l.lesson_type == 'online')
            )
            if became_unsyncable:
                became_unsyncable._google_calendar_unsync(clear_meeting_url=True)
            became_unnotifiable = self.filtered(
                lambda l: l.attendance_notice_sent
                and not (l.state == 'scheduled' and l.lesson_type == 'online')
            )
            if became_unnotifiable:
                became_unnotifiable._google_calendar_apply_updates({'attendance_notice_sent': False})

        if status_changed or lesson_type_changed or (set(vals.keys()) & sync_relevant):
            self.filtered(
                lambda l: l.state == 'scheduled' and l.lesson_type == 'online'
            )._google_calendar_sync_if_needed()
        self._notify_learning_students_for_attendance()
        if not self.env.context.get('skip_lesson_sequence_renormalize') and (
            'sequence' in vals or 'course_id' in vals
        ):
            self.mapped('course_id')._renormalize_lesson_sequences()
        return res

    def unlink(self):
        courses = self.mapped('course_id')
        if not self.env.context.get('skip_google_calendar_sync'):
            self._google_calendar_unsync(clear_meeting_url=False)
        res = super().unlink()
        if not self.env.context.get('skip_lesson_sequence_renormalize'):
            courses._renormalize_lesson_sequences()
        return res


class StudentLessonProgress(models.Model):
    _name = 'lms.student.lesson.progress'
    _description = 'Student Lesson Progress'
    _order = 'id desc'

    student_id = fields.Many2one('lms.student', string='Student', required=True, ondelete='cascade', index=True)
    lesson_id = fields.Many2one('lms.lesson', string='Lesson', required=True, ondelete='cascade', index=True)
    course_id = fields.Many2one(
        'lms.course',
        string='Course',
        related='lesson_id.course_id',
        store=True,
        readonly=True,
    )
    enrollment_id = fields.Many2one(
        'lms.student.course',
        string='Course Enrollment',
        required=True,
        ondelete='cascade',
        index=True,
    )
    watched_seconds = fields.Integer(string='Watched Seconds', default=0)
    video_duration_seconds = fields.Integer(string='Video Duration (seconds)', default=0)
    progress_percent = fields.Float(
        string='Progress (%)',
        compute='_compute_progress_percent',
        store=True,
        digits=(16, 2),
    )
    last_position_seconds = fields.Integer(string='Last Watch Position (seconds)', default=0)
    face_checked_in = fields.Boolean(string='Face Checked In', default=False, copy=False)
    face_checked_in_at = fields.Datetime(string='Checked In At', copy=False)
    status = fields.Selection(
        [
            ('not_started', 'Not Started'),
            ('in_progress', 'In Progress'),
            ('done', 'Completed'),
        ],
        string='Status',
        compute='_compute_status',
        store=True,
    )
    started_at = fields.Datetime(string='Started At', default=fields.Datetime.now)
    completed_at = fields.Datetime(string='Completed At')

    _sql_constraints = [
        ('student_lesson_progress_unique', 'unique(student_id, lesson_id)', 'Student lesson progress already exists.'),
    ]

    @api.model
    def get_or_create_progress(self, student, lesson):
        progress = self.sudo().search(
            [('student_id', '=', student.id), ('lesson_id', '=', lesson.id)],
            limit=1,
        )
        if progress:
            return progress
        enrollment = self.env['lms.student.course'].sudo().search(
            [
                ('student_id', '=', student.id),
                ('course_id', '=', lesson.course_id.id),
                ('status', '!=', 'rejected'),
            ],
            limit=1,
        )
        if not enrollment:
            raise ValidationError(_('Student has not enrolled in the course containing this lesson.'))
        if not self.env['lms.course']._user_may_bypass_prerequisite_rules():
            message = lesson.course_id._prerequisite_error_message(student)
            if message:
                raise ValidationError(message)
            message = lesson._previous_lesson_incomplete_message(student)
            if message:
                raise ValidationError(message)
        return self.sudo().create(
            {
                'student_id': student.id,
                'lesson_id': lesson.id,
                'enrollment_id': enrollment.id,
                'started_at': fields.Datetime.now(),
            }
        )

    @api.depends('watched_seconds', 'lesson_id.duration_minutes', 'video_duration_seconds')
    def _compute_progress_percent(self):
        for rec in self:
            # Ưu tiên thời lượng thật từ trình phát (HTML5). duration_minutes có thể >> video → % quá thấp.
            duration_seconds = int(rec.video_duration_seconds or 0)
            if duration_seconds <= 0:
                duration_seconds = int((rec.lesson_id.duration_minutes or 0) * 60)
            if duration_seconds <= 0:
                rec.progress_percent = 0.0
                continue
            watched = max(0, rec.watched_seconds or 0)
            rec.progress_percent = min(100.0, (watched / duration_seconds) * 100.0)

    @api.depends(
        'watched_seconds',
        'lesson_id.duration_minutes',
        'lesson_id.lesson_type',
        'video_duration_seconds',
        'progress_percent',
        'face_checked_in',
    )
    def _compute_status(self):
        for rec in self:
            lesson_type = rec.lesson_id.lesson_type or 'online'
            if lesson_type == 'online':
                # Bài online: chỉ cần điểm danh khuôn mặt thành công là hoàn thành.
                rec.status = 'done' if rec.face_checked_in else 'not_started'
            else:
                pct = rec.progress_percent or 0.0
                # Bài video: hoàn thành khi xem >= 90%.
                if pct >= 90.0:
                    rec.status = 'done'
                elif pct > 0:
                    rec.status = 'in_progress'
                else:
                    rec.status = 'not_started'

    @api.constrains('last_position_seconds', 'watched_seconds')
    def _check_video_positions(self):
        for rec in self:
            if rec.last_position_seconds < 0 or rec.watched_seconds < 0:
                raise ValidationError(_('Video watch time cannot be negative.'))
            if rec.last_position_seconds > rec.watched_seconds:
                raise ValidationError(_('Last position cannot be greater than total watched time.'))

    @api.constrains('enrollment_id', 'lesson_id', 'student_id')
    def _check_enrollment_consistency(self):
        for rec in self:
            if not rec.enrollment_id or not rec.lesson_id:
                continue
            if rec.enrollment_id.student_id != rec.student_id:
                raise ValidationError(_('Enrollment does not belong to the correct student for this lesson progress.'))
            if rec.enrollment_id.course_id != rec.lesson_id.course_id:
                raise ValidationError(_('Enrollment does not belong to the course of the selected lesson.'))

    def write(self, vals):
        res = super().write(vals)
        if self.env.context.get('skip_progress_completion_ts'):
            return res
        done_no_ts = self.filtered(lambda r: r.status == 'done' and not r.completed_at)
        if done_no_ts:
            done_no_ts.with_context(skip_progress_completion_ts=True).write(
                {'completed_at': fields.Datetime.now()}
            )
        if any(key in vals for key in ('status', 'face_checked_in', 'progress_percent', 'watched_seconds')):
            self.mapped('course_id.lesson_ids').invalidate_recordset(
                ['current_user_lesson_locked', 'current_user_lesson_lock_message']
            )
        return res
