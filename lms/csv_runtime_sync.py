# -*- coding: utf-8 -*-
"""Đồng bộ DB ← thư mục CSV (hook khi load registry và/hoặc cron định kỳ)."""

from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path

from odoo import SUPERUSER_ID, api
from odoo.tools import sql

from . import csv_bootstrap

_logger = logging.getLogger(__name__)

PARAM_BUNDLE_HASH = "lms.csv.bundle_hash"
_ADV_KEYS = (582911, 772233)


def _env_truthy(key: str, default: str = "1") -> bool:
    return os.environ.get(key, default).strip().lower() in ("1", "true", "yes")


def _csv_sync_enabled() -> bool:
    """Tắt toàn bộ đồng bộ CSV (hook + cron) khi LMS_CSV_SYNC_ENABLED=0."""
    return _env_truthy("LMS_CSV_SYNC_ENABLED", "1")


def _csv_on_registry_load_enabled() -> bool:
    """
    Chỉ áp dụng khi gọi từ hook registry (khởi động / reload module).

    - Ưu tiên ``LMS_CSV_ON_REGISTRY_LOAD`` nếu có và không rỗng.
    - Ngược lại ``LMS_CSV_ON_START`` (mặc định **tắt** = khởi động nhanh; cron vẫn sync).
    """
    explicit = os.environ.get("LMS_CSV_ON_REGISTRY_LOAD")
    if explicit is not None and str(explicit).strip() != "":
        return _env_truthy("LMS_CSV_ON_REGISTRY_LOAD", "1")
    return _env_truthy("LMS_CSV_ON_START", "0")


def _csv_delete_missing_managed() -> bool:
    return os.environ.get("LMS_CSV_DELETE_MISSING_MANAGED", "0").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def fingerprint_csv_bundle(csv_dir: Path) -> str:
    digest = hashlib.sha256()
    for p in sorted(csv_dir.glob("lms_*.csv")):
        try:
            digest.update(p.name.encode("utf-8"))
            digest.update(b"\n")
            with p.open("rb") as f:
                while True:
                    chunk = f.read(65536)
                    if not chunk:
                        break
                    digest.update(chunk)
            digest.update(b"\n")
        except OSError:
            digest.update(f"{p.name}:missing\n".encode("utf-8"))
    return digest.hexdigest()


def _get_stored_hash(env) -> str | None:
    v = env["ir.config_parameter"].sudo().get_param(PARAM_BUNDLE_HASH)
    return (v or "").strip() or None


def _set_stored_hash(env, digest: str) -> None:
    env["ir.config_parameter"].sudo().set_param(PARAM_BUNDLE_HASH, digest)


def _run_lms_post_import_sync(cr):
    try:
        odoo_env = api.Environment(cr, SUPERUSER_ID, {})
        mode = os.environ.get("LMS_CSV_POST_IMPORT_MODE", "full").strip().lower()

        # "full" = chạy toàn bộ run_full_repair + refresh thống kê (chậm nhưng đảm bảo dữ liệu khớp).
        # "minimal" = chỉ tính các phần thường cần thiết để UI không lệch quá nhiều (nhanh hơn).
        # "none" = không chạy bước recompute sau import (nhanh nhất, nhưng có thể hiển thị số liệu chưa đúng ngay).
        if mode == "full":
            try:
                odoo_env["lms.data.integrity"].sudo().run_full_repair()
            except Exception:  # noqa: BLE001
                odoo_env["lms.learning.history"].sudo().action_repair_orphan_enrollment_links()
            # run_full_repair đã refresh nhiều thứ, nhưng vẫn giữ bước làm mới UI store để an toàn.
            students = odoo_env["lms.student"].search([])
            for st in students:
                st.action_refresh_statistics()
            scs = odoo_env["lms.student.course"].search([])
            for sc in scs:
                sc._compute_progress()
            courses = odoo_env["lms.course"].search([])
            courses.invalidate_recordset(["enrolled_students_count", "total_lessons"])
            courses._compute_enrolled_students()
            courses._compute_total_lessons()
            if courses:
                courses.flush_recordset(["enrolled_students_count", "total_lessons"])
            if "lms.lecturer" in odoo_env.registry:
                odoo_env["lms.lecturer"].sudo().action_sync_from_existing_instructors()
            return

        if mode == "minimal":
            # Không chạy run_full_repair để tránh các bước merge/relink/roadmap nặng.
            scs = odoo_env["lms.student.course"].search([])
            for sc in scs:
                sc._compute_progress()

            students = odoo_env["lms.student"].search([])
            for st in students:
                st.action_refresh_statistics()

            courses = odoo_env["lms.course"].search([])
            courses.invalidate_recordset(["enrolled_students_count", "total_lessons"])
            courses._compute_enrolled_students()
            courses._compute_total_lessons()
            if courses:
                courses.flush_recordset(["enrolled_students_count", "total_lessons"])
            if "lms.lecturer" in odoo_env.registry:
                odoo_env["lms.lecturer"].sudo().action_sync_from_existing_instructors()
            return

        if mode == "none":
            return

        # Fallback nếu mode lạ: chạy full để an toàn.
        try:
            odoo_env["lms.data.integrity"].sudo().run_full_repair()
        except Exception:  # noqa: BLE001
            odoo_env["lms.learning.history"].sudo().action_repair_orphan_enrollment_links()
    except Exception:  # noqa: BLE001
        _logger.exception("LMS CSV sync: post-import recompute thất bại.")


