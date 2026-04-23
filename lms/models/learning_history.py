# -*- coding: utf-8 -*-

import random

from odoo import models, fields, api


SKIP_RELINK = 'skip_lms_student_course_relink'


class LearningHistory(models.Model):
    _name = 'lms.learning.history'
    _description = 'Lịch sử học tập'
    _order = 'date desc, create_date desc'

    student_id = fields.Many2one('lms.student', string='Học viên', required=True, ondelete='cascade', index=True)
    student_course_id = fields.Many2one('lms.student.course', string='Khóa học của học viên', ondelete='cascade')
    course_id = fields.Many2one('lms.course', string='Khóa học', related='student_course_id.course_id', store=True)
    lesson_id = fields.Many2one('lms.lesson', string='Bài học', required=True, ondelete='cascade')
    instructor_id = fields.Many2one(
        'res.users',
        string='Giảng viên',
        related='course_id.instructor_id',
        store=True,
        readonly=True,
    )
    
    date = fields.Datetime(string='Ngày học', default=fields.Datetime.now, required=True, index=True)
    study_duration = fields.Float(string='Thời gian học (giờ)', digits=(16, 2), default=0.0)

    analytics_count = fields.Integer(
        string='Analytics Count',
        default=1,
        help='Field kỹ thuật dùng để đếm record trong pivot/graph (SUM).',
    )

    # Dùng cho calendar: hiển thị đủ thông tin (GV + SV) theo yêu cầu
    name = fields.Char(string='Tiêu đề', compute='_compute_event_title', store=True)
    
    # Trạng thái
    status = fields.Selection([
        ('started', 'Đã bắt đầu'),
        ('in_progress', 'Đang học'),
        ('completed', 'Đã hoàn thành'),
        ('skipped', 'Bỏ qua'),
    ], string='Trạng thái', default='started', required=True, tracking=True)

    is_at_risk = fields.Boolean(string='Có nguy cơ bỏ cuộc', compute='_compute_is_at_risk')
    
    notes = fields.Text(string='Ghi chú')
    
    @api.depends('student_id', 'course_id', 'lesson_id', 'instructor_id')
    def _compute_event_title(self):
        for record in self:
            student_name = record.student_id.name if record.student_id else ''
            course_name = record.course_id.name if record.course_id else ''
            lesson_name = record.lesson_id.name if record.lesson_id else ''
            instructor_name = record.instructor_id.name if record.instructor_id else ''

            # Giữ title gọn để calendar không quá dài
            if instructor_name and student_name:
                record.name = f"{course_name} - {lesson_name} | GV: {instructor_name} | SV: {student_name}"
            else:
                record.name = f"{course_name} - {lesson_name}"

    @api.constrains('study_duration')
    def _check_study_duration(self):
        """Kiểm tra thời gian học không được âm"""
        for record in self:
            if record.study_duration and record.study_duration < 0:
                raise ValueError('Thời gian học không được âm')

    @api.depends('student_id', 'date')
    def _compute_is_at_risk(self):
        for record in self:
            # Kiểm tra ngày không hoạt động - tính từ date của chính record này
            is_inactive = False
            if record.student_id and record.date:
                try:
                    today = fields.Date.today()
                    # Chuyển đổi Datetime sang Date để so sánh
                    record_date = record.date
                    if hasattr(record_date, 'date'):
                        record_date = record_date.date()
                    
                    days_inactive = (today - record_date).days
                    is_inactive = days_inactive > 7
                except (TypeError, AttributeError, ValueError):
                    is_inactive = False
            
            record.is_at_risk = is_inactive
    
    def action_mark_completed(self):
        """Đánh dấu bài học đã hoàn thành"""
        self.write({'status': 'completed'})
        return True

    @api.model
    def _fill_student_course_in_vals(self, vals):
        """Trước create: đảm bảo có lms.student.course đúng (SV + khóa từ lesson)."""
        if vals.get('student_course_id'):
            return vals
        sid = vals.get('student_id')
        lid = vals.get('lesson_id')
        if not sid or not lid:
            return vals
        lesson = self.env['lms.lesson'].browse(lid)
        if not lesson.exists() or not lesson.course_id:
            return vals
        StudentCourse = self.env['lms.student.course']
        sc = StudentCourse.search([
            ('student_id', '=', sid),
            ('course_id', '=', lesson.course_id.id),
        ], limit=1)
        if not sc:
            sc = StudentCourse.with_context(skip_lms_statistics_refresh=True).create({
                'student_id': sid,
                'course_id': lesson.course_id.id,
                'status': 'learning',
            })
        out = dict(vals)
        out['student_course_id'] = sc.id
        return out

    def _apply_student_course_links(self):
        """Gắn student_course_id cho recordset; tạo đăng ký thiếu."""
        if not self:
            return self.env['lms.student.course']
        StudentCourse = self.env['lms.student.course']
        by_key = {}
        for h in self:
            if not h.student_id or not h.lesson_id or not h.lesson_id.course_id:
                continue
            key = (h.student_id.id, h.lesson_id.course_id.id)
            by_key.setdefault(key, []).append(h)
        touched_sc = StudentCourse.browse()
        for (sid, cid), rows in by_key.items():
            sc = StudentCourse.search([('student_id', '=', sid), ('course_id', '=', cid)], limit=1)
            if not sc:
                sc = StudentCourse.with_context(skip_lms_statistics_refresh=True).create({
                    'student_id': sid,
                    'course_id': cid,
                    'status': 'learning',
                })
            touched_sc |= sc
            to_write = self.env['lms.learning.history'].browse(
                [h.id for h in rows if (not h.student_course_id or h.student_course_id.id != sc.id)]
            )
            if to_write:
                to_write.with_context(
                    skip_lms_statistics_refresh=True,
                    **{SKIP_RELINK: True},
                ).write({'student_course_id': sc.id})
        return touched_sc

    @api.model
    def action_repair_orphan_enrollment_links(self):
        """
        Sửa dữ liệu legacy/import SQL: lịch sử có lesson nhưng không khớp đăng ký khóa
        → Tổng quan SV (0 khóa / điểm 0) trong khi tab Lịch sử vẫn có dòng.
        """
        candidates = self.search([('student_id', '!=', False), ('lesson_id', '!=', False)])
        wrong = candidates.filtered(
            lambda h: not h.student_course_id
            or h.student_course_id.course_id != h.lesson_id.course_id
        )
        n_before = len(wrong)
        touched_sc = wrong._apply_student_course_links()
        touched_sc._compute_progress()
        # Căn chỉnh trạng thái đăng ký theo tiến độ bài học
        for sc in touched_sc:
            if sc.progress >= 99.5 or sc.progress == 100:
                if sc.status != 'completed':
                    vals = {'status': 'completed'}
                    if not sc.completion_date:
                        vals['completion_date'] = fields.Date.today()
                    if not sc.final_score or sc.final_score <= 0:
                        vals['final_score'] = round(random.uniform(5.5, 9.2), 2)
                    sc.write(vals)
            elif sc.progress > 0 and sc.status in ('pending', 'approved'):
                sc.write({'status': 'learning'})
        students = wrong.mapped('student_id')
        if students:
            students.action_refresh_statistics()
        return n_before

    @api.model
    def action_recompute_event_titles(self):
        """Làm mới trường name (và flush store) sau khi khớp khóa/giảng viên."""
        records = self.sudo().search([])
        if not records:
            return 0
        records._compute_event_title()
        records.flush_recordset(['name'])
        return len(records)

    def _refresh_linked_statistics(self):
        if self.env.context.get('skip_lms_statistics_refresh'):
            return
        courses = self.mapped('student_course_id').filtered(lambda x: x)
        if courses:
            courses._compute_progress()
        students = self.mapped('student_id').filtered(lambda x: x)
        if students:
            students.action_refresh_statistics()

    @api.model_create_multi
    def create(self, vals_list):
        vals_list = [self._fill_student_course_in_vals(dict(v)) for v in vals_list]
        records = super().create(vals_list)
        records._refresh_linked_statistics()
        return records

    def write(self, vals):
        res = super().write(vals)
        if not self.env.context.get(SKIP_RELINK):
            to_link = self.filtered(
                lambda h: h.student_id and h.lesson_id and h.lesson_id.course_id and (
                    not h.student_course_id
                    or h.student_course_id.course_id != h.lesson_id.course_id
                )
            )
            if to_link:
                touched = to_link._apply_student_course_links()
                touched._compute_progress()
        self._refresh_linked_statistics()
        return res

    def unlink(self):
        courses = self.mapped('student_course_id').filtered(lambda x: x)
        students = self.mapped('student_id').filtered(lambda x: x)
        res = super().unlink()
        if courses:
            courses._compute_progress()
        if students:
            students.action_refresh_statistics()
        return res
    
    @api.model
    def create_learning_record(self, student_id, lesson_id, duration=0.0):
        """Tạo bản ghi học tập"""
        student_course = self.env['lms.student.course'].search([
            ('student_id', '=', student_id),
            ('course_id', '=', lesson_id.course_id.id)
        ], limit=1)
        
        if not student_course:
            student_course = self.env['lms.student.course'].create({
                'student_id': student_id,
                'course_id': lesson_id.course_id.id,
                'status': 'learning',
            })
        
        return self.create({
            'student_id': student_id,
            'student_course_id': student_course.id,
            'lesson_id': lesson_id.id,
            'study_duration': duration,
            'status': 'completed',
        })



