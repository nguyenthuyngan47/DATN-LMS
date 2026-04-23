#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Generate realistic LMS dataset for Odoo (lms module): categories, levels, tags,
courses, lessons, prerequisites, students, enrollments, learning history, roadmaps.

Output:
  - data/data_lms.json (scripts/import_data_lms_json; không dùng post_init)
  - Optional CSVs under data/export/ when --csv (post_init module lms chỉ import CSV)

Usage:
  python scripts/generate_realistic_lms_data.py
  python scripts/generate_realistic_lms_data.py --seed 42 --out data/data_lms.json
  python scripts/generate_realistic_lms_data.py --no-binary   # smaller JSON (no PDF/avatar bytes)

Logic (summary):
  - 8 categories, 3 levels (Beginner/Intermediate/Advanced), ~20 tags
  - 10 tracks x 5 courses = 50 courses; each course has exactly 10 lessons (500 lessons)
  - Prerequisites: linear chain within each track (course 2 -> prereq 1, 3 -> 2, ...)
  - ~1000 students (Vietnamese-style names + valid emails)
  - ~3500 enrollments with status/progress/final_score aligned to learning history
  - Progress in Odoo is computed from lessons marked completed in learning history only
  - final_score ~ Gaussian when enrollment completed; dropped has no final_score
  - YouTube URLs: real public video IDs (educational); one shared minimal PDF + tiny PNG avatar

