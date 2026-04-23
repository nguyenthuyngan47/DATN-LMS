# -*- coding: utf-8 -*-

from .tools.env_loader import load_lms_dotenv

load_lms_dotenv()

from . import models
from . import wizards
from . import controllers


def _patch_web_signup_params():
    """Cho phép lms_register, lms_lecturer_document trên /web/signup và POST."""
    try:
        from odoo.addons.web.controllers import home

        home.SIGN_UP_REQUEST_PARAMS.update(
            ('lms_register', 'lms_lecturer_document'),
        )
    except Exception:
        pass


_patch_web_signup_params()

# Ensure post-init hook is importable by Odoo.
from .hooks import post_init_hook, pre_init_hook  # noqa: F401



