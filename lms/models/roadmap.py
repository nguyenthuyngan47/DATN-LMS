# -*- coding: utf-8 -*-

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class Roadmap(models.Model):
    _name = 'lms.roadmap'
    _description = 'Learning Roadmap'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'create_date desc'

    name = fields.Char(string='Roadmap Name', compute='_compute_name', store=True)
    student_id = fields.Many2one('lms.student', string='Student', required=True, ondelete='cascade', index=True)
    
    # Thời gian
    create_date = fields.Datetime(string='Created Date', readonly=True, default=fields.Datetime.now)
    valid_from = fields.Date(string='Valid From', default=fields.Date.today)
    valid_to = fields.Date(string='Valid To')
    
    # Trạng thái
    state = fields.Selection([
        ('draft', 'Draft'),
        ('suggested', 'Suggested'),
        ('approved', 'Approved'),
        ('locked', 'Locked'),
        ('rejected', 'Rejected'),
    ], string='Status', default='draft', tracking=True)
    
    # Roadmap review
    # Who approved/locked/rejected the roadmap.
    reviewed_by = fields.Many2one('res.users', string='Reviewed By', tracking=True)
    review_date = fields.Datetime(string='Review Date', tracking=True)
    review_notes = fields.Text(string='Review Notes')
    
    # Các khóa học đề xuất
    course_line_ids = fields.One2many('lms.roadmap.course', 'roadmap_id', string='Suggested Courses')
    total_courses = fields.Integer(string='Total Courses', compute='_compute_total_courses', store=True)
    
    # Phân loại theo thời gian
    short_term_courses = fields.Integer(string='Short Term', compute='_compute_term_courses', store=True)
    medium_term_courses = fields.Integer(string='Medium Term', compute='_compute_term_courses', store=True)
    long_term_courses = fields.Integer(string='Long Term', compute='_compute_term_courses', store=True)
    
    # AI Analysis
    ai_recommendation_reason = fields.Text(string='AI Recommendation Reason')
    recommendation_method = fields.Selection([
        ('content_based', 'Content-Based Filtering'),
        ('rule_based', 'Rule-Based Recommendation'),
        ('hybrid', 'Hybrid'),
    ], string='Recommendation Method', tracking=True)
    
    # Thống kê
    completed_courses_count = fields.Integer(string='Completed', compute='_compute_completed_courses', store=True)
    in_progress_courses_count = fields.Integer(string='In Progress', compute='_compute_completed_courses', store=True)
    
    @api.depends('student_id', 'create_date')
    def _compute_name(self):
        for record in self:
            date_str = fields.Datetime.to_string(record.create_date)[:10]
            if record.student_id:
                record.name = _('Roadmap for %s - %s') % (record.student_id.name, date_str)
            else:
                record.name = _('Roadmap - %s') % (date_str,)
    
    @api.depends('course_line_ids')
    def _compute_total_courses(self):
        for record in self:
            record.total_courses = len(record.course_line_ids)
    
    @api.depends('course_line_ids', 'course_line_ids.timeframe')
    def _compute_term_courses(self):
        for record in self:
            record.short_term_courses = len(record.course_line_ids.filtered(lambda x: x.timeframe == 'short'))
            record.medium_term_courses = len(record.course_line_ids.filtered(lambda x: x.timeframe == 'medium'))
            record.long_term_courses = len(record.course_line_ids.filtered(lambda x: x.timeframe == 'long'))
    
    @api.depends('course_line_ids', 'course_line_ids.status')
    def _compute_completed_courses(self):
        for record in self:
            record.completed_courses_count = len(record.course_line_ids.filtered(lambda x: x.status == 'completed'))
            record.in_progress_courses_count = len(record.course_line_ids.filtered(lambda x: x.status == 'in_progress'))
    
    def action_approve(self):
        """Phê duyệt roadmap"""
        reviewer = self.env.user
        self.write({
            'state': 'approved',
            'reviewed_by': reviewer.id if reviewer else False,
            'review_date': fields.Datetime.now(),
        })
        # Gửi email thông báo cho sinh viên
        if self.student_id.email:
            template = self.env.ref('lms.email_template_roadmap_approved', raise_if_not_found=False)
            if template:
                template.send_mail(self.id, force_send=True)
        return True
    
    def action_lock(self):
        """Khóa roadmap"""
        reviewer = self.env.user
        self.write({
            'state': 'locked',
            'reviewed_by': reviewer.id if reviewer else False,
            'review_date': fields.Datetime.now(),
        })
        return True
    
    def action_reject(self):
        """Từ chối roadmap"""
        reviewer = self.env.user
        self.write({
            'state': 'rejected',
            'reviewed_by': reviewer.id if reviewer else False,
            'review_date': fields.Datetime.now(),
        })
        return True

    def action_open_form(self):
        """Mở form roadmap (dùng sau tư vấn AI hoặc từ hồ sơ sinh viên)."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Learning Roadmap'),
            'res_model': 'lms.roadmap',
            'view_mode': 'form',
            'res_id': self.id,
            'target': 'current',
        }


class RoadmapCourse(models.Model):
    _name = 'lms.roadmap.course'
    _description = 'Roadmap Course'
    _order = 'priority desc, sequence'

    roadmap_id = fields.Many2one('lms.roadmap', string='Roadmap', required=True, ondelete='cascade')
    course_id = fields.Many2one(
        'lms.course', string='Course', required=True, ondelete='cascade'
    )
    
    sequence = fields.Integer(string='Sequence', default=10)
    priority = fields.Selection([
        ('high', 'High'),
        ('medium', 'Medium'),
        ('low', 'Low'),
    ], string='Priority', default='medium', required=True)
    
    timeframe = fields.Selection([
        ('short', 'Short Term (1-2 weeks)'),
        ('medium', 'Medium Term (1-3 months)'),
        ('long', 'Long Term (3+ months)'),
    ], string='Timeframe', default='medium', required=True)
    
    status = fields.Selection([
        ('pending', 'Not Started'),
        ('in_progress', 'In Progress'),
        ('completed', 'Completed'),
        ('skipped', 'Skipped'),
    ], string='Status', default='pending', tracking=True)
    
    # Lý do đề xuất
    recommendation_reason = fields.Text(string='Recommendation Reason')
    similarity_score = fields.Float(string='Similarity Score', digits=(16, 2), help='Similarity score with previously completed courses')
    
    # Tài liệu bổ trợ
    supplementary_materials = fields.Text(string='Supplementary Materials')
    reminder_date = fields.Date(string='Reminder Date')
    
    # Thông tin khóa học (related)
    course_name = fields.Char(string='Course Name', related='course_id.name', readonly=True)
    course_category = fields.Char(string='Category', related='course_id.category_id.name', readonly=True)
    course_level = fields.Char(string='Level', related='course_id.level_id.name', readonly=True)

    enrollment_status = fields.Selection(
        [
            ('not_enrolled', 'Not Enrolled'),
            ('pending', 'Pending Approval'),
            ('approved', 'Approved'),
            ('learning', 'Learning'),
            ('completed', 'Completed'),
            ('rejected', 'Rejected'),
            ('full', 'Course Full'),
            ('unavailable', 'Not Available'),
            ('prerequisite_blocked', 'Prerequisites Not Met'),
        ],
        string='Registration Status',
        compute='_compute_enrollment_status',
    )
    show_enroll_button = fields.Boolean(
        string='Show Enroll Button',
        compute='_compute_enrollment_status',
    )

    @api.depends(
        'course_id',
        'course_id.state',
        'course_id.is_active',
        'course_id.max_student',
        'course_id.prerequisite_ids',
        'roadmap_id.student_id',
        'roadmap_id.student_id.enrolled_courses_ids',
        'roadmap_id.student_id.enrolled_courses_ids.status',
        'roadmap_id.student_id.enrolled_courses_ids.course_id',
    )
    def _compute_enrollment_status(self):
        Enrollment = self.env['lms.student.course'].sudo()
        for line in self:
            line.show_enroll_button = False
            line.enrollment_status = 'not_enrolled'
            student = line.roadmap_id.student_id
            course = line.course_id
            if not student or not course:
                line.enrollment_status = 'unavailable'
                continue
            enrollment = Enrollment.search(
                [('student_id', '=', student.id), ('course_id', '=', course.id)],
                limit=1,
            )
            if enrollment and enrollment.status != 'rejected':
                line.enrollment_status = enrollment.status
                continue
            if course.state != 'published' or not course.is_active:
                line.enrollment_status = 'unavailable'
                continue
            cap = course.max_student or 0
            if cap >= 1 and course._get_occupied_seat_count() >= cap:
                line.enrollment_status = 'full'
                continue
            missing_prereqs = course._get_unmet_prerequisite_courses(student)
            if missing_prereqs:
                line.enrollment_status = 'prerequisite_blocked'
                line.show_enroll_button = True
                continue
            if enrollment and enrollment.status == 'rejected':
                line.enrollment_status = 'rejected'
            else:
                line.enrollment_status = 'not_enrolled'
            line.show_enroll_button = True

    def _check_student_may_enroll(self):
        self.ensure_one()
        student = self.roadmap_id.student_id
        user = self.env.user
        is_owner = student.user_id == user
        is_staff = user.has_group('lms.group_lms_manager') or user.has_group('base.group_system')
        if not is_owner and not is_staff:
            raise UserError(_('You can only register for courses on your own roadmap.'))
        if not student:
            raise UserError(_('Roadmap has no linked student.'))

    def action_enroll(self):
        """Đăng ký khóa học từ dòng roadmap (trạng thái pending, chờ duyệt)."""
        self.ensure_one()
        self._check_student_may_enroll()
        student = self.roadmap_id.student_id
        course = self.course_id
        if not course:
            raise UserError(_('No course linked to this roadmap line.'))
        if self.enrollment_status == 'full':
            raise UserError(_('Course "%s" is full.') % course.name)
        if self.enrollment_status == 'unavailable':
            raise UserError(
                _('Course "%s" is not open for enrollment (unpublished or inactive).') % course.name
            )
        bypass_prereq = self.env['lms.course']._user_may_bypass_prerequisite_rules()
        if not bypass_prereq:
            course._raise_if_prerequisites_unmet(student)
        allowed_statuses = ('not_enrolled', 'rejected')
        if bypass_prereq:
            allowed_statuses = ('not_enrolled', 'rejected', 'prerequisite_blocked')
        if self.enrollment_status not in allowed_statuses:
            status_label = dict(self._fields['enrollment_status'].selection).get(
                self.enrollment_status, self.enrollment_status
            )
            raise UserError(
                _('You have already registered for "%(course)s" (%(status)s).')
                % {'course': course.name, 'status': status_label}
            )

        Enrollment = self.env['lms.student.course'].sudo()
        enrollment = Enrollment.search(
            [('student_id', '=', student.id), ('course_id', '=', course.id)],
            limit=1,
        )
        vals = {
            'student_id': student.id,
            'course_id': course.id,
            'status': 'pending',
            'enrollment_date': fields.Date.today(),
            'start_date': course.start_date,
            'end_date': course.end_date,
            'final_score': False,
        }
        if enrollment:
            enrollment.write(vals)
        else:
            Enrollment.create(vals)

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Course Registration'),
                'message': _(
                    'Registered for "%(course)s". Status: Pending approval.'
                )
                % {'course': course.name},
                'type': 'success',
                'sticky': False,
                'next': {'type': 'ir.actions.client', 'tag': 'reload'},
            },
        }

