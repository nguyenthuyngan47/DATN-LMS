#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Crawl dữ liệu thật qua HTTP (không tự sinh chuỗi kiểu "Khóa 001"):
  - Học viên: RandomUser API (hồ sơ công khai mẫu)
  - Khóa học / bài học: Open Library (catalog sách/tác phẩm thật)
  - Danh mục / cấp / nhãn: Wikipedia API (tên danh mục thật)

Tuân thủ: User-Agent rõ ràng, trễ giữa request (Wikimedia / Open Library).

Sau khi chạy:
  python export_data_to_csv.py --config data_export_config.json
  hoặc đẩy vào Odoo: python odoo_import_crawl.py --db TEN_DB (xem odoo_import_crawl.py)
"""

from __future__ import annotations

import logging
import re
import sqlite3
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote

import requests

SCRIPTS_DIR = Path(__file__).resolve().parent
DB_PATH = SCRIPTS_DIR / "lms_export_demo.db"

N = 200
SESSION = requests.Session()
SESSION.headers.update(
    {
        "User-Agent": "LMS-DataCrawler/1.0 (educational data export; +https://github.com/)",
        "Accept": "application/json",
    }
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("crawl_lms")


def _sleep(s: float = 0.35) -> None:
    time.sleep(s)


def http_get_json(url: str, params: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    for attempt in range(3):
        try:
            r = SESSION.get(url, params=params or {}, timeout=45)
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            logger.warning("Request lỗi (lần %s): %s — %s", attempt + 1, url, e)
            _sleep(1.0 * (attempt + 1))
    raise RuntimeError(f"Không tải được: {url}")


def fetch_random_users(n: int) -> list[dict[str, Any]]:
    """https://randomuser.me/documentation — dữ liệu mẫu công khai."""
    url = "https://randomuser.me/api/"
    params = {
        "results": n,
        "inc": "name,email,phone,cell,dob,picture,nat,login",
        "nat": "us,gb,fr,de,es,it,nl,br,ca,au,vn",
    }
    data = http_get_json(url, params)
    results = data.get("results") or []
    if len(results) < n:
        raise RuntimeError(f"RandomUser trả về {len(results)} < {n}")
    return results[:n]


def _age_from_dob(dob: Optional[dict]) -> Optional[int]:
    if not dob or not dob.get("date"):
        return None
    try:
        d = datetime.fromisoformat(dob["date"].replace("Z", "+00:00"))
        today = date.today()
        return today.year - d.date().year - ((today.month, today.day) < (d.month, d.day))
    except (ValueError, TypeError):
        return None


def _level_from_age(age: Optional[int]) -> str:
    if age is None:
        return "beginner"
    if age < 22:
        return "beginner"
    if age < 40:
        return "intermediate"
    return "advanced"


def wiki_allcategories(prefixes: list[str], max_count: int) -> list[str]:
    """MediaWiki API: allcategories — tên danh mục thật trên Wikipedia."""
    base = "https://en.wikipedia.org/w/api.php"
    out: list[str] = []
    seen: set[str] = set()
    for prefix in prefixes:
        cont: Optional[str] = None
        while len(out) < max_count:
            params: dict[str, Any] = {
                "action": "query",
                "list": "allcategories",
                "acprefix": prefix,
                "aclimit": 500,
                "format": "json",
            }
            if cont:
                params["accontinue"] = cont
            data = http_get_json(base, params)
            q = data.get("query") or {}
            for cat in q.get("allcategories") or []:
                name = (cat.get("*") or "").strip()
                if not name or name in seen:
                    continue
                seen.add(name)
                out.append(name)
                if len(out) >= max_count:
                    return out[:max_count]
            cont = (data.get("continue") or {}).get("accontinue")
            _sleep(0.4)
            if not cont:
                break
    if len(out) < max_count:
        raise RuntimeError(f"Wikipedia allcategories chỉ thu được {len(out)}/{max_count}")
    return out[:max_count]


