# -*- coding: utf-8 -*-

from dateutil.relativedelta import relativedelta
import os

from odoo import _, api, fields, models
from odoo.api import NewId
from odoo.exceptions import UserError, ValidationError

from ..services import google_calendar_sync
from . import face_embedding_utils


class CourseCategory(models.Model):
    _name = 'lms.course.category'
    _description = 'Danh mục khóa học'
    _order = 'sequence, name'

    name = fields.Char(string='Tên danh mục', required=True)
    sequence = fields.Integer(string='Thứ tự', default=10)
    description = fields.Text(string='Mô tả')
    course_ids = fields.One2many('lms.course', 'category_id', string='Khóa học')


class CourseLevel(models.Model):
    _name = 'lms.course.level'
    _description = 'Cấp độ khóa học'
    _order = 'sequence, name'

    name = fields.Char(string='Tên cấp độ', required=True)
    sequence = fields.Integer(string='Thứ tự', default=10)
    description = fields.Text(string='Mô tả')
    course_ids = fields.One2many('lms.course', 'level_id', string='Khóa học')


class CourseTag(models.Model):
    _name = 'lms.course.tag'
    _description = 'Nhãn khóa học'
    _order = 'name'

    name = fields.Char(string='Tên nhãn', required=True)
    color = fields.Integer(string='Màu sắc')
    course_ids = fields.Many2many('lms.course', 'course_tag_rel', 'tag_id', 'course_id', string='Khóa học')


