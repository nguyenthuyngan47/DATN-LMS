#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Cải thiện dữ liệu LMS trên Odoo (XML-RPC), không cần sửa code module:

1) Ghi danh sinh viên vào khóa học đúng cấp độ (Beginner/Intermediate/Advanced),
   điểm cuối khóa (final_score) ngẫu nhiên theo thang 10.
2) Roadmap: mỗi sinh viên 1 roadmap (tạo nếu chưa có) + 3–5 dòng lms.roadmap.course.
3) Thời khóa biểu (lms.learning.history): buổi học tháng 4–5/2026, 2–3 buổi/tuần/khóa,
   khung giờ 8–12 / 13–17 / 18–21, tránh trùng giờ khi một SV học nhiều khóa.

Chạy (sau khi Odoo + module lms đã có dữ liệu khóa học & sinh viên):

  set ODOO_DB=...
  set ODOO_PASSWORD=...
  python improve_lms_data_odoo.py

Tùy chọn: --dry-run (chỉ in kế hoạch), --seed 42
"""

from __future__ import annotations

import argparse
import logging
import os
import random
import sys
import xmlrpc.client
from calendar import monthrange
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any, Optional

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("improve_lms")


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


def _chunked(ids: list[int], n: int) -> list[list[int]]:
    return [ids[i : i + n] for i in range(0, len(ids), n)]


def _cat_id_field(pc: dict[str, Any]) -> int:
    cid = pc.get("category_id")
    if isinstance(cid, (list, tuple)) and cid:
        return int(cid[0])
    return int(cid or 0)


def _score_range(level_key: str) -> tuple[float, float]:
    if level_key == "beginner":
        return (5.0, 7.5)
    if level_key == "intermediate":
        return (6.0, 8.5)
    return (7.0, 9.5)


def _unlink_all(models, db, uid, password: str, model: str) -> int:
    ids = _rpc(models, db, uid, password, model, "search", [[]])
    total = 0
    for part in _chunked(ids, 400):
        _rpc(models, db, uid, password, model, "unlink", [part])
        total += len(part)
    return total


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.environ.get("ODOO_DB"))
    ap.add_argument("--url", default=os.environ.get("ODOO_URL", "http://localhost:8069"))
    ap.add_argument("--login", default=os.environ.get("ODOO_LOGIN", "admin"))
    ap.add_argument("--password", default=os.environ.get("ODOO_PASSWORD"))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--seed", type=int, default=None)
    args = ap.parse_args()

    if not args.db or not args.password:
        logger.error("Cần --db và --password (hoặc ODOO_DB, ODOO_PASSWORD).")
        return 1

    if args.seed is not None:
        random.seed(args.seed)

    common = xmlrpc.client.ServerProxy(f"{args.url.rstrip('/')}/xmlrpc/2/common", allow_none=True)
    models = xmlrpc.client.ServerProxy(f"{args.url.rstrip('/')}/xmlrpc/2/object", allow_none=True)
    uid = common.authenticate(args.db, args.login, args.password, {})
    if not uid:
        logger.error("Đăng nhập Odoo thất bại.")
        return 1

    db, pwd = args.db, args.password

    # --- Level id theo tên seed XML ---
    levels = _rpc(
        models,
        db,
        uid,
        pwd,
        "lms.course.level",
        "search_read",
        [[], ["id", "name", "sequence"]],
        {"order": "sequence"},
    )
    level_by_key: dict[str, int] = {}
    for lv in levels:
        n = (lv.get("name") or "").lower()
        if "beginner" in n or n == "beginner":
            level_by_key.setdefault("beginner", lv["id"])
        elif "intermediate" in n:
            level_by_key.setdefault("intermediate", lv["id"])
        elif "advanced" in n:
            level_by_key.setdefault("advanced", lv["id"])

    if len(level_by_key) < 3:
        logger.error("Cần đủ 3 lms.course.level (Beginner/Intermediate/Advanced). Có: %s", level_by_key)
        return 1

    # --- Tất cả khóa theo level ---
    courses_by_level: dict[int, list[dict[str, Any]]] = defaultdict(list)
    all_courses = _rpc(
        models,
        db,
        uid,
        pwd,
        "lms.course",
        "search_read",
        [[], ["id", "name", "level_id", "category_id"]],
        {"order": "id"},
    )
    for c in all_courses:
        lid = c.get("level_id")
        if isinstance(lid, (list, tuple)):
            lid = lid[0]
        if lid:
            courses_by_level[int(lid)].append(c)

    students = _rpc(
        models,
        db,
        uid,
        pwd,
        "lms.student",
        "search_read",
        [[], ["id", "name", "current_level"]],
        {"order": "id"},
    )
    if not students:
        logger.error("Không có sinh viên.")
        return 1

    logger.info(
        "Tìm thấy: %s SV, %s khóa học, levels=%s",
        len(students),
        len(all_courses),
        level_by_key,
    )

    if args.dry_run:
        logger.info("Dry-run: không ghi DB.")
        return 0

    # --- Xóa dữ liệu cũ (lịch học, đăng ký, dòng roadmap) ---
    n_h = _unlink_all(models, db, uid, pwd, "lms.learning.history")
    n_sc = _unlink_all(models, db, uid, pwd, "lms.student.course")
    n_rc = _unlink_all(models, db, uid, pwd, "lms.roadmap.course")
    logger.info("Đã xóa: learning.history=%s, student.course=%s, roadmap.course=%s", n_h, n_sc, n_rc)

    # --- Ghi danh + điểm ---
    student_course_ids: dict[tuple[int, int], int] = {}  # (student_id, course_id) -> sc_id
    enroll_rows: list[dict[str, Any]] = []
    roadmap_plan: list[tuple[int, list[int]]] = []  # student_id, course_ids ordered

    for st in students:
        sid = st["id"]
        lv_key = (st.get("current_level") or "beginner").lower()
        if lv_key not in ("beginner", "intermediate", "advanced"):
            lv_key = "beginner"
        target_level_id = level_by_key[lv_key]
        pool = list(courses_by_level.get(target_level_id, []))
        if len(pool) < 3:
            # bổ sung khóa cấp gần (sequence)
            fallback = [c for c in all_courses if c["id"] not in {x["id"] for x in pool}]
            pool.extend(fallback[: max(0, 8 - len(pool))])

        if not pool:
            roadmap_plan.append((sid, []))
            continue
        n_pick = min(5, max(3, len(pool)), len(pool))
        picked = random.sample(pool, n_pick)
        picked_sorted = sorted(picked, key=lambda c: (_cat_id_field(c), c["id"]))
        roadmap_plan.append((sid, [p["id"] for p in picked_sorted][:5]))

        for c in picked:
            lo, hi = _score_range(lv_key)
            fs = round(random.uniform(lo, hi), 2)
            st_status = random.choices(
                ["completed", "in_progress", "completed"],
                weights=[0.6, 0.35, 0.05],
            )[0]
            enroll_rows.append(
                {
                    "student_id": sid,
                    "course_id": c["id"],
                    "enrollment_date": "2026-03-01",
                    "start_date": "2026-03-05",
                    "completion_date": "2026-05-30" if st_status == "completed" else False,
                    "status": st_status,
                    "final_score": fs,
                }
            )

    # create student.course batch
    sc_ids_list = []
    for i in range(0, len(enroll_rows), 50):
        chunk = enroll_rows[i : i + 50]
        res = _rpc(models, db, uid, pwd, "lms.student.course", "create", [chunk])
        if isinstance(res, list):
            sc_ids_list.extend(res)
        else:
            sc_ids_list.append(res)

    # map (student, course) -> sc_id
    sc_read = _rpc(
        models,
        db,
        uid,
        pwd,
        "lms.student.course",
        "search_read",
        [[], ["id", "student_id", "course_id"]],
    )
    for r in sc_read:
        su = r["student_id"][0] if isinstance(r["student_id"], (list, tuple)) else r["student_id"]
        cu = r["course_id"][0] if isinstance(r["course_id"], (list, tuple)) else r["course_id"]
        student_course_ids[(su, cu)] = r["id"]

    # --- Roadmap ---
    roadmaps = _rpc(
        models,
        db,
        uid,
        pwd,
        "lms.roadmap",
        "search_read",
        [[], ["id", "student_id"]],
    )
    rm_by_student = {}
    for r in roadmaps:
        su = r["student_id"][0] if isinstance(r["student_id"], (list, tuple)) else r["student_id"]
        rm_by_student.setdefault(su, r["id"])

    roadmap_lines: list[dict[str, Any]] = []
    for sid, cids in roadmap_plan:
        if len(cids) < 1:
            continue
        rid = rm_by_student.get(sid)
        if not rid:
            rid = _rpc(
                models,
                db,
                uid,
                pwd,
                "lms.roadmap",
                "create",
                [
                    {
                        "student_id": sid,
                        "state": "suggested",
                        "valid_from": "2026-04-01",
                        "valid_to": "2026-12-31",
                        "recommendation_method": "rule_based",
                        "ai_recommendation_reason": "Lộ trình gợi ý theo cấp độ & danh mục (script improve_lms_data_odoo).",
                    }
                ],
            )
            if isinstance(rid, list):
                rid = rid[0]
        take = min(5, max(3, len(cids))) if len(cids) >= 3 else len(cids)
        path = cids[:take]
        tf_cycle = ["short", "medium", "long"] * 2
        pr_cycle = ["high", "medium", "low"] * 2
        for seq_i, cid in enumerate(path):
            roadmap_lines.append(
                {
                    "roadmap_id": rid,
                    "course_id": cid,
                    "sequence": (seq_i + 1) * 10,
                    "priority": pr_cycle[seq_i % len(pr_cycle)],
                    "timeframe": tf_cycle[seq_i % len(tf_cycle)],
                    "status": "pending",
                    "recommendation_reason": f"Bước {seq_i + 1} trên lộ trình (script).",
                    "similarity_score": round(random.uniform(0.5, 0.95), 2),
                }
            )

    for i in range(0, len(roadmap_lines), 40):
        _rpc(models, db, uid, pwd, "lms.roadmap.course", "create", [roadmap_lines[i : i + 40]])

    logger.info("Đã tạo %s đăng ký khóa, %s dòng roadmap.course.", len(enroll_rows), len(roadmap_lines))

    # --- Lịch học Apr–May 2026 ---
    lessons_by_course: dict[int, list[int]] = {}
    for c in all_courses:
        cid = c["id"]
        lids = _rpc(
            models,
            db,
            uid,
            pwd,
            "lms.lesson",
            "search",
            [[["course_id", "=", cid]]],
            {"order": "sequence,id"},
        )
        lessons_by_course[cid] = lids

    # Slot: (hour_start, duration_hours) trong bucket
    buckets = [
        ("morning", 8, 12),
        ("afternoon", 13, 17),
        ("evening", 18, 21),
    ]

    student_busy: dict[int, set[tuple[str, int]]] = defaultdict(set)

    def pick_slot(student_id: int, day: date) -> tuple[int, float]:
        """Trả về (hour_start int, duration 2-3h)."""
        busy = student_busy[student_id]
        for _ in range(12):
            bucket = random.choice(buckets)
            _, h0, h1 = bucket
            dur = random.choice([2.0, 2.5, 3.0])
            last_start = int(h1 - dur)
            if last_start < h0:
                continue
            hs = random.randint(h0, last_start)
            key = (day.isoformat(), hs)
            if key in busy:
                continue
            busy.add(key)
            return hs, dur
        return 9, 2.0

    # Phân bổ weekday cho từng (student, course): tránh trùng
    history_rows: list[dict[str, Any]] = []
    wd_sets = [
        [0, 2],
        [1, 3],
        [4, 0],
        [2, 4],
        [1, 4],
    ]

    for st in students:
        sid = st["id"]
        my_courses = [r for r in enroll_rows if r["student_id"] == sid]
        for ci, row in enumerate(my_courses):
            cid = row["course_id"]
            sc_id = student_course_ids.get((sid, cid))
            if not sc_id:
                continue
            lids = lessons_by_course.get(cid)
            if not lids:
                continue
            wdays = wd_sets[ci % len(wd_sets)]
            sessions_per_week = random.choice([2, 2, 3])
            wdays_use = (wdays * 2)[:sessions_per_week]

            for month in (4, 5):
                for d in range(1, monthrange(2026, month)[1] + 1):
                    day = date(2026, month, d)
                    if day.weekday() not in wdays_use:
                        continue
                    hs, dur = pick_slot(sid, day)
                    lesson_id = lids[random.randint(0, len(lids) - 1)]
                    dt = datetime(day.year, day.month, day.day, hs, 0, 0)
                    hist_status = "completed" if random.random() < 0.55 else "in_progress"
                    history_rows.append(
                        {
                            "student_id": sid,
                            "student_course_id": sc_id,
                            "lesson_id": lesson_id,
                            "date": dt.strftime("%Y-%m-%d %H:%M:%S"),
                            "study_duration": dur,
                            "status": hist_status,
                            "notes": f"Buổi học (script TKB 4–5/2026, bucket {sessions_per_week}x/tuần).",
                        }
                    )

    for i in range(0, len(history_rows), 50):
        _rpc(models, db, uid, pwd, "lms.learning.history", "create", [history_rows[i : i + 50]])

    logger.info("Đã tạo %s bản ghi lịch học (learning.history).", len(history_rows))

    logger.info("Hoàn tất. Mở lại form sinh viên / roadmap / Thời khóa biểu để thấy điểm & lịch (computed tự cập nhật).")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        logger.exception("Lỗi.")
        sys.exit(1)