def openlibrary_works(subjects: list[str], need: int) -> list[dict[str, Any]]:
    """Open Library subject API — tác phẩm / sách thật (metadata công khai)."""
    collected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for subj in subjects:
        if len(collected) >= need:
            break
        offset = 0
        while len(collected) < need:
            batch = min(50, need - len(collected))
            path = quote(subj.strip(), safe="")
            url = f"https://openlibrary.org/subjects/{path}.json"
            try:
                data = http_get_json(url, {"limit": batch, "offset": offset})
            except (RuntimeError, requests.RequestException) as e:
                logger.warning("Bỏ qua subject '%s': %s", subj, e)
                break
            works = data.get("works") or []
            if not works:
                break
            for w in works:
                title = (w.get("title") or "").strip()
                key = title.lower()
                if not title or key in seen:
                    continue
                seen.add(key)
                collected.append(w)
                if len(collected) >= need:
                    return collected[:need]
            offset += len(works)
            _sleep(0.35)
    if len(collected) < need:
        raise RuntimeError(
            f"Open Library không đủ tác phẩm ({len(collected)}/{need}). Thử mạng hoặc mở rộng subjects."
        )
    return collected[:need]


def _sanitize_htmlish(s: str, max_len: int = 2000) -> str:
    s = re.sub(r"\s+", " ", s).strip()
    if len(s) > max_len:
        s = s[: max_len - 1] + "…"
    return s


def _work_description(w: dict[str, Any]) -> str:
    authors = w.get("authors") or []
    names = []
    for a in authors[:3]:
        if isinstance(a, dict) and a.get("name"):
            names.append(a["name"])
    year = w.get("first_publish_year")
    bits = []
    if names:
        bits.append("Authors: " + ", ".join(names))
    if year:
        bits.append(f"First published: {year}")
    subj = w.get("subject") if isinstance(w.get("subject"), str) else None
    if subj:
        bits.append("Subject: " + subj[:200])
    return _sanitize_htmlish("<p>" + " | ".join(bits) + "</p>" if bits else "<p>(Open Library)</p>")


