# -*- coding: utf-8 -*-

from odoo import fields, models


class CalendarEvent(models.Model):
    _inherit = 'calendar.event'

    lms_learning_history_id = fields.Many2one(
        'lms.learning.history',
        string='Buổi học LMS',
        ondelete='cascade',
        index=True,
        copy=False,
    )