def sync_csv_bundle_if_needed(env, *, from_registry_hook: bool = False):
    """
    Đồng bộ CSV khi fingerprint đổi (một lần / thay đổi bộ file).

    - ``LMS_CSV_SYNC_ENABLED`` (mặc định bật): tắt toàn bộ (hook + cron) nếu 0.
    - ``LMS_CSV_ON_REGISTRY_LOAD`` hoặc ``LMS_CSV_ON_START``: chỉ điều khiển **hook
      khi load registry**. ``LMS_CSV_ON_START=0`` → không import nặng lúc cài/khởi động;
      **cron vẫn chạy** sync nếu ``LMS_CSV_SYNC_ENABLED`` bật.
    - Không purge toàn domain; dùng safe upsert.
    - ``LMS_CSV_DELETE_MISSING_MANAGED=1``: xóa record có external-id CSV không còn trong source.
    - Advisory lock Postgres tránh nhiều worker import cùng lúc.
    """
    if not _csv_sync_enabled():
        return
    if from_registry_hook and not _csv_on_registry_load_enabled():
        _logger.debug(
            "LMS CSV sync: bỏ qua hook registry (LMS_CSV_ON_REGISTRY_LOAD / LMS_CSV_ON_START)."
        )
        return

    cr = env.cr
    if not sql.table_exists(cr, "ir_module_module"):
        return

    Mod = env["ir.module.module"].sudo()
    if not Mod.search_count([("name", "=", "lms"), ("state", "=", "installed")]):
        return

    csv_dir_s = csv_bootstrap.get_csv_import_dir()
    csv_dir = Path(csv_dir_s)
    marker = csv_dir / "lms_course.csv"

    if not csv_dir.is_dir():
        _logger.warning(
            "LMS CSV sync: không có thư mục %s (mount hoặc LMS_CSV_IMPORT_DIR).",
            csv_dir_s,
        )
        return
    if not marker.is_file():
        _logger.warning("LMS CSV sync: thiếu %s — bỏ qua.", marker)
        return

    digest = fingerprint_csv_bundle(csv_dir)
    stored = _get_stored_hash(env)
    delete_missing_managed = _csv_delete_missing_managed()

    if stored == digest:
        _logger.debug("LMS CSV sync: bộ CSV không đổi, bỏ qua.")
        return

    cr.execute("SELECT pg_try_advisory_lock(%s, %s)", _ADV_KEYS)
    row = cr.fetchone()
    got_lock = bool(row and row[0])
    if not got_lock:
        _logger.debug("LMS CSV sync: worker khác đang giữ lock, bỏ qua.")
        return

    try:
        _logger.info(
            "LMS CSV sync: đồng bộ từ %s (safe_upsert=%s, delete_missing_managed=%s).",
            csv_dir_s,
            _env_truthy("LMS_CSV_SAFE_UPSERT", "1"),
            delete_missing_managed,
        )
        safe_upsert = _env_truthy("LMS_CSV_SAFE_UPSERT", "1")
        csv_bootstrap.import_lms_from_csv_directory(
            env,
            csv_dir_s,
            safe_upsert=safe_upsert,
            delete_missing_managed=delete_missing_managed,
        )
        _run_lms_post_import_sync(cr)
        _set_stored_hash(env, digest)
        env["ir.config_parameter"].sudo().set_param("lms.csv_import_done", "true")
        _logger.info("LMS CSV sync: hoàn tất.")
    except Exception:  # noqa: BLE001
        _logger.exception("LMS CSV sync: lỗi — DB có thể chưa khớp CSV.")
    finally:
        cr.execute("SELECT pg_advisory_unlock(%s, %s)", _ADV_KEYS)
