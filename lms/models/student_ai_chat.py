# -*- coding: utf-8 -*-

from odoo import fields, models


class LmsStudentAiChat(models.TransientModel):
    _name = 'lms.student.ai.chat'
    _description = 'Tư vấn roadmap AI cho học viên'

    title = fields.Char(
        string='Tiêu đề phiên tư vấn',
        default='Tư vấn roadmap học tập',
    )
    student_id = fields.Many2one(
        'lms.student',
        string='Học viên',
        readonly=True,
        default=lambda self: self.env['lms.student'].sudo().search(
            [('user_id', '=', self.env.user.id)],
            limit=1,
        ),
    )
    user_message = fields.Text(
        string='Nội dung bạn muốn tư vấn',
        help='Màn hình này là placeholder. Luồng chat AI sẽ được bổ sung ở bước sau.',
    )
    ai_response_placeholder = fields.Html(
        string='Phản hồi AI',
        readonly=True,
        default='<p><i>Chưa có phản hồi AI. Chức năng chat sẽ được triển khai ở bước tiếp theo.</i></p>',
    )
