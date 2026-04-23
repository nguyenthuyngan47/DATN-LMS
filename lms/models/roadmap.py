# -*- coding: utf-8 -*-

from odoo import models, fields, api


class Roadmap(models.Model):
    _name = 'lms.roadmap'
    _description = 'Roadmap đề xuất học tập'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'create_date desc'

    name = fields.Char(string='Tên roadmap', compute='_compute_name', store=True)
    student_id = fields.Many2one('lms.student', string='Học viên', required=True, ondelete='cascade', index=True)
    
    # Thời gian
    create_date = fields.Datetime(string='Ngày tạo', readonly=True, default=fields.Datetime.now)
    valid_from = fields.Date(string='Có hiệu lực từ', default=fields.Date.today)
    valid_to = fields.Date(string='Có hiệu lực đến')
    
    # Trạng thái
    state = fields.Selection([
        ('draft', 'Nháp'),
        ('suggested', 'Đã đề xuất'),
        ('approved', 'Đã phê duyệt'),
        ('locked', 'Đã khóa'),
        ('rejected', 'Từ chối'),
    ], string='Trạng thái', default='draft', tracking=True)
    
    # Roadmap review
    # Who approved/locked/rejected the roadmap.
    reviewed_by = fields.Many2one('res.users', string='Được xem xét bởi', tracking=True)
    review_date = fields.Datetime(string='Ngày xem xét', tracking=True)
    review_notes = fields.Text(string='Ghi chú xem xét')
    
    # Các khóa học đề xuất
    course_line_ids = fields.One2many('lms.roadmap.course', 'roadmap_id', string='Khóa học đề xuất')
    total_courses = fields.Integer(string='Tổng số khóa học', compute='_compute_total_courses', store=True)
    
    # Phân loại theo thời gian
    short_term_courses = fields.Integer(string='Ngắn hạn', compute='_compute_term_courses', store=True)
    medium_term_courses = fields.Integer(string='Trung hạn', compute='_compute_term_courses', store=True)
    long_term_courses = fields.Integer(string='Dài hạn', compute='_compute_term_courses', store=True)
    
    # AI Analysis
    ai_recommendation_reason = fields.Text(string='Lý do đề xuất từ AI')
    recommendation_method = fields.Selection([
        ('content_based', 'Content-Based Filtering'),
        ('rule_based', 'Rule-Based Recommendation'),
        ('hybrid', 'Hybrid (Kết hợp)'),
    ], string='Phương pháp đề xuất', tracking=True)
    
    # Thống kê
    completed_courses_count = fields.Integer(string='Đã hoàn thành', compute='_compute_completed_courses', store=True)
    in_progress_courses_count = fields.Integer(string='Đang học', compute='_compute_completed_courses', store=True)
    
    @api.depends('student_id', 'create_date')
    def _compute_name(self):
        for record in self:
            if record.student_id:
                record.name = f"Roadmap cho {record.student_id.name} - {fields.Datetime.to_string(record.create_date)[:10]}"
            else:
                record.name = f"Roadmap - {fields.Datetime.to_string(record.create_date)[:10]}"
    
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


class RoadmapCourse(models.Model):
    _name = 'lms.roadmap.course'
    _description = 'Khóa học trong roadmap'
    _order = 'priority desc, sequence'

    roadmap_id = fields.Many2one('lms.roadmap', string='Roadmap', required=True, ondelete='cascade')
    course_id = fields.Many2one(
        'lms.course', string='Khóa học', required=True, ondelete='cascade'
    )
    
    sequence = fields.Integer(string='Thứ tự', default=10)
    priority = fields.Selection([
        ('high', 'Cao'),
        ('medium', 'Trung bình'),
        ('low', 'Thấp'),
    ], string='Ưu tiên', default='medium', required=True)
    
    timeframe = fields.Selection([
        ('short', 'Ngắn hạn (1-2 tuần)'),
        ('medium', 'Trung hạn (1-3 tháng)'),
        ('long', 'Dài hạn (3+ tháng)'),
    ], string='Thời gian', default='medium', required=True)
    
    status = fields.Selection([
        ('pending', 'Chưa bắt đầu'),
        ('in_progress', 'Đang học'),
        ('completed', 'Đã hoàn thành'),
        ('skipped', 'Bỏ qua'),
    ], string='Trạng thái', default='pending', tracking=True)
    
    # Lý do đề xuất
    recommendation_reason = fields.Text(string='Lý do đề xuất')
    similarity_score = fields.Float(string='Độ tương đồng', digits=(16, 2), help='Điểm tương đồng với khóa học đã học')
    
    # Tài liệu bổ trợ
    supplementary_materials = fields.Text(string='Tài liệu bổ trợ')
    reminder_date = fields.Date(string='Ngày nhắc nhở')
    
    # Thông tin khóa học (related)
    course_name = fields.Char(string='Tên khóa học', related='course_id.name', readonly=True)
    course_category = fields.Char(string='Danh mục', related='course_id.category_id.name', readonly=True)
    course_level = fields.Char(string='Cấp độ', related='course_id.level_id.name', readonly=True)

