# -*- coding: utf-8 -*-

from odoo import models, fields, api


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    lms_gemini_api_key = fields.Char(
        string='Gemini API Key',
        config_parameter='gemini.api_key',
        help='API Key từ Google Gemini để sử dụng AI đề xuất khóa học. Lấy tại https://makersuite.google.com/app/apikey'
    )


