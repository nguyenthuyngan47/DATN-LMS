#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Import dữ liệu LMS từ thư mục CSV (scripts/export/*.csv) vào Odoo qua XML-RPC.

Dùng khi bạn đã có bộ file export CSV và muốn đưa vào database Odoo (tạo bản ghi mới,
ID trong CSV được ánh xạ sang ID mới trong Odoo).

Yêu cầu: Odoo đang chạy, module `lms` đã cài, tài khoản có quyền tạo bản ghi LMS.

Chạy (từ thư mục repo hoặc chỉ định đủ đường dẫn):

  set ODOO_DB=ten_database
  set ODOO_PASSWORD=mat_khau_admin
  python scripts/import_csv_to_odoo.py

Tham số:
  --export-dir   mặc định: <thư_mục_script>/export
  --url          mặc định ODOO_URL hoặc http://localhost:8069
  --login        mặc định admin
  --db           ODOO_DB
  --password     ODOO_PASSWORD

Sau import: nếu cần, Upgrade lại module lms hoặc tạo bản ghi qua UI — ORM sẽ tính lại thống kê
khi sửa lịch sử/đăng ký; có thể gọi thủ công action_refresh_statistics trên sinh viên nếu import tay.
"""

from __future__ import annotations

import argparse
import csv
import logging
import os
import sys
import xmlrpc.client
from pathlib import Path
from typing import Any, Optional

SCRIPTS_DIR = Path(__file__).resolve().parent
DEFAULT_EXPORT_DIR = SCRIPTS_DIR / "export"

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("csv_import")


def _rpc(
    models: xmlrpc.client.ServerProxy,
    db: str,
    uid: int,
    password: str,
    model: str,
    method: str,
    args: list[Any],
    kwargs: Optional[dict[str, Any]] = None,
) -> Any:
    if kwargs:
        return models.execute_kw(db, uid, password, model, method, args, kwargs)
    return models.execute_kw(db, uid, password, model, method, args)


def _batch_create(
    models: xmlrpc.client.ServerProxy,
    db: str,
    uid: int,
    password: str,
    model: str,
    rows: list[dict[str, Any]],
    chunk: int = 40,
) -> list[int]:
    ids: list[int] = []
    for i in range(0, len(rows), chunk):
        part = rows[i : i + chunk]
        if not part:
            continue
        res = _rpc(models, db, uid, password, model, "create", [part])
        if isinstance(res, list):
            ids.extend(res)
        else:
            ids.append(res)
    return ids


def _to_bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    if v is None or v == "":
        return False
    s = str(v).strip().lower()
    if s in ("0", "false", "no", ""):
        return False
    return True


def _to_float(v: Any) -> float:
    if v is None or v == "":
        return 0.0
    return float(v)


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise FileNotFoundError(str(path))
    with path.open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _norm_date(s: Optional[str]) -> Any:
    if not s or not str(s).strip():
        return False
    return str(s).strip()[:10]


def _norm_datetime(s: Optional[str]) -> Any:
    if not s or not str(s).strip():
        return False
    dt = str(s).strip()
    if "T" in dt:
        dt = dt.replace("T", " ", 1).split("+")[0].split("Z")[0].strip()
    return dt[:19] if len(dt) >= 19 else dt


def main() -> int:
    p = argparse.ArgumentParser(description="Import CSV export vào Odoo (XML-RPC).")
    p.add_argument("--db", default=os.environ.get("ODOO_DB"), help="Tên database Odoo")
    p.add_argument("--url", default=os.environ.get("ODOO_URL", "http://localhost:8069"))
    p.add_argument("--login", default=os.environ.get("ODOO_LOGIN", "admin"))
    p.add_argument("--password", default=os.environ.get("ODOO_PASSWORD"))
    p.add_argument("--export-dir", type=Path, default=DEFAULT_EXPORT_DIR)
    args = p.parse_args()

    if not args.db:
        logger.error("Thiếu tên database: --db hoặc ODOO_DB")
        return 1
    if not args.password:
        logger.error("Thiếu mật khẩu: --password hoặc ODOO_PASSWORD")
        return 1

    export_dir = args.export_dir
    if not export_dir.is_dir():
        logger.error("Không thấy thư mục export: %s", export_dir)
        return 1

    common = xmlrpc.client.ServerProxy(f"{args.url.rstrip('/')}/xmlrpc/2/common", allow_none=True)
    models = xmlrpc.client.ServerProxy(f"{args.url.rstrip('/')}/xmlrpc/2/object", allow_none=True)

    uid = common.authenticate(args.db, args.login, args.password, {})
    if not uid:
        logger.error("Đăng nhập Odoo thất bại (db/login/password).")
        return 1
    logger.info("Đã xác thực Odoo uid=%s db=%s", uid, args.db)

    admin_ids = _rpc(
        models,
        args.db,
        uid,
        args.password,
        "res.users",
        "search",
        [[["login", "=", args.login]]],
        {"limit": 1},
    )
    default_instructor = admin_ids[0] if admin_ids else uid

    def f(name: str) -> Path:
        return export_dir / f"lms_{name}.csv"

    try:
        # --- Categories / levels / tags ---
        cat_rows_raw = _read_csv(f("course_category"))
        cat_rows = [{"id": int(r["id"]), **r} for r in cat_rows_raw]
        cat_vals = [
            {
                "name": (r["name"] or "")[:500],
                "sequence": int(r.get("sequence") or 10),
                "description": (r.get("description") or "")[:65535] or False,
            }
            for r in cat_rows_raw
        ]
        cat_ids = _batch_create(models, args.db, uid, args.password, "lms.course.category", cat_vals)
        map_cat = {old: new for old, new in zip([int(r["id"]) for r in cat_rows_raw], cat_ids)}

        lev_rows_raw = _read_csv(f("course_level"))
        lev_vals = [
            {
                "name": (r["name"] or "")[:500],
                "sequence": int(r.get("sequence") or 10),
                "description": (r.get("description") or "")[:65535] or False,
            }
            for r in lev_rows_raw
        ]
        lev_ids = _batch_create(models, args.db, uid, args.password, "lms.course.level", lev_vals)
        map_lev = {old: new for old, new in zip([int(r["id"]) for r in lev_rows_raw], lev_ids)}

        tag_rows_raw = _read_csv(f("course_tag"))
        tag_vals = [
            {"name": (r["name"] or "")[:120], "color": int(r.get("color") or 0)}
            for r in tag_rows_raw
        ]
        tag_ids = _batch_create(models, args.db, uid, args.password, "lms.course.tag", tag_vals)
        map_tag = {old: new for old, new in zip([int(r["id"]) for r in tag_rows_raw], tag_ids)}

        # --- Courses ---
        course_rows_raw = _read_csv(f("course"))
        course_vals = []
        for r in course_rows_raw:
            ins = r.get("instructor_id")
            try:
                ins_id = int(ins) if ins not in (None, "") else default_instructor
            except (TypeError, ValueError):
                ins_id = default_instructor
            # Kiểm tra user tồn tại (tránh lỗi RPC)
            if not _rpc(
                models,
                args.db,
                uid,
                args.password,
                "res.users",
                "search_count",
                [[["id", "=", ins_id]]],
            ):
                ins_id = default_instructor
            cid = int(r["category_id"])
            lid = int(r["level_id"])
            course_vals.append(
                {
                    "name": (r["name"] or "")[:500],
                    "description": (r.get("description") or "")[:65535] or "<p></p>",
                    "category_id": map_cat[cid],
                    "level_id": map_lev[lid],
                    "instructor_id": ins_id,
                    "duration_hours": _to_float(r.get("duration_hours")),
                    "state": (r.get("state") or "draft").strip(),
                    "is_active": _to_bool(r.get("is_active", True)),
                    "average_rating": _to_float(r.get("average_rating")),
                }
            )
        course_new_ids = _batch_create(models, args.db, uid, args.password, "lms.course", course_vals)
        map_course = {old: new for old, new in zip([int(r["id"]) for r in course_rows_raw], course_new_ids)}

        for row in _read_csv(f("course_tag_rel")):
            cid = map_course[int(row["course_id"])]
            tid = map_tag[int(row["tag_id"])]
            _rpc(
                models,
                args.db,
                uid,
                args.password,
                "lms.course",
                "write",
                [[cid], {"tag_ids": [(4, tid)]}],
            )

        for row in _read_csv(f("course_prerequisite_rel")):
            p0 = row.get("prerequisite_id") or row.get("prerequisite_course_id")
            if p0 is None or str(p0).strip() == "":
                continue
            cid = map_course[int(float(row["course_id"]))]
            pid = map_course[int(float(p0))]
            _rpc(
                models,
                args.db,
                uid,
                args.password,
                "lms.course",
                "write",
                [[cid], {"prerequisite_ids": [(4, pid)]}],
            )

        # --- Lessons ---
        lesson_rows_raw = _read_csv(f("lesson"))
        lesson_vals = []
        for r in lesson_rows_raw:
            lesson_vals.append(
                {
                    "name": (r["name"] or "")[:500],
                    "sequence": int(r.get("sequence") or 10),
                    "description": (r.get("description") or "")[:65535] or False,
                    "course_id": map_course[int(r["course_id"])],
                    "video_url": (r.get("video_url") or "")[:2048] or False,
                    "pdf_filename": (r.get("pdf_filename") or "")[:255] or False,
                    "duration_minutes": int(float(r.get("duration_minutes") or 0)),
                }
            )
        lesson_new_ids = _batch_create(models, args.db, uid, args.password, "lms.lesson", lesson_vals)
        map_lesson = {old: new for old, new in zip([int(r["id"]) for r in lesson_rows_raw], lesson_new_ids)}

        # --- Students (bỏ cột computed / placeholder / user_id lạ) ---
        st_rows_raw = _read_csv(f("student"))
        st_vals = []
        for r in st_rows_raw:
            em = ((r.get("email") or "").strip()[:255])
            if not em:
                em = f"student_import_{r['id']}@local.invalid"
            st_vals.append(
                {
                    "name": (r["name"] or "")[:200],
                    "email": em,
                    "phone": ((r.get("phone") or "").strip()[:64]) or False,
                    "current_level": (r.get("current_level") or "beginner").strip(),
                    "learning_goals": (r.get("learning_goals") or "")[:65535] or False,
                    "desired_skills": (r.get("desired_skills") or "")[:65535] or False,
                    "is_active": _to_bool(r.get("is_active", True)),
                }
            )
        st_new_ids = _batch_create(models, args.db, uid, args.password, "lms.student", st_vals)
        map_student = {old: new for old, new in zip([int(r["id"]) for r in st_rows_raw], st_new_ids)}

        # --- Student course (bỏ progress — Odoo tự tính) ---
        sc_rows_raw = _read_csv(f("student_course"))
        sc_vals = []
        for r in sc_rows_raw:
            fs = r.get("final_score")
            sc_vals.append(
                {
                    "student_id": map_student[int(r["student_id"])],
                    "course_id": map_course[int(r["course_id"])],
                    "enrollment_date": _norm_date(r.get("enrollment_date")),
                    "start_date": _norm_date(r.get("start_date")),
                    "completion_date": _norm_date(r.get("completion_date")),
                    "status": (r.get("status") or "enrolled").strip(),
                    "final_score": _to_float(fs) if fs not in (None, "") else False,
                }
            )
        sc_new_ids = _batch_create(models, args.db, uid, args.password, "lms.student.course", sc_vals)
        map_sc = {old: new for old, new in zip([int(r["id"]) for r in sc_rows_raw], sc_new_ids)}

        # --- Learning history (bỏ name, is_at_risk, course_id, instructor_id — related/compute) ---
        lh_rows_raw = _read_csv(f("learning_history"))
        lh_vals = []
        for r in lh_rows_raw:
            lh_vals.append(
                {
                    "student_id": map_student[int(r["student_id"])],
                    "student_course_id": map_sc[int(r["student_course_id"])],
                    "lesson_id": map_lesson[int(r["lesson_id"])],
                    "date": _norm_datetime(r.get("date")),
                    "study_duration": _to_float(r.get("study_duration")),
                    "status": (r.get("status") or "started").strip(),
                    "notes": (r.get("notes") or "")[:65535] or False,
                }
            )
        _batch_create(models, args.db, uid, args.password, "lms.learning.history", lh_vals)

        n_rm = 0
        n_rmc = 0
        rm_csv = f("roadmap")
        if rm_csv.is_file():
            rm_rows_raw = _read_csv(rm_csv)
            rm_vals = []
            for r in rm_rows_raw:
                rv = (r.get("reviewed_by") or "").strip()
                reviewed_by = False
                if rv.isdigit():
                    uid_r = int(rv)
                    if _rpc(
                        models,
                        args.db,
                        uid,
                        args.password,
                        "res.users",
                        "search_count",
                        [[["id", "=", uid_r]]],
                    ):
                        reviewed_by = uid_r
                rm_vals.append(
                    {
                        "student_id": map_student[int(r["student_id"])],
                        "valid_from": _norm_date(r.get("valid_from")),
                        "valid_to": _norm_date(r.get("valid_to")),
                        "state": (r.get("state") or "draft").strip(),
                        "reviewed_by": reviewed_by,
                        "ai_recommendation_reason": (r.get("ai_recommendation_reason") or "")[:65535]
                        or False,
                        "recommendation_method": (r.get("recommendation_method") or "hybrid").strip(),
                    }
                )
            rm_new_ids = _batch_create(
                models, args.db, uid, args.password, "lms.roadmap", rm_vals
            )
            map_roadmap = {
                old: new for old, new in zip([int(r["id"]) for r in rm_rows_raw], rm_new_ids)
            }
            n_rm = len(rm_new_ids)

            rmc_csv = f("roadmap_course")
            if rmc_csv.is_file():
                rmc_rows_raw = _read_csv(rmc_csv)
                rmc_vals = []
                for r in rmc_rows_raw:
                    ss = (r.get("similarity_score") or "").strip()
                    rmc_vals.append(
                        {
                            "roadmap_id": map_roadmap[int(r["roadmap_id"])],
                            "course_id": map_course[int(r["course_id"])],
                            "sequence": int(r.get("sequence") or 10),
                            "priority": (r.get("priority") or "medium").strip(),
                            "timeframe": (r.get("timeframe") or "medium").strip(),
                            "status": (r.get("status") or "pending").strip(),
                            "recommendation_reason": (r.get("recommendation_reason") or "")[:65535]
                            or False,
                            "similarity_score": _to_float(ss) if ss else 0.0,
                        }
                    )
                _batch_create(
                    models, args.db, uid, args.password, "lms.roadmap.course", rmc_vals
                )
                n_rmc = len(rmc_vals)

        logger.info(
            "Hoàn tất import CSV: categories=%s levels=%s tags=%s courses=%s lessons=%s "
            "students=%s enrollments=%s history=%s roadmaps=%s roadmap_lines=%s",
            len(cat_ids),
            len(lev_ids),
            len(tag_ids),
            len(course_new_ids),
            len(lesson_new_ids),
            len(st_new_ids),
            len(sc_new_ids),
            len(lh_rows_raw),
            n_rm,
            n_rmc,
        )
        logger.info(
            "Gợi ý: trên từng sinh viên bấm cập nhật thống kê nếu cần, hoặc Upgrade module lms."
        )
    except FileNotFoundError as e:
        logger.error("Thiếu file: %s", e)
        return 1
    except KeyError as e:
        logger.error("CSV thiếu cột hoặc id tham chiếu không khớp: %s", e)
        return 1

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        logger.exception("Import CSV thất bại.")
        sys.exit(1)
