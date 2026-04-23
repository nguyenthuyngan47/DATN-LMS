#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Đẩy dữ liệu từ lms_export_demo.db (sau crawl_lms_data.py) trực tiếp vào Odoo qua XML-RPC.

Yêu cầu: Odoo đang chạy, module lms đã cài, tài khoản có quyền tạo bản ghi LMS.

Biến môi trường (hoặc tham số CLI):
  ODOO_URL      mặc định http://localhost:8069
  ODOO_DB       tên database Odoo (bắt buộc)
  ODOO_LOGIN    mặc định admin
  ODOO_PASSWORD mật khẩu user Odoo

Chạy:
  python crawl_lms_data.py
  python odoo_import_crawl.py --db TEN_DATABASE
"""

from __future__ import annotations

import argparse
import logging
import os
import sqlite3
import sys
import xmlrpc.client
from pathlib import Path
from typing import Any, Optional

SCRIPTS_DIR = Path(__file__).resolve().parent
DB_SQLITE = SCRIPTS_DIR / "lms_export_demo.db"

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("odoo_import")


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
    """create([vals,...]) trả về list id."""
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
    if v is None:
        return False
    return bool(int(v))


def _to_float(v: Any) -> float:
    if v is None or v == "":
        return 0.0
    return float(v)


def _to_int(v: Any) -> int:
    if v is None or v == "":
        return 0
    return int(v)


def main() -> int:
    p = argparse.ArgumentParser(description="Import SQLite crawl vào Odoo (XML-RPC).")
    p.add_argument("--db", default=os.environ.get("ODOO_DB"), help="Tên database Odoo")
    p.add_argument("--url", default=os.environ.get("ODOO_URL", "http://localhost:8069"))
    p.add_argument("--login", default=os.environ.get("ODOO_LOGIN", "admin"))
    p.add_argument("--password", default=os.environ.get("ODOO_PASSWORD"))
    p.add_argument("--sqlite", type=Path, default=DB_SQLITE)
    args = p.parse_args()

    if not args.db:
        logger.error("Thiếu tên database: --db hoặc ODOO_DB")
        return 1
    if not args.password:
        logger.error("Thiếu mật khẩu: --password hoặc ODOO_PASSWORD")
        return 1
    if not args.sqlite.is_file():
        logger.error("Không tìm thấy file SQLite: %s (chạy crawl_lms_data.py trước)", args.sqlite)
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
    instructor_id = admin_ids[0] if admin_ids else uid

    conn = sqlite3.connect(args.sqlite)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    try:
        # --- Categories / levels / tags ---
        c.execute("SELECT id, name, sequence, description FROM lms_course_category ORDER BY id")
        cat_rows = [dict(r) for r in c.fetchall()]
        c.execute("SELECT id, name, sequence, description FROM lms_course_level ORDER BY id")
        lev_rows = [dict(r) for r in c.fetchall()]
        c.execute("SELECT id, name, color FROM lms_course_tag ORDER BY id")
        tag_rows = [dict(r) for r in c.fetchall()]

        cat_vals = [
            {"name": r["name"], "sequence": int(r["sequence"]), "description": r["description"] or False}
            for r in cat_rows
        ]
        cat_ids = _batch_create(models, args.db, uid, args.password, "lms.course.category", cat_vals)
        map_cat = {old["id"]: new for old, new in zip(cat_rows, cat_ids)}

        lev_vals = [
            {"name": r["name"], "sequence": int(r["sequence"]), "description": r["description"] or False}
            for r in lev_rows
        ]
        lev_ids = _batch_create(models, args.db, uid, args.password, "lms.course.level", lev_vals)
        map_lev = {old["id"]: new for old, new in zip(lev_rows, lev_ids)}

        tag_vals = [
            {"name": r["name"][:120], "color": int(r["color"] or 0)} for r in tag_rows
        ]
        tag_ids = _batch_create(models, args.db, uid, args.password, "lms.course.tag", tag_vals)
        map_tag = {old["id"]: new for old, new in zip(tag_rows, tag_ids)}

        # --- Courses ---
        c.execute(
            """SELECT id, name, description, category_id, level_id, instructor_id, duration_hours,
               state, is_active, average_rating FROM lms_course ORDER BY id"""
        )
        course_rows = [dict(r) for r in c.fetchall()]
        course_vals = []
        for r in course_rows:
            course_vals.append(
                {
                    "name": r["name"][:500],
                    "description": r["description"] or "<p></p>",
                    "category_id": map_cat[r["category_id"]],
                    "level_id": map_lev[r["level_id"]],
                    "instructor_id": instructor_id,
                    "duration_hours": _to_float(r["duration_hours"]),
                    "state": r["state"] or "draft",
                    "is_active": _to_bool(r["is_active"]),
                    "average_rating": _to_float(r["average_rating"]),
                }
            )
        course_new_ids = _batch_create(models, args.db, uid, args.password, "lms.course", course_vals)
        map_course = {old["id"]: new for old, new in zip(course_rows, course_new_ids)}

        # Tag rel + prerequisite (write từng khóa — ít nhất rõ ràng)
        c.execute("SELECT course_id, tag_id FROM lms_course_tag_rel ORDER BY id")
        for row in c.fetchall():
            cid = map_course[row[0]]
            tid = map_tag[row[1]]
            _rpc(
                models,
                args.db,
                uid,
                args.password,
                "lms.course",
                "write",
                [[cid], {"tag_ids": [(4, tid)]}],
            )

        c.execute("SELECT course_id, prerequisite_id FROM lms_course_prerequisite_rel WHERE prerequisite_id IS NOT NULL ORDER BY id")
        for row in c.fetchall():
            cid = map_course[row[0]]
            pid = map_course[row[1]]
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
        c.execute(
            """SELECT id, name, sequence, description, course_id, video_url,
               pdf_filename, duration_minutes FROM lms_lesson ORDER BY id"""
        )
        lesson_rows = [dict(r) for r in c.fetchall()]
        lesson_vals = []
        for r in lesson_rows:
            lesson_vals.append(
                {
                    "name": r["name"][:500],
                    "sequence": int(r["sequence"]),
                    "description": r["description"] or False,
                    "course_id": map_course[r["course_id"]],
                    "video_url": r["video_url"] or False,
                    "pdf_filename": r["pdf_filename"] or False,
                    "duration_minutes": int(r["duration_minutes"] or 0),
                }
            )
        lesson_new_ids = _batch_create(models, args.db, uid, args.password, "lms.lesson", lesson_vals)
        map_lesson = {old["id"]: new for old, new in zip(lesson_rows, lesson_new_ids)}

        # --- Students ---
        c.execute(
            """SELECT id, name, email, phone, current_level, learning_goals, desired_skills, is_active
               FROM lms_student ORDER BY id"""
        )
        st_rows = [dict(r) for r in c.fetchall()]
        st_vals = []
        for r in st_rows:
            st_vals.append(
                {
                    "name": r["name"][:200],
                    "email": (r["email"] or "").strip()[:255],
                    "phone": (r["phone"] or "")[:64] or False,
                    "current_level": r["current_level"] or "beginner",
                    "learning_goals": r["learning_goals"] or False,
                    "desired_skills": r["desired_skills"] or False,
                    "is_active": _to_bool(r["is_active"]),
                }
            )
        st_new_ids = _batch_create(models, args.db, uid, args.password, "lms.student", st_vals)
        map_student = {old["id"]: new for old, new in zip(st_rows, st_new_ids)}

        # --- Student course ---
        c.execute(
            """SELECT id, student_id, course_id, enrollment_date, start_date, completion_date,
               status, final_score FROM lms_student_course ORDER BY id"""
        )
        sc_rows = [dict(r) for r in c.fetchall()]
        sc_vals = []
        for r in sc_rows:
            sc_vals.append(
                {
                    "student_id": map_student[r["student_id"]],
                    "course_id": map_course[r["course_id"]],
                    "enrollment_date": r["enrollment_date"] or False,
                    "start_date": r["start_date"] or False,
                    "completion_date": r["completion_date"] or False,
                    "status": r["status"] or "enrolled",
                    "final_score": _to_float(r["final_score"]) if r["final_score"] is not None else False,
                }
            )
        sc_new_ids = _batch_create(models, args.db, uid, args.password, "lms.student.course", sc_vals)
        map_sc = {old["id"]: new for old, new in zip(sc_rows, sc_new_ids)}

        # --- Learning history ---
        c.execute(
            """SELECT id, student_id, student_course_id, lesson_id, date, study_duration, status, notes
               FROM lms_learning_history ORDER BY id"""
        )
        lh_rows = [dict(r) for r in c.fetchall()]
        lh_vals = []
        for r in lh_rows:
            dt = (r["date"] or "").strip()
            if "T" in dt:
                dt = dt.replace("T", " ", 1).split("+")[0].split("Z")[0].strip()
            dt = dt[:19] if len(dt) >= 19 else dt
            lh_vals.append(
                {
                    "student_id": map_student[r["student_id"]],
                    "student_course_id": map_sc[r["student_course_id"]],
                    "lesson_id": map_lesson[r["lesson_id"]],
                    "date": dt or False,
                    "study_duration": _to_float(r["study_duration"]),
                    "status": r["status"] or "started",
                    "notes": r["notes"] or False,
                }
            )
        _batch_create(models, args.db, uid, args.password, "lms.learning.history", lh_vals)

        logger.info("Hoàn tất import: categories=%s levels=%s tags=%s courses=%s lessons=%s students=%s enrollments=%s history=%s",
                    len(cat_ids), len(lev_ids), len(tag_ids), len(course_new_ids),
                    len(lesson_new_ids), len(st_new_ids), len(sc_new_ids), len(lh_rows))
    finally:
        conn.close()

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        logger.exception("Import thất bại.")
        sys.exit(1)