class Course(models.Model):
    _name = 'lms.course'
    _description = 'Khóa học'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char(string='Tên khóa học', required=True, tracking=True)
    # mail.tracking không hỗ trợ field Html, chỉ giữ hiển thị nội dung.
    description = fields.Html(string='Mô tả')
    image_1920 = fields.Image(string='Ảnh khóa học', max_width=1920, max_height=1920)
    
    # Phân loại
    category_id = fields.Many2one('lms.course.category', string='Danh mục', required=True, tracking=True)
    level_id = fields.Many2one('lms.course.level', string='Cấp độ', required=True, tracking=True)
    tag_ids = fields.Many2many('lms.course.tag', 'course_tag_rel', 'course_id', 'tag_id', string='Nhãn')
    
    # Thông tin khóa học
    instructor_id = fields.Many2one('res.users', string='Giảng viên', tracking=True)
    duration_hours = fields.Float(string='Thời lượng (giờ)', digits=(16, 2), tracking=True)
    max_student = fields.Integer(string='Số học viên tối đa')
    start_date = fields.Date(string='Ngày bắt đầu')
    end_date = fields.Date(string='Ngày kết thúc')
    # VND không dùng phần thập phân -> lưu số nguyên để tránh hiển thị 100,000.00
    price = fields.Integer(string='Chi phí (VND)', default=0, tracking=True)
    contact_payment = fields.Text(string='Liên hệ giáo viên', tracking=True)
    prerequisite_ids = fields.Many2many(
        'lms.course', 'course_prerequisite_rel', 'course_id', 'prerequisite_id',
        string='Khóa học tiên quyết'
    )
    
    # Nội dung
    lesson_ids = fields.One2many('lms.lesson', 'course_id', string='Bài học')
    total_lessons = fields.Integer(string='Tổng số bài học', compute='_compute_total_lessons', store=True)
    
    # Thống kê
    enrolled_students_count = fields.Integer(string='Số học viên đăng ký', compute='_compute_enrolled_students', store=True)
    average_rating = fields.Float(string='Đánh giá trung bình', digits=(16, 2))
    show_register_button = fields.Boolean(
        string='Hiển thị nút đăng ký',
        compute='_compute_current_user_registration_state',
    )
    show_cancel_button = fields.Boolean(
        string='Hiển thị nút hủy đăng ký',
        compute='_compute_current_user_registration_state',
    )
    show_learning_content_tabs = fields.Boolean(
        string='Hiển thị tab học tập',
        compute='_compute_current_user_registration_state',
    )
    is_student_course_readonly = fields.Boolean(
        string='Form khóa học chỉ đọc (học viên)',
        compute='_compute_is_student_course_readonly',
    )

    # Trạng thái
    state = fields.Selection([
        ('draft', 'Nháp'),
        ('published', 'Đã xuất bản'),
        ('archived', 'Lưu trữ'),
    ], string='Trạng thái', default='draft', tracking=True)
    
    is_active = fields.Boolean(string='Đang hoạt động', default=True)

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
            raise ValidationError('Chi phí khóa học phải là số nguyên (VND).') from e
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
                raise ValueError('Thời lượng khóa học không được âm')

    @api.constrains('price')
    def _check_price_non_negative(self):
        for record in self:
            if record.price is not None and record.price < 0:
                raise ValueError('Chi phí khóa học không được âm')
    
    @api.constrains('prerequisite_ids')
    def _check_prerequisite_cycle(self):
        """Kiểm tra prerequisite không được tạo vòng lặp"""
        for record in self:
            if record.id in record.prerequisite_ids.ids:
                raise ValueError('Khóa học không thể là prerequisite của chính nó')
            # Kiểm tra vòng lặp gián tiếp (đệ quy)
            visited = set()
            to_check = list(record.prerequisite_ids.ids)
            while to_check:
                prereq_id = to_check.pop()
                if prereq_id == record.id:
                    raise ValueError('Phát hiện vòng lặp trong prerequisite. Khóa học không thể có prerequisite dẫn đến chính nó.')
                if prereq_id in visited:
                    continue
                visited.add(prereq_id)
                prereq_course = self.browse(prereq_id)
                if prereq_course.exists():
                    to_check.extend(prereq_course.prerequisite_ids.ids)
    
    @api.depends('lesson_ids')
    def _compute_total_lessons(self):
        for record in self:
            record.total_lessons = len(record.lesson_ids)
    
    @api.depends('student_course_ids')
    def _compute_enrolled_students(self):
        for record in self:
            record.enrolled_students_count = len(record.student_course_ids)

    @api.depends('state', 'is_active', 'student_course_ids', 'instructor_id')
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
                ('status', '!=', 'cancelled'),
            ]).mapped('course_id').ids
        )
        for record in self:
            is_enrolled = record.id in enrolled_ids
            # Khớp action_register_courses: chỉ published + đang hoạt động mới cho đăng ký mới.
            record.show_register_button = (
                not is_enrolled
                and record.state == 'published'
                and record.is_active
            )
            record.show_cancel_button = is_enrolled
            approved_or_learning = self.env['lms.student.course'].sudo().search_count(
                [
                    ('student_id', '=', student.id),
                    ('course_id', '=', record.id),
                    ('status', 'in', ['approved', 'learning']),
                ]
            )
            record.show_learning_content_tabs = bool(approved_or_learning)

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

    student_course_ids = fields.One2many('lms.student.course', 'course_id', string='Học viên đăng ký')
    
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
                    'title': _('Đăng ký khóa học'),
                    'message': _('Chỉ tài khoản học viên mới được đăng ký khóa học.'),
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
                    'title': _('Đăng ký khóa học'),
                    'message': _('Tài khoản của bạn chưa liên kết hồ sơ học viên.'),
                    'type': 'warning',
                    'sticky': False,
                    'next': {'type': 'ir.actions.client', 'tag': 'reload'},
                },
            }

        StudentCourse = self.env['lms.student.course'].sudo()
        created_names = []
        duplicate_names = []
        blocked_names = []

        for course in self:
            # Chỉ cho đăng ký khóa học đang hoạt động và đã xuất bản.
            if course.state != 'published' or not course.is_active:
                blocked_names.append(course.name)
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
                    'final_score': False,
                }
            )
            created_names.append(course.name)

        lines = []
        if created_names:
            lines.append(_('Đăng ký thành công: %s') % ', '.join(created_names))
        if duplicate_names:
            for name in duplicate_names:
                lines.append(_('Bạn đã đăng ký khóa học %s rồi') % name)
        if blocked_names:
            lines.append(
                _('Không thể đăng ký (chưa xuất bản hoặc không hoạt động): %s')
                % ', '.join(blocked_names)
            )
        if not lines:
            lines.append(_('Không có khóa học nào được xử lý.'))

        notif_type = 'success' if created_names and not (duplicate_names or blocked_names) else 'warning'
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Đăng ký khóa học'),
                'message': '\n'.join(lines),
                'type': notif_type,
                'sticky': False,
                'next': {'type': 'ir.actions.client', 'tag': 'reload'},
            },
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
                'title': _('Hủy đăng ký'),
                'message': _('Đã hủy đăng ký khóa học %s.') % self.name,
                'type': 'success',
                'sticky': False,
                'next': {'type': 'ir.actions.client', 'tag': 'reload'},
            },
        }