def main() -> int:
    logger.info("Bắt đầu crawl (N=%s) — có thể mất 1–3 phút do giới hạn tốc độ.", N)

    # --- Thu nguồn ---
    logger.info("1/5 RandomUser: học viên…")
    users = fetch_random_users(N)

    logger.info("2/5 Wikipedia: danh mục / cấp / nhãn…")
    cat_names = wiki_allcategories(
        ["Education", "Computer", "Science", "Art", "Music", "Medicine", "History", "Law"],
        N,
    )
    level_names = wiki_allcategories(
        ["Level", "Grade", "Degree", "Class", "Stage", "Rank", "Standard", "Exam"],
        N,
    )
    tag_names = wiki_allcategories(
        ["Data", "Software", "Web", "System", "Code", "Study", "Theory", "Design"],
        N,
    )

    logger.info("3/5 Open Library: khóa học (ưu tiên IT/CS — works)…")
    # Chủ đề Open Library: nhiều môn lập trình / máy tính để không bị “khóa học ít” / lệch ngành nghề.
    course_subjects = [
        "programming",
        "javascript",
        "python",
        "java",
        "c++",
        "computer science",
        "computer programming",
        "software engineering",
        "object-oriented programming",
        "algorithms",
        "data structures",
        "networking",
        "computer networks",
        "operating systems",
        "databases",
        "sql",
        "machine learning",
        "artificial intelligence",
        "cybersecurity",
        "computer security",
        "computer graphics",
        "compilers",
        "distributed computing",
        "embedded systems",
        "information technology",
        "web development",
        "mobile computing",
        "cloud computing",
        "ruby",
        "php",
        "perl",
        "scala",
        "kotlin",
        "typescript",
        "rust",
        "haskell",
        "linux",
        "unix",
        "git",
        "devops",
        "computer architecture",
        "digital electronics",
        "parallel computing",
        "computational mathematics",
    ]
    works_courses = openlibrary_works(course_subjects, N)

    logger.info("4/5 Open Library: bài học (works khác chủ đề IT/kỹ thuật)…")
    lesson_subjects = [
        "python",
        "javascript",
        "java",
        "c++",
        "computer science",
        "computer programming",
        "software engineering",
        "algorithms",
        "databases",
        "sql",
        "machine learning",
        "artificial intelligence",
        "cybersecurity",
        "networking",
        "operating systems",
        "web development",
        "mobile computing",
        "cloud computing",
        "ruby",
        "php",
        "perl",
        "scala",
        "kotlin",
        "typescript",
        "rust",
        "linux",
        "unix",
        "git",
        "devops",
        "computer graphics",
        "compilers",
        "distributed computing",
        "embedded systems",
        "information technology",
        "object-oriented programming",
        "computer networks",
        "parallel computing",
        "digital electronics",
        "computational mathematics",
        "computer architecture",
        "haskell",
        "computer security",
    ]
    works_lessons = openlibrary_works(lesson_subjects, N)

    # --- SQLite ---
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    try:
        c = conn.cursor()
        c.executescript(
            """
            PRAGMA foreign_keys = OFF;
            DROP TABLE IF EXISTS lms_learning_history;
            DROP TABLE IF EXISTS lms_roadmap_course;
            DROP TABLE IF EXISTS lms_roadmap;
            DROP TABLE IF EXISTS lms_student_course;
            DROP TABLE IF EXISTS lms_course_prerequisite_rel;
            DROP TABLE IF EXISTS lms_course_tag_rel;
            DROP TABLE IF EXISTS lms_lesson;
            DROP TABLE IF EXISTS lms_student;
            DROP TABLE IF EXISTS lms_course;
            DROP TABLE IF EXISTS lms_course_tag;
            DROP TABLE IF EXISTS lms_course_category;
            DROP TABLE IF EXISTS lms_course_level;
            PRAGMA foreign_keys = ON;

            CREATE TABLE lms_course_category (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                description TEXT
            );
            CREATE TABLE lms_course_level (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                description TEXT
            );
            CREATE TABLE lms_course_tag (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                color INTEGER
            );
            CREATE TABLE lms_course (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT,
                image_1920_placeholder TEXT,
                category_id INTEGER NOT NULL,
                level_id INTEGER NOT NULL,
                instructor_id INTEGER,
                duration_hours REAL NOT NULL,
                prerequisite_summary TEXT,
                total_lessons INTEGER NOT NULL,
                enrolled_students_count INTEGER NOT NULL,
                average_rating REAL,
                state TEXT NOT NULL,
                is_active INTEGER NOT NULL,
                FOREIGN KEY (category_id) REFERENCES lms_course_category(id),
                FOREIGN KEY (level_id) REFERENCES lms_course_level(id)
            );
            CREATE TABLE lms_course_tag_rel (
                id INTEGER PRIMARY KEY,
                course_id INTEGER NOT NULL,
                tag_id INTEGER NOT NULL,
                FOREIGN KEY (course_id) REFERENCES lms_course(id),
                FOREIGN KEY (tag_id) REFERENCES lms_course_tag(id)
            );
            CREATE TABLE lms_course_prerequisite_rel (
                id INTEGER PRIMARY KEY,
                course_id INTEGER NOT NULL,
                prerequisite_id INTEGER,
                FOREIGN KEY (course_id) REFERENCES lms_course(id),
                FOREIGN KEY (prerequisite_id) REFERENCES lms_course(id)
            );
            CREATE TABLE lms_lesson (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                description TEXT,
                course_id INTEGER NOT NULL,
                video_url TEXT,
                video_attachment_placeholder TEXT,
                pdf_attachment_placeholder TEXT,
                pdf_filename TEXT,
                duration_minutes INTEGER NOT NULL,
                FOREIGN KEY (course_id) REFERENCES lms_course(id)
            );
            CREATE TABLE lms_student (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                email TEXT NOT NULL,
                phone TEXT,
                image_1920_placeholder TEXT,
                current_level TEXT NOT NULL,
                learning_goals TEXT,
                desired_skills TEXT,
                user_id INTEGER,
                total_courses INTEGER NOT NULL,
                completed_courses INTEGER NOT NULL,
                average_score REAL NOT NULL,
                total_study_time REAL NOT NULL,
                last_activity_date TEXT,
                is_active INTEGER NOT NULL,
                inactive_days INTEGER NOT NULL
            );
            CREATE TABLE lms_student_course (
                id INTEGER PRIMARY KEY,
                student_id INTEGER NOT NULL,
                course_id INTEGER NOT NULL,
                enrollment_date TEXT NOT NULL,
                start_date TEXT,
                completion_date TEXT,
                status TEXT NOT NULL,
                progress REAL NOT NULL,
                final_score REAL,
                UNIQUE(student_id, course_id),
                FOREIGN KEY (student_id) REFERENCES lms_student(id),
                FOREIGN KEY (course_id) REFERENCES lms_course(id)
            );
            CREATE TABLE lms_learning_history (
                id INTEGER PRIMARY KEY,
                student_id INTEGER NOT NULL,
                student_course_id INTEGER NOT NULL,
                course_id INTEGER NOT NULL,
                lesson_id INTEGER NOT NULL,
                instructor_id INTEGER,
                date TEXT NOT NULL,
                study_duration REAL NOT NULL,
                status TEXT NOT NULL,
                name TEXT,
                is_at_risk INTEGER NOT NULL,
                notes TEXT,
                FOREIGN KEY (student_id) REFERENCES lms_student(id),
                FOREIGN KEY (student_course_id) REFERENCES lms_student_course(id),
                FOREIGN KEY (course_id) REFERENCES lms_course(id),
                FOREIGN KEY (lesson_id) REFERENCES lms_lesson(id)
            );
            CREATE TABLE lms_roadmap (
                id INTEGER PRIMARY KEY,
                student_id INTEGER NOT NULL,
                valid_from TEXT,
                valid_to TEXT,
                state TEXT NOT NULL,
                reviewed_by INTEGER,
                ai_recommendation_reason TEXT,
                recommendation_method TEXT,
                FOREIGN KEY (student_id) REFERENCES lms_student(id)
            );
            CREATE TABLE lms_roadmap_course (
                id INTEGER PRIMARY KEY,
                roadmap_id INTEGER NOT NULL,
                course_id INTEGER NOT NULL,
                sequence INTEGER NOT NULL,
                priority TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                status TEXT NOT NULL,
                recommendation_reason TEXT,
                similarity_score REAL,
                FOREIGN KEY (roadmap_id) REFERENCES lms_roadmap(id),
                FOREIGN KEY (course_id) REFERENCES lms_course(id)
            );
            """
        )

        enroll_status = ["enrolled", "in_progress", "completed", "dropped"]
        lh_status = ["started", "in_progress", "completed", "skipped"]
        course_states = ["draft", "published", "archived"]

        c.executemany(
            "INSERT INTO lms_course_category (id, name, sequence, description) VALUES (?,?,?,?)",
            [
                (i + 1, cat_names[i], (i + 1) * 10, f"Wikipedia category name (en): {cat_names[i]}")
                for i in range(N)
            ],
        )
        c.executemany(
            "INSERT INTO lms_course_level (id, name, sequence, description) VALUES (?,?,?,?)",
            [
                (i + 1, level_names[i], (i + 1) * 10, f"Wikipedia category name (en): {level_names[i]}")
                for i in range(N)
            ],
        )
        c.executemany(
            "INSERT INTO lms_course_tag (id, name, color) VALUES (?,?,?)",
            [(i + 1, tag_names[i][:120], (i * 3) % 12) for i in range(N)],
        )

        courses_rows = []
        for i in range(N):
            w = works_courses[i]
            title = _sanitize_htmlish(w.get("title") or f"Work {i}", 500)
            cid = i + 1
            cat_id = (i % N) + 1
            lv_id = ((i * 11) % N) + 1
            courses_rows.append(
                (
                    cid,
                    title,
                    _work_description(w),
                    (w.get("cover_id") and f"https://covers.openlibrary.org/b/id/{w['cover_id']}-L.jpg")
                    or "",
                    cat_id,
                    lv_id,
                    (cid % 20) + 1,
                    float(6 + (i % 50)),
                    f"Prerequisite hint: see Open Library work key {w.get('key', '')}",
                    1,
                    1,
                    round(3.0 + (i % 20) / 10.0, 2),
                    course_states[i % len(course_states)],
                    1,
                )
            )
        c.executemany(
            """INSERT INTO lms_course (
                id, name, description, image_1920_placeholder, category_id, level_id,
                instructor_id, duration_hours, prerequisite_summary, total_lessons,
                enrolled_students_count, average_rating, state, is_active
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            courses_rows,
        )

        c.executemany(
            "INSERT INTO lms_course_tag_rel (id, course_id, tag_id) VALUES (?,?,?)",
            [(i + 1, i + 1, (i % N) + 1) for i in range(N)],
        )
        c.executemany(
            "INSERT INTO lms_course_prerequisite_rel (id, course_id, prerequisite_id) VALUES (?,?,?)",
            [(i + 1, i + 1, i if i > 0 else None) for i in range(N)],
        )

        lesson_rows = []
        for i in range(N):
            w = works_lessons[i]
            title = _sanitize_htmlish(w.get("title") or f"Lesson work {i}", 500)
            lesson_rows.append(
                (
                    i + 1,
                    title,
                    10,
                    _work_description(w),
                    i + 1,
                    f"https://archive.org/details/placeholder{i}" if i % 2 == 0 else "",
                    "",
                    "",
                    f"openlibrary_{(w.get('key') or '').replace('/', '_')[:80] or i}.pdf",
                    15 + (i % 90),
                )
            )
        c.executemany(
            """INSERT INTO lms_lesson (
                id, name, sequence, description, course_id, video_url,
                video_attachment_placeholder, pdf_attachment_placeholder, pdf_filename, duration_minutes
            ) VALUES (?,?,?,?,?,?,?,?,?,?)""",
            lesson_rows,
        )

        student_rows = []
        for i in range(N):
            u = users[i]
            nm = u.get("name") or {}
            full = f"{nm.get('first', '').strip()} {nm.get('last', '').strip()}".strip() or u.get("email", f"user{i}")
            age = _age_from_dob(u.get("dob"))
            lvl = _level_from_age(age)
            pic = (u.get("picture") or {}).get("large") or ""
            phone = u.get("cell") or u.get("phone") or ""
            email = (u.get("email") or "").strip()
            nat = (u.get("nat") or "").upper()
            goals = f"Học tập & phát triển ({nat}); tuổi ước lượng: {age if age is not None else 'n/a'}"
            skills = f"Ngôn ngữ gốc: {nat}; mục tiêu theo hồ sơ RandomUser."
            student_rows.append(
                (
                    i + 1,
                    full,
                    email,
                    phone,
                    pic,
                    lvl,
                    goals,
                    skills,
                    10_000 + i,
                    1,
                    1 if i % 3 == 0 else 0,
                    round(6.0 + (i % 15) / 5.0, 2),
                    round(2.0 + (i % 40) / 10.0, 2),
                    date.today().isoformat(),
                    1,
                    (i % 12),
                )
            )
        c.executemany(
            """INSERT INTO lms_student (
                id, name, email, phone, image_1920_placeholder, current_level,
                learning_goals, desired_skills, user_id, total_courses, completed_courses,
                average_score, total_study_time, last_activity_date, is_active, inactive_days
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            student_rows,
        )

        sc_rows = []
        for i in range(N):
            idx = i + 1
            st = enroll_status[i % len(enroll_status)]
            sc_rows.append(
                (
                    idx,
                    idx,
                    idx,
                    (date.today() - timedelta(days=120 + (i % 60))).isoformat(),
                    (date.today() - timedelta(days=90 + (i % 30))).isoformat(),
                    (date.today() - timedelta(days=10)).isoformat() if st == "completed" else None,
                    st,
                    float((i * 17) % 100),
                    round(5.0 + (i % 45) / 10.0, 2) if st == "completed" else None,
                )
            )
        c.executemany(
            """INSERT INTO lms_student_course (
                id, student_id, course_id, enrollment_date, start_date, completion_date,
                status, progress, final_score
            ) VALUES (?,?,?,?,?,?,?,?,?)""",
            sc_rows,
        )

        lh_rows = []
        for i in range(N):
            idx = i + 1
            w_c = works_courses[i]
            w_l = works_lessons[i]
            notes = (
                f"Course OL: {w_c.get('key', '')}; Lesson OL: {w_l.get('key', '')}; "
                f"source=RandomUser+OpenLibrary+Wikipedia"
            )
            lh_rows.append(
                (
                    idx,
                    idx,
                    idx,
                    idx,
                    idx,
                    (idx % 20) + 1,
                    (datetime.now(timezone.utc) - timedelta(hours=i * 3)).replace(microsecond=0).isoformat(),
                    round(0.3 + (i % 25) * 0.07, 2),
                    lh_status[i % len(lh_status)],
                    _sanitize_htmlish(
                        f"{works_courses[i].get('title', '')[:80]} | {works_lessons[i].get('title', '')[:80]}",
                        300,
                    ),
                    1 if i % 13 == 0 else 0,
                    notes[:2000],
                )
            )
        c.executemany(
            """INSERT INTO lms_learning_history (
                id, student_id, student_course_id, course_id, lesson_id, instructor_id,
                date, study_duration, status, name, is_at_risk, notes
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            lh_rows,
        )

        roadmap_states = ["draft", "suggested", "approved", "locked", "rejected"]
        recommendation_methods = ["content_based", "rule_based", "hybrid"]
        roadmap_rows = []
        for i in range(N):
            idx = i + 1
            vf = (date.today() - timedelta(days=45 + (i % 30))).isoformat()
            vt = (date.today() + timedelta(days=30 + (i % 60))).isoformat()
            roadmap_rows.append(
                (
                    idx,
                    idx,  # one roadmap per student in demo crawl set
                    vf,
                    vt,
                    roadmap_states[i % len(roadmap_states)],
                    None,
                    "Đề xuất tự động từ dữ liệu học tập crawl demo.",
                    recommendation_methods[i % len(recommendation_methods)],
                )
            )
        c.executemany(
            """INSERT INTO lms_roadmap (
                id, student_id, valid_from, valid_to, state, reviewed_by,
                ai_recommendation_reason, recommendation_method
            ) VALUES (?,?,?,?,?,?,?,?)""",
            roadmap_rows,
        )

        roadmap_priorities = ["high", "medium", "low"]
        roadmap_timeframes = ["short", "medium", "long"]
        roadmap_line_statuses = ["pending", "in_progress", "completed", "skipped"]
        roadmap_course_rows = []
        rc_id = 1
        for i in range(N):
            roadmap_id = i + 1
            for j in range(3):
                course_id = ((i + j * 11) % N) + 1
                roadmap_course_rows.append(
                    (
                        rc_id,
                        roadmap_id,
                        course_id,
                        (j + 1) * 10,
                        roadmap_priorities[(i + j) % len(roadmap_priorities)],
                        roadmap_timeframes[(i + j) % len(roadmap_timeframes)],
                        roadmap_line_statuses[(i + j) % len(roadmap_line_statuses)],
                        f"Gợi ý học tiếp dựa trên course #{course_id}",
                        round(0.55 + ((i + j) % 40) * 0.01, 2),
                    )
                )
                rc_id += 1
        c.executemany(
            """INSERT INTO lms_roadmap_course (
                id, roadmap_id, course_id, sequence, priority, timeframe, status,
                recommendation_reason, similarity_score
            ) VALUES (?,?,?,?,?,?,?,?,?)""",
            roadmap_course_rows,
        )

        conn.commit()
        logger.info("5/5 Đã ghi SQLite: %s", DB_PATH.resolve())
        for t in (
            "lms_course_category",
            "lms_course_level",
            "lms_course_tag",
            "lms_course",
            "lms_course_tag_rel",
            "lms_course_prerequisite_rel",
            "lms_lesson",
            "lms_student",
            "lms_student_course",
            "lms_learning_history",
            "lms_roadmap",
        ):
            c.execute(f"SELECT COUNT(*) FROM {t}")
            assert c.fetchone()[0] == N
        c.execute("SELECT COUNT(*) FROM lms_roadmap_course")
        assert c.fetchone()[0] == N * 3
        logger.info("Hoàn tất: mỗi bảng %s bản ghi (dữ liệu crawl).", N)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        logger.exception("Crawl thất bại.")
        sys.exit(1)
