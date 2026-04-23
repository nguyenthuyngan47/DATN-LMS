# -*- coding: utf-8 -*-

from odoo import SUPERUSER_ID, api


def _drop_legacy_lms_quiz_tables_once(cr):
    """Xóa bảng PostgreSQL còn sót từ bản LMS cũ (tên bảng lms...quiz...). Một lần / database."""
    cr.execute(
        "SELECT 1 FROM ir_config_parameter WHERE key = %s AND lower(trim(value)) IN ('1', 'true', 'yes')",
        ("lms.legacy_quiz_drop_done",),
    )
    if cr.fetchone():
        return
    cr.execute(
        """
        SELECT tablename FROM pg_tables
        WHERE schemaname = 'public'
          AND tablename LIKE 'lms%%' AND tablename LIKE '%%quiz%%'
        """
    )
    for (tname,) in cr.fetchall():
        if not tname or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789_" for c in tname.lower()):
            continue
        cr.execute(f'DROP TABLE IF EXISTS "{tname}" CASCADE')
    cr.execute(
        """
        INSERT INTO ir_config_parameter (key, value, create_date, write_date)
        VALUES (%s, %s, now(), now())
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, write_date = now()
        """,
        ("lms.legacy_quiz_drop_done", "true"),
    )


def pre_init_hook(cr):
    """Chạy trước khi load module: gỡ bảng legacy nếu có."""
    try:
        _drop_legacy_lms_quiz_tables_once(cr)
    except Exception:  # noqa: BLE001 — không chặn cài module nếu DB lạ/không phải Postgres
        pass


def _cleanup_removed_maintenance_ui(env):
    """Gỡ menu/action/view wizard bảo trì dữ liệu (đã bỏ khỏi module) khi nâng cấp."""
    try:
        for xmlid in (
            "lms.menu_lms_data_maintenance",
            "lms.action_lms_data_maintenance",
            "lms.view_lms_data_maintenance_form",
        ):
            rec = env.ref(xmlid, raise_if_not_found=False)
            if rec:
                rec.unlink()
    except Exception:  # noqa: BLE001
        pass


def post_init_hook(cr, registry=None):
    """
    Chạy khi cài/nâng cấp module (dọn UI gỡ bỏ). Đồng bộ CSV khởi động xử lý tại
    :class:`lms.csv.registry.hook` (mỗi lần Odoo load registry).
    """
    if registry is None:
        env = cr
    else:
        env = api.Environment(cr, SUPERUSER_ID, {})

    if hasattr(env, "ref"):
        _cleanup_removed_maintenance_ui(env)
