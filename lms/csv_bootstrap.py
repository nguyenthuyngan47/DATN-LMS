# -*- coding: utf-8 -*-
"""Import LMS từ thư mục CSV trong tiến trình Odoo (cùng thứ tự/ánh xạ như scripts/import_csv_to_odoo.py)."""

from __future__ import annotations

import csv
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from odoo import SUPERUSER_ID, fields

_logger = logging.getLogger(__name__)


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
    with path.open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _norm_date(s: Any) -> Any:
    if not s or not str(s).strip():
        return False
    return str(s).strip()[:10]


def _norm_datetime(s: Any) -> Any:
    if not s or not str(s).strip():
        return False
    dt = str(s).strip()
    if "T" in dt:
        dt = dt.replace("T", " ", 1).split("+")[0].split("Z")[0].strip()
    return dt[:19] if len(dt) >= 19 else dt


def _normalize_learning_history_dates_apr_may(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Chuẩn hóa lịch học về tháng 4-5 để bền vững qua mỗi lần restart/import.
    Phân bổ xen kẽ theo ngày từ 2026-04-01 đến 2026-05-31.
    """
    if not rows:
        return rows
    base = datetime(2026, 4, 1, 8, 0, 0)
    span_days = 61  # 2026-04-01 .. 2026-05-31
    for idx, row in enumerate(rows):
        day_offset = idx % span_days
        hour_offset = (idx * 3) % 12
        minute_offset = (idx * 7) % 60
        dt = base + timedelta(days=day_offset, hours=hour_offset, minutes=minute_offset)
        row["date"] = dt.strftime("%Y-%m-%d %H:%M:%S")
    return rows


def _batch_create(Model, rows: list[dict[str, Any]], chunk: int = 40) -> list[int]:
    ids: list[int] = []
    sudo = Model.sudo()
    for i in range(0, len(rows), chunk):
        part = rows[i : i + chunk]
        if not part:
            continue
        recs = sudo.create(part)
        ids.extend(recs.ids)
    return ids


def _upsert_by_xmlid(
    env,
    model_name: str,
    xmlid_name: str,
    vals: dict[str, Any],
    fallback_domain: list[tuple[str, str, Any]] | None = None,
):
    """Upsert record được quản lý bởi CSV qua external id lms.<xmlid_name>."""
    imd = env["ir.model.data"].sudo()
    row = imd.search([("module", "=", "lms"), ("name", "=", xmlid_name)], limit=1)
    if row and row.model == model_name:
        rec = env[model_name].browse(row.res_id)
        if rec.exists():
            rec.sudo().write(vals)
            return rec
    rec = env.ref(f"lms.{xmlid_name}", raise_if_not_found=False)
    if rec and rec._name == model_name:
        rec.sudo().write(vals)
        return rec
    if fallback_domain:
        rec = env[model_name].sudo().search(fallback_domain, limit=1)
        if rec:
            rec.write(vals)
            existing = imd.search(
                [("module", "=", "lms"), ("name", "=", xmlid_name), ("model", "=", model_name)],
                limit=1,
            )
            if existing:
                existing.write({"res_id": rec.id})
            else:
                any_xmlid = imd.search(
                    [("module", "=", "lms"), ("name", "=", xmlid_name)], limit=1
                )
                if any_xmlid:
                    any_xmlid.write(
                        {"model": model_name, "res_id": rec.id, "noupdate": True}
                    )
                else:
                    imd.create(
                        {
                            "module": "lms",
                            "name": xmlid_name,
                            "model": model_name,
                            "res_id": rec.id,
                            "noupdate": True,
                        }
                    )
            return rec
    rec = env[model_name].sudo().create(vals)
    existing = imd.search(
        [("module", "=", "lms"), ("name", "=", xmlid_name), ("model", "=", model_name)],
        limit=1,
    )
    if existing:
        existing.write({"res_id": rec.id})
    else:
        any_xmlid = imd.search(
            [("module", "=", "lms"), ("name", "=", xmlid_name)], limit=1
        )
        if any_xmlid:
            any_xmlid.write(
                {"model": model_name, "res_id": rec.id, "noupdate": True}
            )
        else:
            imd.create(
                {
                    "module": "lms",
                    "name": xmlid_name,
                    "model": model_name,
                    "res_id": rec.id,
                    "noupdate": True,
                }
            )
    return rec


def _delete_missing_managed_records(
    env,
    model_name: str,
    prefix: str,
    source_ids: set[int],
    allow_delete: bool,
) -> None:
    if not allow_delete:
        return
    imd = env["ir.model.data"].sudo()
    managed = imd.search(
        [("module", "=", "lms"), ("model", "=", model_name), ("name", "=like", f"{prefix}%")]
    )
    for row in managed:
        suffix = row.name.replace(prefix, "", 1)
        try:
            src_id = int(suffix)
        except (TypeError, ValueError):
            continue
        if src_id in source_ids:
            continue
        rec = env[model_name].sudo().browse(row.res_id)
        if rec.exists():
            rec.unlink()
        row.unlink()


def _ensure_many_to_many_enrollments(env, per_course: int = 5) -> None:
    """
    Chuẩn hóa quan hệ học tập thành many-to-many bền vững sau mỗi lần import CSV:
    - Mỗi khóa học có `per_course` học viên
    - Mỗi học viên học nhiều khóa (phân bổ round-robin)
    - Learning history luôn trỏ đúng student_course_id theo (student, course)
    """
    students = env["lms.student"].sudo().search([], order="id")
    courses = env["lms.course"].sudo().search([], order="id")
    if not students or not courses or per_course <= 1:
        return

    n_students = len(students)
    offsets = list(range(per_course))
    target_pairs: set[tuple[int, int]] = set()
    for idx, course in enumerate(courses):
        for k in offsets:
            sid = students[(idx + k) % n_students].id
            target_pairs.add((sid, course.id))

    SC = env["lms.student.course"].sudo()
    existing = SC.search([])
    existing_pairs = {(r.student_id.id, r.course_id.id): r for r in existing}

    to_create: list[dict[str, Any]] = []
    for sid, cid in sorted(target_pairs, key=lambda x: (x[1], x[0])):
        if (sid, cid) not in existing_pairs:
            to_create.append(
                {
                    "student_id": sid,
                    "course_id": cid,
                    "enrollment_date": fields.Date.today(),
                    "status": "pending",
                }
            )
    if to_create:
        _batch_create(SC, to_create)

    # Xóa đăng ký không thuộc target để lần restart nào cũng ra cùng 1 dataset.
    drop_ids = [r.id for r in SC.search([]) if (r.student_id.id, r.course_id.id) not in target_pairs]
    if drop_ids:
        SC.browse(drop_ids).with_context(
            skip_lms_statistics_refresh=True, skip_lms_student_course_relink=True
        ).unlink()

    # Re-link history vào enrollment đúng theo (student, course của lesson)
    all_sc = SC.search([])
    sc_by_pair = {(r.student_id.id, r.course_id.id): r.id for r in all_sc}
    histories = env["lms.learning.history"].sudo().search([])
    for h in histories:
        cid = h.lesson_id.course_id.id or h.course_id.id
        sid = h.student_id.id
        target_sc = sc_by_pair.get((sid, cid))
        if target_sc and h.student_course_id.id != target_sc:
            h.write({"student_course_id": target_sc})


def _stabilize_student_levels(env) -> None:
    """
    Giữ phân bố level sinh viên ổn định sau import/restart.
    Mục tiêu mặc định theo dataset hiện tại: 150 beginner, 35 intermediate, 15 advanced.
    """
    students = env["lms.student"].sudo().search([], order="id")
    if not students:
        return

    # Phân nhóm theo thứ tự ổn định để lần import nào cũng ra cùng kết quả.
    # Đồng thời khóa level nghiệp vụ để không bị ghi đè theo average_score.
    for idx, st in enumerate(students, start=1):
        if idx <= 150:
            level = "beginner"
            status_cycle = ("pending", "learning")
            score_cycle = (False, False)
        elif idx <= 185:
            level = "intermediate"
            status_cycle = ("completed", "learning")
            score_cycle = (6.6, False)
        else:
            level = "advanced"
            status_cycle = ("completed", "learning")
            score_cycle = (8.6, False)

        if st.current_level != level or not st.manual_level_lock:
            st.write({"current_level": level, "manual_level_lock": True})

        enrollments = st.enrolled_courses_ids.sudo().sorted(lambda r: r.id)
        for j, sc in enumerate(enrollments):
            sc.write(
                {
                    "status": status_cycle[j % len(status_cycle)],
                    "final_score": score_cycle[j % len(score_cycle)],
                }
            )

    # Chạy lại compute chuẩn hệ thống để đảm bảo UI và DB store đồng bộ.
    students.action_refresh_statistics()


def import_roadmaps_from_csv_directory(
    env,
    base: Path,
    map_student: dict[int, int],
    map_course: dict[int, int],
    *,
    safe_upsert: bool,
    delete_missing_managed: bool,
) -> tuple[int, int]:
    """
    Đọc ``lms_roadmap.csv`` và ``lms_roadmap_course.csv`` nếu có.
    Ánh xạ ``student_id`` / ``course_id`` theo ``map_student`` / ``map_course`` (ID CSV → Odoo).
    """
    rm_path = base / "lms_roadmap.csv"
    rmc_path = base / "lms_roadmap_course.csv"
    if not rm_path.is_file() or "lms.roadmap" not in env.registry:
        return 0, 0

    rm_rows = _read_csv(rm_path)
    if not rm_rows:
        return 0, 0

    Users = env["res.users"].sudo()
    source_rm_ids: set[int] = set()
    map_roadmap: dict[int, int] = {}
    for r in rm_rows:
        old_id = int(r["id"])
        source_rm_ids.add(old_id)
        sid_csv = int(r["student_id"])
        rv = (r.get("reviewed_by") or "").strip()
        reviewed_by = False
        if rv.isdigit():
            uid = int(rv)
            if Users.browse(uid).exists():
                reviewed_by = uid
        vals = {
            "student_id": map_student[sid_csv],
            "valid_from": _norm_date(r.get("valid_from")),
            "valid_to": _norm_date(r.get("valid_to")),
            "state": (r.get("state") or "draft").strip(),
            "reviewed_by": reviewed_by,
            "ai_recommendation_reason": (r.get("ai_recommendation_reason") or "")[:65535] or False,
            "recommendation_method": (r.get("recommendation_method") or "hybrid").strip(),
        }
        if safe_upsert:
            rec = _upsert_by_xmlid(
                env,
                "lms.roadmap",
                f"csv_roadmap_{old_id}",
                vals,
                fallback_domain=[("student_id", "=", vals["student_id"])],
            )
        else:
            rec = env["lms.roadmap"].sudo().create(vals)
        map_roadmap[old_id] = rec.id
    _delete_missing_managed_records(
        env, "lms.roadmap", "csv_roadmap_", source_rm_ids, delete_missing_managed
    )

    n_lines = 0
    if rmc_path.is_file():
        rmc_rows = _read_csv(rmc_path)
        source_rmc_ids: set[int] = set()
        for r in rmc_rows:
            old_id = int(r["id"])
            source_rmc_ids.add(old_id)
            ss = (r.get("similarity_score") or "").strip()
            vals = {
                "roadmap_id": map_roadmap[int(r["roadmap_id"])],
                "course_id": map_course[int(r["course_id"])],
                "sequence": int(r.get("sequence") or 10),
                "priority": (r.get("priority") or "medium").strip(),
                "timeframe": (r.get("timeframe") or "medium").strip(),
                "status": (r.get("status") or "pending").strip(),
                "recommendation_reason": (r.get("recommendation_reason") or "")[:65535] or False,
                "similarity_score": _to_float(ss) if ss else 0.0,
            }
            if safe_upsert:
                _upsert_by_xmlid(
                    env,
                    "lms.roadmap.course",
                    f"csv_roadmap_course_{old_id}",
                    vals,
                    fallback_domain=[
                        ("roadmap_id", "=", vals["roadmap_id"]),
                        ("course_id", "=", vals["course_id"]),
                    ],
                )
            else:
                env["lms.roadmap.course"].sudo().create(vals)
            n_lines += 1
        _delete_missing_managed_records(
            env,
            "lms.roadmap.course",
            "csv_roadmap_course_",
            source_rmc_ids,
            delete_missing_managed,
        )

    return len(rm_rows), n_lines


def import_lms_from_csv_directory(
    env,
    csv_dir: str | os.PathLike[str],
    *,
    safe_upsert: bool = True,
    delete_missing_managed: bool = False,
) -> None:
    """
    Đọc ``lms_*.csv`` trong ``csv_dir`` và tạo bản ghi mới (ID CSV → ID Odoo mới).
    Cần đủ: course_category, course_level, course_tag, course, course_tag_rel,
    course_prerequisite_rel, lesson, student, student_course, learning_history.
    Tuỳ chọn: roadmap, roadmap_course (nếu có ``lms_roadmap.csv`` / ``lms_roadmap_course.csv``).
    """
    base = Path(csv_dir)
    if not base.is_dir():
        raise FileNotFoundError(str(base))

    def f(name: str) -> Path:
        return base / f"lms_{name}.csv"

    Users = env["res.users"]
    ref_admin = env.ref("base.user_admin", raise_if_not_found=False)
    default_instructor = ref_admin.id if ref_admin else SUPERUSER_ID

    cat_rows_raw = _read_csv(f("course_category"))[:10]
    map_cat: dict[int, int] = {}
    source_cat_ids: set[int] = set()
    for r in cat_rows_raw:
        old_id = int(r["id"])
        source_cat_ids.add(old_id)
        vals = {
            "name": (r["name"] or "")[:500],
            "sequence": int(r.get("sequence") or 10),
            "description": (r.get("description") or "")[:65535] or False,
        }
        if safe_upsert:
            rec = _upsert_by_xmlid(
                env,
                "lms.course.category",
                f"csv_course_category_{old_id}",
                vals,
                fallback_domain=[("name", "=", vals["name"])],
            )
        else:
            rec = env["lms.course.category"].sudo().create(vals)
        map_cat[old_id] = rec.id
    _delete_missing_managed_records(
        env, "lms.course.category", "csv_course_category_", source_cat_ids, delete_missing_managed
    )

    lev_rows_raw = _read_csv(f("course_level"))[:4]
    map_lev: dict[int, int] = {}
    source_lev_ids: set[int] = set()
    for r in lev_rows_raw:
        old_id = int(r["id"])
        source_lev_ids.add(old_id)
        vals = {
            "name": (r["name"] or "")[:500],
            "sequence": int(r.get("sequence") or 10),
            "description": (r.get("description") or "")[:65535] or False,
        }
        if safe_upsert:
            rec = _upsert_by_xmlid(
                env,
                "lms.course.level",
                f"csv_course_level_{old_id}",
                vals,
                fallback_domain=[("name", "=", vals["name"])],
            )
        else:
            rec = env["lms.course.level"].sudo().create(vals)
        map_lev[old_id] = rec.id
    _delete_missing_managed_records(
        env, "lms.course.level", "csv_course_level_", source_lev_ids, delete_missing_managed
    )

    tag_rows_raw = _read_csv(f("course_tag"))
    map_tag: dict[int, int] = {}
    source_tag_ids: set[int] = set()
    for r in tag_rows_raw:
        old_id = int(r["id"])
        source_tag_ids.add(old_id)
        vals = {"name": (r["name"] or "")[:120], "color": int(r.get("color") or 0)}
        if safe_upsert:
            rec = _upsert_by_xmlid(
                env,
                "lms.course.tag",
                f"csv_course_tag_{old_id}",
                vals,
                fallback_domain=[("name", "=", vals["name"])],
            )
        else:
            rec = env["lms.course.tag"].sudo().create(vals)
        map_tag[old_id] = rec.id
    _delete_missing_managed_records(
        env, "lms.course.tag", "csv_course_tag_", source_tag_ids, delete_missing_managed
    )

    course_rows_raw = _read_csv(f("course"))
    category_pool = list(map_cat.values())
    level_pool = list(map_lev.values())
    if not category_pool:
        raise ValueError("CSV import: không có danh mục khóa học để ánh xạ.")
    if not level_pool:
        raise ValueError("CSV import: không có cấp độ khóa học để ánh xạ.")
    course_new_ids = []
    Course = env["lms.course"]
    for idx, r in enumerate(course_rows_raw):
        old_id = int(r["id"])
        ins = r.get("instructor_id")
        try:
            ins_id = int(ins) if ins not in (None, "") else default_instructor
        except (TypeError, ValueError):
            ins_id = default_instructor
        if not Users.browse(ins_id).exists():
            ins_id = default_instructor
        # Chuẩn hóa theo yêu cầu nghiệp vụ:
        # - Chỉ giữ 10 danh mục
        # - Phân bổ khóa học đều vào 10 danh mục (round-robin theo thứ tự CSV)
        target_cat_id = category_pool[idx % len(category_pool)]
        # Chuẩn hóa cấp độ khóa học theo nhóm mức rõ ràng, cân bằng theo round-robin.
        target_level_id = level_pool[idx % len(level_pool)]
        vals = {
            "name": (r["name"] or "")[:500],
            "description": (r.get("description") or "")[:65535] or "<p></p>",
            "category_id": target_cat_id,
            "level_id": target_level_id,
            "instructor_id": ins_id,
            "duration_hours": _to_float(r.get("duration_hours")),
            "state": (r.get("state") or "draft").strip(),
            "is_active": _to_bool(r.get("is_active", True)),
            "average_rating": _to_float(r.get("average_rating")),
        }
        if safe_upsert:
            rec = _upsert_by_xmlid(
                env,
                "lms.course",
                f"csv_course_{old_id}",
                vals,
                fallback_domain=[("name", "=", vals["name"])],
            )
        else:
            rec = Course.sudo().create(vals)
        course_new_ids.append(rec.id)
    map_course = {old: new for old, new in zip([int(r["id"]) for r in course_rows_raw], course_new_ids)}

    for row in _read_csv(f("course_tag_rel")):
        cid = map_course[int(row["course_id"])]
        tid = map_tag[int(row["tag_id"])]
        Course.browse(cid).sudo().write({"tag_ids": [(4, tid)]})

    for row in _read_csv(f("course_prerequisite_rel")):
        p0 = row.get("prerequisite_id") or row.get("prerequisite_course_id")
        if p0 is None or str(p0).strip() == "":
            continue
        cid = map_course[int(float(row["course_id"]))]
        pid = map_course[int(float(p0))]
        Course.browse(cid).sudo().write({"prerequisite_ids": [(4, pid)]})

    lesson_rows_raw = _read_csv(f("lesson"))
    lesson_new_ids = []
    for r in lesson_rows_raw:
        old_id = int(r["id"])
        vals = {
            "name": (r["name"] or "")[:500],
            "sequence": int(r.get("sequence") or 10),
            "description": (r.get("description") or "")[:65535] or False,
            "course_id": map_course[int(r["course_id"])],
            "pdf_filename": (r.get("pdf_filename") or "")[:255] or False,
            "duration_minutes": int(float(r.get("duration_minutes") or 0)),
        }
        if safe_upsert:
            rec = _upsert_by_xmlid(
                env,
                "lms.lesson",
                f"csv_lesson_{old_id}",
                vals,
                fallback_domain=[("name", "=", vals["name"]), ("course_id", "=", vals["course_id"])],
            )
        else:
            rec = env["lms.lesson"].sudo().create(vals)
        lesson_new_ids.append(rec.id)
    map_lesson = {
        old: new for old, new in zip([int(r["id"]) for r in lesson_rows_raw], lesson_new_ids)
    }

    st_rows_raw = _read_csv(f("student"))
    st_new_ids = []
    for r in st_rows_raw:
        old_id = int(r["id"])
        em = (r.get("email") or "").strip()[:255]
        if not em:
            em = f"student_import_{r['id']}@local.invalid"
        vals = {
            "name": (r["name"] or "")[:200],
            "email": em,
            "phone": ((r.get("phone") or "").strip()[:64]) or False,
            "current_level": (r.get("current_level") or "beginner").strip(),
            "learning_goals": (r.get("learning_goals") or "")[:65535] or False,
            "desired_skills": (r.get("desired_skills") or "")[:65535] or False,
            "is_active": _to_bool(r.get("is_active", True)),
        }
        if safe_upsert:
            rec = _upsert_by_xmlid(
                env,
                "lms.student",
                f"csv_student_{old_id}",
                vals,
                fallback_domain=[("email", "=ilike", vals["email"])],
            )
        else:
            rec = env["lms.student"].sudo().create(vals)
        st_new_ids.append(rec.id)
    map_student = {
        old: new for old, new in zip([int(r["id"]) for r in st_rows_raw], st_new_ids)
    }

    sc_rows_raw = _read_csv(f("student_course"))
    sc_new_ids = []
    for r in sc_rows_raw:
        old_id = int(r["id"])
        fs = r.get("final_score")
        vals = {
            "student_id": map_student[int(r["student_id"])],
            "course_id": map_course[int(r["course_id"])],
            "enrollment_date": _norm_date(r.get("enrollment_date")),
            "start_date": _norm_date(r.get("start_date")),
            "completion_date": _norm_date(r.get("completion_date")),
            "status": (r.get("status") or "pending").strip(),
            "final_score": _to_float(fs) if fs not in (None, "") else False,
        }
        if safe_upsert:
            rec = _upsert_by_xmlid(
                env,
                "lms.student.course",
                f"csv_student_course_{old_id}",
                vals,
                fallback_domain=[("student_id", "=", vals["student_id"]), ("course_id", "=", vals["course_id"])],
            )
        else:
            rec = env["lms.student.course"].sudo().create(vals)
        sc_new_ids.append(rec.id)
    map_sc = {old: new for old, new in zip([int(r["id"]) for r in sc_rows_raw], sc_new_ids)}

    lh_rows_raw = _read_csv(f("learning_history"))
    lh_entries: list[tuple[int, dict[str, Any]]] = []
    for r in lh_rows_raw:
        old_id = int(r["id"])
        lh_entries.append(
            (
                old_id,
                {
                    "student_id": map_student[int(r["student_id"])],
                    "student_course_id": map_sc[int(r["student_course_id"])],
                    "lesson_id": map_lesson[int(r["lesson_id"])],
                    "date": _norm_datetime(r.get("date")),
                    "study_duration": _to_float(r.get("study_duration")),
                    "status": (r.get("status") or "started").strip(),
                    "notes": (r.get("notes") or "")[:65535] or False,
                },
            )
        )
    normalized = _normalize_learning_history_dates_apr_may([vals for _, vals in lh_entries])
    for (old_id, _), vals in zip(lh_entries, normalized):
        if safe_upsert:
            _upsert_by_xmlid(
                env,
                "lms.learning.history",
                f"csv_learning_history_{old_id}",
                vals,
                fallback_domain=[
                    ("student_course_id", "=", vals["student_course_id"]),
                    ("lesson_id", "=", vals["lesson_id"]),
                    ("date", "=", vals["date"]),
                ],
            )
        else:
            env["lms.learning.history"].sudo().create(vals)
    _ensure_many_to_many_enrollments(env, per_course=5)
    _stabilize_student_levels(env)

    n_roadmaps, n_roadmap_lines = import_roadmaps_from_csv_directory(
        env,
        base,
        map_student,
        map_course,
        safe_upsert=safe_upsert,
        delete_missing_managed=delete_missing_managed,
    )

    n_lecturers = import_lecturers_from_csv_directory(env, base)

    _logger.info(
        "LMS CSV bootstrap: categories=%s levels=%s tags=%s courses=%s lessons=%s "
        "students=%s enrollments=%s history=%s roadmaps=%s roadmap_lines=%s lecturers=%s",
        len(map_cat),
        len(map_lev),
        len(map_tag),
        len(course_new_ids),
        len(lesson_new_ids),
        len(st_new_ids),
        len(sc_new_ids),
        len(lh_rows_raw),
        n_roadmaps,
        n_roadmap_lines,
        n_lecturers,
    )


def get_csv_import_dir() -> str:
    return os.environ.get("LMS_CSV_IMPORT_DIR", "/mnt/extra-addons/data/export")


def _norm_selection(val: Any, allowed: set[str]) -> Any:
    s = (str(val).strip().lower() if val not in (None, "") else "")
    return s if s in allowed else False


def import_lecturers_from_csv_directory(env, base: Path) -> int:
    """
    Đọc ``lms_lecturer.csv`` (nếu có): tạo/cập nhật ``res.users`` + ``lms.lecturer``,
    sau đó gán ``lms.course.instructor_id`` luân phiên cho khớp dataset hiện có.

    Cột CSV (UTF-8-SIG): id, login, password, full_name, email, phone, gender,
    date_of_birth, address, department, specialization, academic_degree,
    years_of_experience, faculty, subject_expertise, certifications,
    teaching_level, teaching_type, active
    """
    path = base / "lms_lecturer.csv"
    if not path.is_file() or "lms.lecturer" not in env.registry:
        return 0

    rows = _read_csv(path)
    if not rows:
        return 0

    group_user = env.ref("base.group_user", raise_if_not_found=False)
    group_inst = env.ref("lms.group_lms_instructor", raise_if_not_found=False)
    group_ids: list[int] = []
    if group_user:
        group_ids.append(group_user.id)
    if group_inst:
        group_ids.append(group_inst.id)

    Users = env["res.users"].sudo()
    Lecturer = env["lms.lecturer"].sudo()
    user_ids_order: list[int] = []

    genders = {"male", "female", "other"}
    levels = {"beginner", "intermediate", "advanced", "expert"}
    types_ = {"online", "offline", "hybrid"}

    for r in rows:
        login = (r.get("login") or "").strip()
        if not login:
            continue
        full_name = ((r.get("full_name") or login) or "")[:200].strip()
        email = ((r.get("email") or f"{login}@lms.training.local") or "").strip()[:255]
        user = Users.search([("login", "=", login)], limit=1)
        active = _to_bool(r.get("active", True))
        vals_user: dict[str, Any] = {
            "name": full_name,
            "login": login,
            "email": email,
            "active": active,
            "share": False,
        }
        if group_ids:
            vals_user["groups_id"] = [(6, 0, group_ids)]
        if user:
            user.write({"name": full_name, "email": email, "active": active})
            if group_ids:
                user.write({"groups_id": [(6, 0, group_ids)]})
        else:
            user = Users.create(vals_user)

        password = (r.get("password") or "").strip()
        if password:
            user.sudo().write({"password": password})

        partner = user.partner_id.sudo()
        phone = ((r.get("phone") or "").strip()[:64]) or False
        partner.write({"email": email or False, "phone": phone})

        gender = _norm_selection(r.get("gender"), genders)
        teaching_level = _norm_selection(r.get("teaching_level"), levels)
        teaching_type = _norm_selection(r.get("teaching_type"), types_)
        if teaching_type is False:
            teaching_type = "hybrid"

        lect_vals: dict[str, Any] = {
            "full_name": full_name,
            "gender": gender or False,
            "date_of_birth": _norm_date(r.get("date_of_birth")),
            "avatar_url": ((r.get("avatar_url") or "").strip()[:2048]) or False,
            "address": ((r.get("address") or "").strip()[:500]) or False,
            "department": ((r.get("department") or "").strip()[:255]) or False,
            "specialization": ((r.get("specialization") or "").strip()[:255]) or False,
            "academic_degree": ((r.get("academic_degree") or "").strip()[:255]) or False,
            "years_of_experience": int(float(r.get("years_of_experience") or 0)),
            "faculty": ((r.get("faculty") or "").strip()[:255]) or False,
            "subject_expertise": ((r.get("subject_expertise") or "").strip()[:65535]) or False,
            "certifications": ((r.get("certifications") or "").strip()[:65535]) or False,
            "teaching_level": teaching_level or False,
            "teaching_type": teaching_type,
        }

        existing = Lecturer.search([("user_id", "=", user.id)], limit=1)
        if existing:
            existing.write(lect_vals)
        else:
            Lecturer.create({"user_id": user.id, **lect_vals})

        user_ids_order.append(user.id)

    courses = env["lms.course"].sudo().search([], order="id")
    if user_ids_order and courses:
        n = len(user_ids_order)
        for i, course in enumerate(courses):
            course.write({"instructor_id": user_ids_order[i % n]})

    return len(user_ids_order)
