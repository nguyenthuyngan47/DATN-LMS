# -*- coding: utf-8 -*-

from odoo import models, fields, api


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    lms_gemini_api_key = fields.Char(
        string='Gemini API Key',
        config_parameter='gemini.api_key',
        help='API Key from Google Gemini for AI course recommendations. Get it at https://makersuite.google.com/app/apikey'
    )


