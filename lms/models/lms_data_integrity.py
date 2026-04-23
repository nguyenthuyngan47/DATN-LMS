# -*- coding: utf-8 -*-
"""Đồng bộ liên kết giữa các model LMS (không tạo bảng)."""

from odoo import api, models


class LmsDataIntegrity(models.AbstractModel):
    _name = 'lms.data.integrity'
    _description = 'Đồng bộ toàn bộ liên kết dữ liệu LMS'

    @api.model
    def run_full_repair(self):
        """
        Thứ tự:
        1) Gộp đăng ký trùng (student + course)
        2) Khớp lịch sử ↔ đăng ký + trạng thái/điểm theo tiến độ
        3) Làm mới tiêu đề lịch sử (name) và flush trường store
        4) Tính lại progress từng đăng ký
        5) Thống kê sinh viên
        6) Số học viên theo khóa
        7) Thống kê roadmap
        8) Gỡ liên kết calendar.event → lịch sử đã xóa
        """
        report = {}

        SC = self.env['lms.student.course'].sudo()
        report['merge_duplicate_enrollments'] = SC.action_merge_duplicate_enrollments()

        History = self.env['lms.learning.history'].sudo()
        report['history_relink_count'] = History.action_repair_orphan_enrollment_links()
        report['history_titles_refreshed'] = History.action_recompute_event_titles()

        SC.search([])._compute_progress()

        students = self.env['lms.student'].sudo().search([])
        students.action_refresh_statistics()

        Course = self.env['lms.course'].sudo()
        courses = Course.search([])
        courses.invalidate_recordset(['enrolled_students_count', 'total_lessons'])
        courses._compute_enrolled_students()
        courses._compute_total_lessons()
        report['courses_refreshed'] = len(courses)

        Roadmap = self.env['lms.roadmap'].sudo()
        rms = Roadmap.search([])
        for rm in rms:
            rm._compute_total_courses()
            rm._compute_term_courses()
            rm._compute_completed_courses()
        report['roadmaps_refreshed'] = len(rms)

        Lecturer = self.env.get('lms.lecturer')
        if Lecturer:
            report['lecturers_synced'] = Lecturer.sudo().action_sync_from_existing_instructors()
        else:
            report['lecturers_synced'] = 0

        Calendar = self.env.get('calendar.event')
        if Calendar:
            report['calendar_orphans_cleared'] = self._clear_orphan_calendar_lms_links(Calendar.sudo())
        else:
            report['calendar_orphans_cleared'] = 0

        return report

    def _clear_orphan_calendar_lms_links(self, Calendar):
        """Xóa Many2one tới lms.learning.history nếu bản ghi lịch sử không còn."""
        events = Calendar.search([('lms_learning_history_id', '!=', False)])
        n = 0
        for ev in events:
            if not ev.lms_learning_history_id.exists():
                ev.write({'lms_learning_history_id': False})
                n += 1
        return n