class Lesson(models.Model):
    _name = 'lms.lesson'
    _description = 'Bài học'
    _order = 'sequence, name'

    @api.model
    def _default_end_datetime(self):
        return fields.Datetime.now() + relativedelta(hours=1)

    name = fields.Char(string='Tên bài học', required=True)
    sequence = fields.Integer(string='Thứ tự', default=10, required=True)
    lesson_type = fields.Selection(
        [
            ('video', 'Video'),
            ('online', 'Online'),
        ],
        string='Loại bài học',
        required=True,
        default='online',
    )
    description = fields.Html(string='Mô tả')
    
    course_id = fields.Many2one('lms.course', string='Khóa học', required=True, ondelete='cascade')
    course_form_readonly = fields.Boolean(
        string='Form bài học chỉ đọc (theo khóa học)',
        related='course_id.is_student_course_readonly',
        readonly=True,
    )

    def action_open_lesson_full(self):
        """Mở form bài học trên cửa sổ chính (nút trên list one2many; không dùng JS)."""
        self.ensure_one()
        if isinstance(self.id, NewId):
            raise UserError(_('Vui lòng lưu khóa học (và bài học mới) trước khi mở chi tiết.'))
        return {
            'type': 'ir.actions.act_window',
            'name': _('Bài học'),
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
    video_attachment = fields.Binary(string='File video', attachment=True)
    video_filename = fields.Char(string='Tên file video')
    pdf_attachment = fields.Binary(string='File PDF', attachment=True)
    pdf_filename = fields.Char(string='Tên file PDF')
    video_preview_html = fields.Html(
        string='Xem video',
        compute='_compute_video_preview_html',
        sanitize=False,
    )
    video_upload_hint_html = fields.Html(
        string='Gợi ý upload video',
        compute='_compute_video_upload_hint_html',
        sanitize=False,
    )
    video_upload_hint = fields.Char(
        string='Khuyến nghị video',
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
                        'File video vượt quá dung lượng cho phép (%sMB). '
                        'Vui lòng nén video hoặc chọn file nhỏ hơn.'
                    )
                    % max_mb
                )

    # Thời lượng
    duration_minutes = fields.Integer(string='Thời lượng (phút)')
    start_datetime = fields.Datetime(string='Thời gian bắt đầu', required=True, default=fields.Datetime.now)
    end_datetime = fields.Datetime(string='Thời gian kết thúc', required=True, default=_default_end_datetime)
    meeting_url = fields.Char(string='Link Google Meet')
    calendar_event_id = fields.Many2one(
        'calendar.event',
        string='Sự kiện lịch Odoo',
        ondelete='set null',
        copy=False,
    )
    state = fields.Selection(
        [
            ('draft', 'Draft'),
            ('scheduled', 'Scheduled'),
            ('done', 'Done'),
            ('cancelled', 'Cancelled'),
        ],
        string='Trạng thái',
        default='draft',
        required=True,
        copy=False,
    )
    calendar_color = fields.Integer(
        string='Màu lịch',
        compute='_compute_calendar_color',
        store=False,
    )
    is_published = fields.Boolean(string='Hiển thị cho học viên', default=False, copy=False)
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
    progress_ids = fields.One2many(
        'lms.student.lesson.progress', 'lesson_id', string='Tiến độ học viên'
    )
    current_user_progress_percent = fields.Float(
        string='Tiến độ học viên hiện tại (%)',
        compute='_compute_current_user_progress',
        digits=(16, 2),
    )
    current_user_status = fields.Selection(
        [
            ('not_started', 'Chưa bắt đầu'),
            ('in_progress', 'Đang học'),
            ('done', 'Hoàn thành'),
        ],
        string='Trạng thái học viên hiện tại',
        compute='_compute_current_user_progress',
    )
    current_user_watched_seconds = fields.Integer(
        string='Thời gian đã xem (giây)',
        compute='_compute_current_user_progress',
    )
    current_user_last_position_seconds = fields.Integer(
        string='Vị trí xem gần nhất (giây)',
        compute='_compute_current_user_progress',
    )
    current_user_lesson_progress_label = fields.Char(
        string='Trạng thái của tôi',
        compute='_compute_current_user_lesson_progress_label',
    )
    current_user_face_checked_in = fields.Boolean(
        string='Đã điểm danh khuôn mặt',
        compute='_compute_current_user_progress',
    )
    face_lesson_attendance_mount_html = fields.Html(
        string='Điểm danh khuôn mặt',
        compute='_compute_face_lesson_attendance_mount_html',
        sanitize=False,
    )

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

    @api.depends('progress_ids', 'progress_ids.status', 'progress_ids.student_id')
    def _compute_current_user_lesson_progress_label(self):
        student = self.env['lms.student'].sudo().search([('user_id', '=', self.env.user.id)], limit=1)
        Progress = self.env['lms.student.lesson.progress']
        status_sel = Progress._fields['status'].selection
        if callable(status_sel):
            status_sel = status_sel(Progress)
        selection_labels = dict(status_sel)
        for lesson in self:
            if not student:
                lesson.current_user_lesson_progress_label = False
                continue
            progress = lesson.progress_ids.filtered(lambda p: p.student_id.id == student.id)[:1]
            if not progress:
                lesson.current_user_lesson_progress_label = 'Chưa học'
            else:
                lesson.current_user_lesson_progress_label = selection_labels.get(
                    progress.status, progress.status
                ) or ''

    def action_update_current_user_progress(self, watched_seconds, last_position_seconds, video_duration_seconds=None):
        self.ensure_one()
        student = self.env['lms.student'].sudo().search([('user_id', '=', self.env.user.id)], limit=1)
        if not student:
            raise ValidationError(_('Không tìm thấy hồ sơ học viên của tài khoản hiện tại.'))
        progress = self.env['lms.student.lesson.progress'].sudo().get_or_create_progress(student, self)
        vals = {
            'watched_seconds': max(progress.watched_seconds, int(watched_seconds or 0)),
            'last_position_seconds': max(0, int(last_position_seconds or 0)),
        }
        if video_duration_seconds is not None:
            vals['video_duration_seconds'] = max(progress.video_duration_seconds or 0, int(video_duration_seconds or 0))
        progress.write(vals)
        return True

    def action_lesson_face_attendance(self, embedding_json):
        """Điểm danh khuôn mặt (1 lần / bài / học viên). Cần đã đăng ký embedding trên hồ sơ."""
        self.ensure_one()
        if not isinstance(embedding_json, str) or not embedding_json.strip():
            raise ValidationError(_('Thiếu dữ liệu ảnh chụp (embedding).'))
        student = self.env['lms.student'].sudo().search([('user_id', '=', self.env.user.id)], limit=1)
        if not student:
            raise ValidationError(_('Không tìm thấy hồ sơ học viên.'))
        if not student.face_embedding_json:
            raise UserError(_('Vui lòng đăng ký mẫu khuôn mặt trên hồ sơ học viên trước khi điểm danh.'))
        enrolled = self.env['lms.student.course'].sudo().search_count(
            [
                ('student_id', '=', student.id),
                ('course_id', '=', self.course_id.id),
                ('status', 'in', ('approved', 'learning')),
            ]
        )
        if not enrolled:
            raise UserError(_('Bạn chưa được duyệt đăng ký khóa học này.'))
        ref = face_embedding_utils.parse_embedding(student.face_embedding_json)
        probe = face_embedding_utils.parse_embedding(embedding_json)
        if not ref or not probe:
            raise ValidationError(_('Dữ liệu khuôn mặt không hợp lệ.'))
        sim = face_embedding_utils.cosine_similarity(ref, probe)
        if sim < face_embedding_utils.COSINE_MATCH_THRESHOLD:
            raise UserError(
                _('Không khớp khuôn mặt (độ tương đồng %.0f%%). Hãy thử lại với ánh sáng tốt hơn.')
                % (sim * 100)
            )
        progress = self.env['lms.student.lesson.progress'].sudo().get_or_create_progress(student, self)
        if progress.face_checked_in:
            raise UserError(_('Bạn đã điểm danh bài học này.'))
        progress.sudo().write(
            {
                'face_checked_in': True,
                'face_checked_in_at': fields.Datetime.now(),
            }
        )
        return {
            'lms_face_result': True,
            'message': _('Điểm danh thành công.'),
            'progress_status': progress.status,
        }

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
            html = '<p class="text-muted"><i>Chưa có video để xem trực tiếp.</i></p>'
            if lesson.video_attachment and lesson.id:
                stream_url = '/web/content/%s?model=lms.lesson&field=video_attachment&download=false' % lesson.id
                mime = self._guess_video_mime(lesson.video_filename or '')
                html = (
                    '<video class="lms-video-tracker" data-lms-lesson-id="%s" controls preload="metadata" style="width:100%%;max-width:900px;">'
                    '<source src="%s" type="%s"/>'
                    'Trình duyệt không hỗ trợ phát video trực tiếp.'
                    '</video>'
                ) % (lesson.id, stream_url, mime)
            lesson.video_preview_html = html

    @api.depends('video_attachment', 'video_filename')
    def _compute_video_upload_hint_html(self):
        max_mb = self._get_max_video_upload_mb()
        text_hint = (
            'Khuyến nghị: ưu tiên MP4 (hoặc WebM/OGG), dung lượng video nên dưới %sMB.'
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
        text_hint = 'Khuyến nghị file video: ưu tiên MP4 (hoặc WebM/OGG), dung lượng video nên dưới %sMB.' % max_mb
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
        for vals in vals_list:
            # Tạo inline từ one2many thường truyền course_id qua context default_course_id.
            course_id = vals.get('course_id') or ctx_course_id
            if not course_id:
                continue
            course = Course.browse(int(course_id))
            if course.exists() and course.state != 'published':
                raise ValidationError(
                    _('Chỉ có thể tạo bài học khi khóa học đã ở trạng thái "Đã xuất bản".')
                )
        lessons = super().create(vals_list)
        if not self.env.context.get('skip_google_calendar_sync'):
            lessons._google_calendar_sync_if_needed()
        return lessons

    def write(self, vals):
        if self.env.context.get('skip_google_calendar_sync'):
            return super().write(vals)

        was_syncable = {
            lesson.id: lesson.state == 'scheduled' and lesson.lesson_type == 'online'
            for lesson in self
        }
        res = super().write(vals)

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

        if status_changed or lesson_type_changed or (set(vals.keys()) & sync_relevant):
            self.filtered(
                lambda l: l.state == 'scheduled' and l.lesson_type == 'online'
            )._google_calendar_sync_if_needed()
        return res

    def unlink(self):
        if not self.env.context.get('skip_google_calendar_sync'):
            self._google_calendar_unsync(clear_meeting_url=False)
        return super().unlink()


class StudentLessonProgress(models.Model):
    _name = 'lms.student.lesson.progress'
    _description = 'Tiến độ học viên theo bài học'
    _order = 'id desc'

    student_id = fields.Many2one('lms.student', string='Học viên', required=True, ondelete='cascade', index=True)
    lesson_id = fields.Many2one('lms.lesson', string='Bài học', required=True, ondelete='cascade', index=True)
    course_id = fields.Many2one(
        'lms.course',
        string='Khóa học',
        related='lesson_id.course_id',
        store=True,
        readonly=True,
    )
    enrollment_id = fields.Many2one(
        'lms.student.course',
        string='Đăng ký khóa học',
        required=True,
        ondelete='cascade',
        index=True,
    )
    watched_seconds = fields.Integer(string='Số giây đã xem', default=0)
    video_duration_seconds = fields.Integer(string='Thời lượng video (giây)', default=0)
    progress_percent = fields.Float(
        string='Tiến độ (%)',
        compute='_compute_progress_percent',
        store=True,
        digits=(16, 2),
    )
    last_position_seconds = fields.Integer(string='Vị trí xem gần nhất (giây)', default=0)
    face_checked_in = fields.Boolean(string='Đã điểm danh khuôn mặt', default=False, copy=False)
    face_checked_in_at = fields.Datetime(string='Điểm danh lúc', copy=False)
    status = fields.Selection(
        [
            ('not_started', 'Chưa bắt đầu'),
            ('in_progress', 'Đang học'),
            ('done', 'Hoàn thành'),
        ],
        string='Trạng thái',
        compute='_compute_status',
        store=True,
    )
    started_at = fields.Datetime(string='Bắt đầu học', default=fields.Datetime.now)
    completed_at = fields.Datetime(string='Hoàn thành')

    _sql_constraints = [
        ('student_lesson_progress_unique', 'unique(student_id, lesson_id)', 'Tiến độ bài học của học viên đã tồn tại.'),
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
                ('status', '!=', 'cancelled'),
            ],
            limit=1,
        )
        if not enrollment:
            raise ValidationError(_('Học viên chưa đăng ký khóa học chứa bài học này.'))
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
                raise ValidationError(_('Giây xem video không được âm.'))
            if rec.last_position_seconds > rec.watched_seconds:
                raise ValidationError(_('Vị trí gần nhất không được lớn hơn tổng thời gian đã xem.'))

    @api.constrains('enrollment_id', 'lesson_id', 'student_id')
    def _check_enrollment_consistency(self):
        for rec in self:
            if not rec.enrollment_id or not rec.lesson_id:
                continue
            if rec.enrollment_id.student_id != rec.student_id:
                raise ValidationError(_('Enrollment không thuộc đúng học viên của tiến độ bài học.'))
            if rec.enrollment_id.course_id != rec.lesson_id.course_id:
                raise ValidationError(_('Enrollment không thuộc khóa học của bài học đã chọn.'))

    def write(self, vals):
        res = super().write(vals)
        if self.env.context.get('skip_progress_completion_ts'):
            return res
        done_no_ts = self.filtered(lambda r: r.status == 'done' and not r.completed_at)
        if done_no_ts:
            done_no_ts.with_context(skip_progress_completion_ts=True).write(
                {'completed_at': fields.Datetime.now()}
            )
        return res