Requires: Python 3.10+
"""

from __future__ import annotations

import argparse
import base64
import csv
import json
import math
import random
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "data" / "data_lms.json"

# ---------------------------------------------------------------------------
# Assets: tiny valid PNG (1x1) + minimal single-page PDF (bytes embedded as b64 in output meta)
# ---------------------------------------------------------------------------
def _tiny_png_b64() -> str:
    # 1x1 blue pixel PNG
    return (
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
    )


def _minimal_pdf_bytes() -> bytes:
    """Minimal valid PDF (single empty page)."""
    return (
        b"%PDF-1.4\n"
        b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
        b"3 0 obj<</Type/Page/MediaBox[0 0 612 792]/Parent 2 0 R>>endobj\n"
        b"xref\n0 4\n0000000000 65535 f \n"
        b"trailer<</Size 4/Root 1 0 R>>\n"
        b"startxref\n100\n%%EOF\n"
    )


# Real YouTube watch URLs (public, long-lived educational / tech talks; rotate per lesson)
YOUTUBE_IDS = [
    "rfscVS0vtbw",
    "kqtD5dpn9C8",
    "YYXdXT2l-gM",
    "t8pPdKYjJQo",
    "eWRfhZUzrLA",
    "8DvywoWv6fI",
    "aircAruvnKk",
    "NWONeJJo6PA",
    "T5pRl6brGgU",
    "G3hPH_bc0Ww",
    "hYA5dndUrRw",
    "qBigTkxsUi8",
    "p7HKvqRiKgY",
    "2JIxZY9BZHY",
    "yUBU5Oig2K8",
    "M576WGiWnFs",
    "u6_a37R0lT8",
    "WvhQf4HP57I",
    "JezqW4aCDR8",
    "c7O91H_wL00",
]


# ---------------------------------------------------------------------------
# Taxonomy
# ---------------------------------------------------------------------------
CATEGORIES: list[dict[str, Any]] = [
    {"id": 1, "name": "Web Development", "sequence": 10, "description": "HTML, CSS, JavaScript, frontend/backend web."},
    {"id": 2, "name": "Data Science", "sequence": 20, "description": "Phân tích dữ liệu, thống kê, trực quan hóa."},
    {"id": 3, "name": "Machine Learning & AI", "sequence": 30, "description": "Học máy, deep learning, ứng dụng AI."},
    {"id": 4, "name": "Cyber Security", "sequence": 40, "description": "An toàn thông tin, bảo mật hệ thống."},
    {"id": 5, "name": "DevOps & Automation", "sequence": 50, "description": "CI/CD, container, tự động hóa triển khai."},
    {"id": 6, "name": "Cloud Computing", "sequence": 60, "description": "Điện toán đám mây, kiến trúc phân tán."},
    {"id": 7, "name": "Database & Backend", "sequence": 70, "description": "SQL, NoSQL, thiết kế và vận hành CSDL."},
    {"id": 8, "name": "Software Engineering", "sequence": 80, "description": "Kiến trúc phần mềm, chất lượng mã, quy trình."},
]

LEVELS: list[dict[str, Any]] = [
    {"id": 1, "name": "Beginner", "sequence": 10, "description": "Nền tảng, phù hợp người mới."},
    {"id": 2, "name": "Intermediate", "sequence": 20, "description": "Đã có nền, đi sâu kỹ năng."},
    {"id": 3, "name": "Advanced", "sequence": 30, "description": "Chuyên sâu, tối ưu và kiến trúc."},
]

TAGS: list[dict[str, Any]] = [
    {"id": 1, "name": "Python", "color": 1},
    {"id": 2, "name": "JavaScript", "color": 2},
    {"id": 3, "name": "SQL", "color": 3},
    {"id": 4, "name": "Docker", "color": 4},
    {"id": 5, "name": "Kubernetes", "color": 5},
    {"id": 6, "name": "AWS", "color": 6},
    {"id": 7, "name": "Security", "color": 7},
    {"id": 8, "name": "Linux", "color": 8},
    {"id": 9, "name": "Git", "color": 9},
    {"id": 10, "name": "React", "color": 10},
    {"id": 11, "name": "Node.js", "color": 11},
    {"id": 12, "name": "Pandas", "color": 12},
    {"id": 13, "name": "TensorFlow", "color": 13},
    {"id": 14, "name": "Networking", "color": 14},
    {"id": 15, "name": "Agile", "color": 1},
    {"id": 16, "name": "Testing", "color": 2},
    {"id": 17, "name": "Microservices", "color": 3},
    {"id": 18, "name": "MongoDB", "color": 4},
    {"id": 19, "name": "Flutter", "color": 5},
    {"id": 20, "name": "Architecture", "color": 6},
]

# 10 tracks x 5 courses; category_id per track
TRACKS: list[dict[str, Any]] = [
    {"key": "web", "title": "Lập trình Web", "category_id": 1, "tag_ids": [2, 10, 9]},
    {"key": "py", "title": "Python & Backend", "category_id": 8, "tag_ids": [1, 3, 11]},
    {"key": "ds", "title": "Khoa học dữ liệu", "category_id": 2, "tag_ids": [1, 12]},
    {"key": "ml", "title": "Machine Learning", "category_id": 3, "tag_ids": [1, 13]},
    {"key": "sec", "title": "An toàn thông tin", "category_id": 4, "tag_ids": [7, 14]},
    {"key": "devops", "title": "DevOps", "category_id": 5, "tag_ids": [4, 5, 8]},
    {"key": "cloud", "title": "Cloud", "category_id": 6, "tag_ids": [6, 4]},
    {"key": "db", "title": "Cơ sở dữ liệu", "category_id": 7, "tag_ids": [3, 18]},
    {"key": "mobile", "title": "Phát triển Mobile", "category_id": 1, "tag_ids": [19, 1]},
    {"key": "se", "title": "Kỹ thuật phần mềm", "category_id": 8, "tag_ids": [16, 17, 15]},
]

# Per-course level index in track: 0,1 beginner; 2 intermediate; 3,4 advanced
TRACK_LEVEL_IDS = [1, 1, 2, 3, 3]

LESSON_BLUEPRINTS: list[list[str]] = [
    [
        "Tổng quan chương trình và mục tiêu học tập",
        "Thiết lập môi trường và công cụ",
        "Khái niệm cốt lõi (1)",
        "Khái niệm cốt lõi (2)",
        "Thực hành lab: bài tập cơ bản",
        "Xử lý lỗi thường gặp và debug",
        "Best practices và tài liệu tham khảo",
        "Mini project: ứng dụng nhỏ",
        "Ôn tập và kiểm tra nhanh",
        "Tổng kết và bài tập về nhà",
    ],
]


def _lesson_titles(course_name: str) -> list[str]:
    base = LESSON_BLUEPRINTS[0]
    return [
        f"{course_name} — {base[i]}" if i < len(base) else f"{course_name} — Bài {i+1}"
        for i in range(10)
    ]


@dataclass
class CourseSpec:
    id: int
    track_key: str
    track_title: str
    name: str
    short_desc: str
    category_id: int
    level_id: int
    tag_ids: list[int]
    lesson_titles: list[str]
    duration_hours: float
    state: str
    average_rating: float
    index_in_track: int
    prerequisite_id: int | None


def build_courses() -> list[CourseSpec]:
    courses: list[CourseSpec] = []
    cid = 0
    for tr in TRACKS:
        names = [
            f"{tr['title']} — Nền tảng",
            f"{tr['title']} — Thực hành ứng dụng",
            f"{tr['title']} — Chuyên sâu",
            f"{tr['title']} — Nâng cao & tối ưu",
            f"{tr['title']} — Dự án tổng hợp",
        ]
        descs = [
            "Làm quen nền tảng, thuật ngữ và luồng công việc thực tế.",
            "Xây dựng ứng dụng/bài toán điển hình theo hướng dẫn có kiểm soát.",
            "Đi sâu mô hình, công cụ và case study trung bình.",
            "Tối ưu hiệu năng, bảo mật và kiến trúc nâng cao.",
            "Tổng hợp kiến thức qua dự án cuối kỳ theo nhóm tiêu chí rõ ràng.",
        ]
        for j in range(5):
            cid += 1
            lvl = TRACK_LEVEL_IDS[j]
            pre = None
            if j > 0:
                pre = cid - 1
            titles = _lesson_titles(names[j])
            hours = round(20 + j * 4 + (cid % 7) * 0.5, 2)
            state = "published" if j % 3 != 2 else "published"  # mostly published
            rating = round(random.gauss(4.2, 0.35), 2)
            rating = max(3.0, min(5.0, rating))
            courses.append(
                CourseSpec(
                    id=cid,
                    track_key=tr["key"],
                    track_title=tr["title"],
                    name=names[j],
                    short_desc=descs[j],
                    category_id=tr["category_id"],
                    level_id=lvl,
                    tag_ids=list(tr["tag_ids"]),
                    lesson_titles=titles,
                    duration_hours=hours,
                    state=state,
                    average_rating=rating,
                    index_in_track=j,
                    prerequisite_id=pre,
                )
            )
    return courses


SURNAMES = [
    "Nguyễn", "Trần", "Lê", "Phạm", "Hoàng", "Huỳnh", "Võ", "Đặng", "Bùi", "Đỗ",
    "Ngô", "Dương", "Lý", "Vũ", "Đinh", "Trương", "Phan", "Mai", "Tôn", "Lâm",
]
GIVEN = [
    "An", "Bình", "Chi", "Dũng", "Giang", "Hà", "Hải", "Hương", "Khánh", "Lan",
    "Minh", "Nam", "Ngọc", "Phúc", "Quang", "Quỳnh", "Tâm", "Thảo", "Trang", "Tuấn",
    "Uyên", "Việt", "Yến", "Đức", "Hòa", "Khoa", "Linh", "Mai", "Nga", "Phương",
]


def vietnamese_name(rng: random.Random) -> str:
    return f"{rng.choice(SURNAMES)} {rng.choice(GIVEN)}"


def clip_score(x: float) -> float:
    return round(max(0.0, min(10.0, x)), 2)


def gaussian_score(rng: random.Random, mean: float = 7.4, sigma: float = 1.1) -> float:
    return clip_score(rng.gauss(mean, sigma))


def enrollment_target_progress(rng: random.Random, status: str, n_lessons: int) -> tuple[int, str]:
    """
    Return (completed_lesson_count, status) aligned with Odoo progress = completed/n * 100.
    """
    n = max(1, n_lessons)
    if status == "completed":
        return n, "completed"
    if status == "dropped":
        k = max(1, int(n * rng.uniform(0.15, 0.55)))
        return min(k, n - 1), "dropped"
    if status == "enrolled":
        hi = max(0, int(n * 0.10))
        k = rng.randint(0, hi)
        return k, "enrolled"
    # in_progress
    lo = max(1, int(math.ceil(n * 0.10)))
    hi = max(lo, int(math.floor(n * 0.90)))
    k = rng.randint(lo, hi)
    return k, "in_progress"


def build_learning_rows(
    rng: random.Random,
    sc_id: int,
    student_id: int,
    course_id: int,
    lesson_ids: list[int],
    completed_count: int,
    status: str,
    base_date: datetime,
) -> list[dict[str, Any]]:
    """Create history rows: first `completed_count` lessons completed; next lesson in progress or started."""
    rows: list[dict[str, Any]] = []
    n = len(lesson_ids)
    if n == 0:
        return rows

    if status == "dropped":
        # partial completion then stop
        for i in range(completed_count):
            dt = base_date - timedelta(days=(n - i) * 2 + rng.randint(0, 2))
            rows.append(
                {
                    "lesson_id": lesson_ids[i],
                    "date": dt.isoformat(),
                    "study_duration": round(rng.uniform(0.4, 2.2), 2),
                    "status": "completed",
                    "notes": "Hoàn thành theo lịch học.",
                }
            )
        return rows

    for i in range(completed_count):
        dt = base_date - timedelta(days=(n - i) * 3 + rng.randint(0, 3))
        rows.append(
            {
                "lesson_id": lesson_ids[i],
                "date": dt.isoformat(),
                "study_duration": round(rng.uniform(0.5, 2.5), 2),
                "status": "completed",
                "notes": "Hoàn thành bài; ôn tập slide và lab.",
            }
        )

    if completed_count < n and status in ("enrolled", "in_progress"):
        nxt = completed_count
        st = "in_progress" if status == "in_progress" else "started"
        dt = base_date - timedelta(days=rng.randint(0, 5))
        rows.append(
            {
                "lesson_id": lesson_ids[nxt],
                "date": dt.isoformat(),
                "study_duration": round(rng.uniform(0.3, 1.2), 2),
                "status": st,
                "notes": "Đang xem video và làm bài tập kèm.",
            }
        )

    return rows


def generate_dataset(
    seed: int,
    include_binary: bool,
    n_students: int,
    target_enrollments: int,
) -> dict[str, Any]:
    rng = random.Random(seed)
    courses = build_courses()
    course_by_id = {c.id: c for c in courses}

    # Lessons 1..500
    lessons: list[dict[str, Any]] = []
    lid = 0
    lesson_ids_by_course: dict[int, list[int]] = {}
    for c in courses:
        lesson_ids_by_course[c.id] = []
        for seq, title in enumerate(c.lesson_titles, start=1):
            lid += 1
            vid = YOUTUBE_IDS[(lid + seed) % len(YOUTUBE_IDS)]
            lessons.append(
                {
                    "id": lid,
                    "name": title,
                    "sequence": seq * 10,
                    "course_id": c.id,
                    "description": f"<p>Bài {seq}/10 — {c.name}. Nội dung gắn với chương trình {c.track_title}.</p>",
                    "video_url": f"https://www.youtube.com/watch?v={vid}",
                    "duration_minutes": 35 + (lid % 25),
                    "use_shared_pdf": bool(include_binary),
                }
            )
            lesson_ids_by_course[c.id].append(lid)

    # Courses JSON
    courses_out: list[dict[str, Any]] = []
    for c in courses:
        courses_out.append(
            {
                "id": c.id,
                "name": c.name,
                "description": f"<p>{c.short_desc} Thuộc lĩnh vực <b>{c.track_title}</b>. "
                f"Cấp độ theo lộ trình nội bộ (bài học đồng nhất với tên khóa).</p>",
                "category_id": c.category_id,
                "level_id": c.level_id,
                "duration_hours": c.duration_hours,
                "state": c.state,
                "is_active": True,
                "average_rating": c.average_rating,
                "instructor_id": 2,
            }
        )

    prerequisites: list[dict[str, int]] = []
    for c in courses:
        if c.prerequisite_id:
            prerequisites.append({"course_id": c.id, "prerequisite_id": c.prerequisite_id})

    course_tags: list[dict[str, int]] = []
    for c in courses:
        for t in c.tag_ids:
            course_tags.append({"course_id": c.id, "tag_id": t})

    # Students
    students: list[dict[str, Any]] = []
    used_emails: set[str] = set()
    for sid in range(1, n_students + 1):
        name = vietnamese_name(rng)
        email = f"sv{sid:04d}@lms.training.vn"
        if email in used_emails:
            email = f"sv{sid:04d}.{rng.randint(100,999)}@lms.training.vn"
        used_emails.add(email)
        lvl = rng.choices(["beginner", "intermediate", "advanced"], weights=[0.45, 0.35, 0.20])[0]
        students.append(
            {
                "id": sid,
                "name": name,
                "email": email,
                "phone": f"09{rng.randint(1,9)}{rng.randint(10**7, 10**8-1):07d}",
                "current_level": lvl,
                "learning_goals": "Hoàn thành chứng chỉ nội bộ và nâng cao kỹ năng làm việc nhóm.",
                "desired_skills": "Kỹ năng thực hành, tư duy hệ thống, viết tài liệu kỹ thuật.",
                "is_active": True,
                "use_shared_avatar": bool(include_binary),
            }
        )

    # Enrollments: distribute courses by student level affinity + random
    student_courses: list[dict[str, Any]] = []
    sc_id = 0
    course_ids = [c.id for c in courses]

    def level_weight(st_level: str, c: CourseSpec) -> float:
        m = {"beginner": 1, "intermediate": 2, "advanced": 3}
        ds = abs(m[st_level] - c.level_id)
        return 1.0 / (1.0 + ds)

    attempts = 0
    while len(student_courses) < target_enrollments and attempts < target_enrollments * 10:
        attempts += 1
        st = rng.choice(students)
        # weighted pick course
        weights = [level_weight(st["current_level"], course_by_id[coid]) for coid in course_ids]
        cid = rng.choices(course_ids, weights=weights, k=1)[0]
        # avoid duplicate (student, course)
        if any(x["student_id"] == st["id"] and x["course_id"] == cid for x in student_courses):
            continue
        sc_id += 1
        cspec = course_by_id[cid]
        nles = len(lesson_ids_by_course[cid])
        # status mix
        r = rng.random()
        if r < 0.42:
            st_sc = "completed"
        elif r < 0.78:
            st_sc = "in_progress"
        elif r < 0.92:
            st_sc = "enrolled"
        else:
            st_sc = "dropped"

        comp, eff = enrollment_target_progress(rng, st_sc, nles)
        enroll_day = date(2025, 9, 1) + timedelta(days=rng.randint(0, 120))
        start_day = enroll_day + timedelta(days=rng.randint(1, 14))
        completion_day = None
        final_sc = None
        if st_sc == "completed":
            completion_day = start_day + timedelta(days=rng.randint(14, 60))
            final_sc = gaussian_score(rng, mean=7.6, sigma=1.0)
        elif st_sc == "dropped":
            completion_day = None
            final_sc = None
        else:
            final_sc = None

        # Progress percent from completed count (matches Odoo compute)
        prog = round(100.0 * comp / nles, 2) if nles else 0.0

        student_courses.append(
            {
                "id": sc_id,
                "student_id": st["id"],
                "course_id": cid,
                "enrollment_date": enroll_day.isoformat(),
                "start_date": start_day.isoformat(),
                "completion_date": completion_day.isoformat() if completion_day else None,
                "status": st_sc,
                "progress": prog,
                "final_score": final_sc,
                "_eff": eff,
                "_completed_lessons": comp,
            }
        )

    # Learning histories
    learning_histories: list[dict[str, Any]] = []
    lh_id = 0
    for sc in student_courses:
        comp = sc.pop("_completed_lessons")
        sc.pop("_eff", None)
        lids = lesson_ids_by_course[sc["course_id"]]
        base_dt = datetime(2026, 1, 10, 10, 0, 0) + timedelta(days=rng.randint(0, 40))
        rows = build_learning_rows(
            rng,
            sc["id"],
            sc["student_id"],
            sc["course_id"],
            lids,
            comp,
            sc["status"],
            base_dt,
        )
        for row in rows:
            lh_id += 1
            learning_histories.append(
                {
                    "id": lh_id,
                    "student_id": sc["student_id"],
                    "student_course_id": sc["id"],
                    "course_id": sc["course_id"],
                    "lesson_id": row["lesson_id"],
                    "date": row["date"],
                    "study_duration": row["study_duration"],
                    "status": row["status"],
                    "notes": row["notes"],
                }
            )

    # Roadmaps: first 200 students
    roadmaps: list[dict[str, Any]] = []
    roadmap_courses: list[dict[str, Any]] = []
    rid = 0
    rcid = 0
    for stid in range(1, 201):
        rid += 1
        roadmaps.append(
            {
                "id": rid,
                "student_id": stid,
                "valid_from": "2026-02-01",
                "valid_to": "2026-12-31",
                "state": "suggested",
                "ai_recommendation_reason": "Gợi ý theo lộ trình nội bộ và mức độ hiện tại.",
                "recommendation_method": "rule_based",
            }
        )
        tk = rng.choice(TRACKS)["key"]
        tr_courses = [c for c in courses if c.track_key == tk][:3]
        if len(tr_courses) < 3:
            tr_courses = courses[:3]
        for i, co in enumerate(tr_courses[:3]):
            rcid += 1
            roadmap_courses.append(
                {
                    "id": rcid,
                    "roadmap_id": rid,
                    "course_id": co.id,
                    "sequence": (i + 1) * 10,
                    "priority": ["high", "medium", "low"][i],
                    "timeframe": ["short", "medium", "long"][i],
                    "status": "pending",
                    "recommendation_reason": f"Phù hợp mục tiêu sau khóa {co.track_title}.",
                    "similarity_score": round(rng.uniform(0.65, 0.95), 2),
                }
            )

    assets: dict[str, str] | None = None
    if include_binary:
        assets = {
            "lesson_pdf": base64.b64encode(_minimal_pdf_bytes()).decode("ascii"),
            "student_avatar": _tiny_png_b64(),
        }

    out: dict[str, Any] = {
        "_meta": {
            "generator": "generate_realistic_lms_data.py",
            "seed": seed,
            "counts": {
                "categories": len(CATEGORIES),
                "levels": len(LEVELS),
                "tags": len(TAGS),
                "courses": len(courses_out),
                "lessons": len(lessons),
                "students": len(students),
                "student_courses": len(student_courses),
                "learning_histories": len(learning_histories),
                "roadmaps": len(roadmaps),
                "roadmap_courses": len(roadmap_courses),
            },
            "notes": "Dùng với lms.hooks.post_init_hook; instructor_id=2 (admin Odoo).",
        },
        "categories": CATEGORIES,
        "levels": LEVELS,
        "tags": TAGS,
        "courses": courses_out,
        "course_prerequisites": prerequisites,
        "course_tags": course_tags,
        "lessons": lessons,
        "students": students,
        "student_courses": student_courses,
        "learning_histories": learning_histories,
        "roadmaps": roadmaps,
        "roadmap_courses": roadmap_courses,
    }
    if assets:
        out["assets"] = assets
    # Hook applies shared PDF/avatar from assets; strip internal flags from payload.
    for s in out["students"]:
        s.pop("use_shared_avatar", None)
    for l in out["lessons"]:
        l.pop("use_shared_pdf", None)
    return out


def write_csvs(data: dict[str, Any], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    skip = {"_meta", "assets"}
    for key, rows in data.items():
        if key in skip or not isinstance(rows, list) or not rows:
            continue
        if not isinstance(rows[0], dict):
            continue
        path = out_dir / f"{key}.csv"
        with path.open("w", newline="", encoding="utf-8") as f:
            fieldnames = list(rows[0].keys())
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            for r in rows:
                w.writerow({k: r.get(k) for k in fieldnames})


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate realistic LMS JSON for Odoo.")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--no-binary", action="store_true", help="Omit PDF/avatar assets (smaller file).")
    ap.add_argument("--students", type=int, default=1000)
    ap.add_argument("--enrollments", type=int, default=3500)
    ap.add_argument("--csv", action="store_true", help="Also write data/export/*.csv")
    args = ap.parse_args()

    random.seed(args.seed)
    data = generate_dataset(
        seed=args.seed,
        include_binary=not args.no_binary,
        n_students=args.students,
        target_enrollments=args.enrollments,
    )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))

    meta = data["_meta"]
    try:
        print(json.dumps(meta, ensure_ascii=False, indent=2))
    except UnicodeEncodeError:
        print(json.dumps(meta, ensure_ascii=True, indent=2))
    print(f"Wrote {args.out} ({args.out.stat().st_size // 1024} KB)")

    if args.csv:
        csv_dir = ROOT / "data" / "export"
        write_csvs(data, csv_dir)
        print(f"Wrote CSVs under {csv_dir}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
