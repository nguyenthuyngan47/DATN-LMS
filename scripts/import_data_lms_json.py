import base64
import json
from datetime import datetime, date
from pathlib import Path
import argparse
import sys
import re
import xmlrpc.client
import os

import psycopg2

try:
    import requests  # optional, only needed for debug-db-selector
except ImportError:  # pragma: no cover
    requests = None


JSON_PATH = Path("data/data_lms.json")
DB_CONFIG = {
    "host": "localhost",
    "port": 5432,
    # Allow overriding DB name when the project DB isn't named "lms".
    "dbname": os.environ.get("LMS_DB_NAME", "lms"),
    "user": "odoo",
    "password": "odoo",
}


def parse_date(value):
    if not value:
        return None
    if isinstance(value, date):
        return value
    s = str(value).strip()
    if not s:
        return None
    return datetime.strptime(s[:10], "%Y-%m-%d").date()


def parse_datetime(value):
    if not value:
        return None
    s = str(value).strip()
    if not s:
        return None
    s = s.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S")


def reset_sequence(cur, table):
    cur.execute(
        f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), COALESCE(MAX(id), 1), MAX(id) IS NOT NULL) FROM {table}"
    )


def main():
    if not JSON_PATH.exists():
        raise FileNotFoundError(f"Missing file: {JSON_PATH}")

    with JSON_PATH.open("r", encoding="utf-8") as f:
        data = json.load(f)

    conn = psycopg2.connect(**DB_CONFIG)
    conn.autocommit = False
    cur = conn.cursor()

    # categories
    for row in data.get("categories", []):
        cur.execute(
            """
            INSERT INTO lms_course_category (id, name, sequence, description)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE
            SET name = EXCLUDED.name,
                sequence = EXCLUDED.sequence,
                description = EXCLUDED.description
            """,
            (row["id"], row.get("name"), row.get("sequence"), row.get("description")),
        )

    # levels
    for row in data.get("levels", []):
        cur.execute(
            """
            INSERT INTO lms_course_level (id, name, sequence, description)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE
            SET name = EXCLUDED.name,
                sequence = EXCLUDED.sequence,
                description = EXCLUDED.description
            """,
            (row["id"], row.get("name"), row.get("sequence"), row.get("description")),
        )

    # tags
    for row in data.get("tags", []):
        cur.execute(
            """
            INSERT INTO lms_course_tag (id, name, color)
            VALUES (%s, %s, %s)
            ON CONFLICT (id) DO UPDATE
            SET name = EXCLUDED.name,
                color = EXCLUDED.color
            """,
            (row["id"], row.get("name"), row.get("color")),
        )

    assets = data.get("assets") or {}
    shared_pdf_bytes = None
    if assets.get("lesson_pdf"):
        try:
            shared_pdf_bytes = base64.b64decode(assets["lesson_pdf"])
        except Exception:
            shared_pdf_bytes = None
    shared_avatar_bytes = None
    if assets.get("student_avatar"):
        try:
            shared_avatar_bytes = base64.b64decode(assets["student_avatar"])
        except Exception:
            shared_avatar_bytes = None

    # courses (before lessons / prerequisites)
    for row in data.get("courses", []):
        cur.execute(
            """
            INSERT INTO lms_course (
                id, name, description, category_id, level_id, instructor_id, duration_hours, state, is_active, average_rating
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE
            SET name = EXCLUDED.name,
                description = EXCLUDED.description,
                category_id = EXCLUDED.category_id,
                level_id = EXCLUDED.level_id,
                instructor_id = EXCLUDED.instructor_id,
                duration_hours = EXCLUDED.duration_hours,
                state = EXCLUDED.state,
                is_active = EXCLUDED.is_active,
                average_rating = EXCLUDED.average_rating
            """,
            (
                row["id"],
                row.get("name"),
                row.get("description"),
                row.get("category_id"),
                row.get("level_id"),
                row.get("instructor_id"),
                row.get("duration_hours"),
                row.get("state"),
                row.get("is_active", True),
                row.get("average_rating"),
            ),
        )

    for row in data.get("course_prerequisites", []):
        cid, pid = row.get("course_id"), row.get("prerequisite_id")
        if not cid or not pid:
            continue
        cur.execute(
            """
            INSERT INTO course_prerequisite_rel (course_id, prerequisite_id)
            SELECT %s, %s
            WHERE NOT EXISTS (
                SELECT 1 FROM course_prerequisite_rel
                WHERE course_id = %s AND prerequisite_id = %s
            )
            """,
            (cid, pid, cid, pid),
        )

    for row in data.get("course_tags", []):
        cid, tid = row.get("course_id"), row.get("tag_id")
        if not cid or not tid:
            continue
        cur.execute(
            """
            INSERT INTO course_tag_rel (course_id, tag_id)
            SELECT %s, %s
            WHERE NOT EXISTS (
                SELECT 1 FROM course_tag_rel
                WHERE course_id = %s AND tag_id = %s
            )
            """,
            (cid, tid, cid, tid),
        )

    # lessons
    for row in data.get("lessons", []):
        pdf_bytes = shared_pdf_bytes
        pdf_name = None
        if pdf_bytes:
            pdf_name = row.get("pdf_filename") or "tai-lieu-bai-hoc.pdf"
        cur.execute(
            """
            INSERT INTO lms_lesson (
                id, name, sequence, course_id, description, video_url, duration_minutes, pdf_attachment, pdf_filename
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE
            SET name = EXCLUDED.name,
                sequence = EXCLUDED.sequence,
                course_id = EXCLUDED.course_id,
                description = EXCLUDED.description,
                video_url = EXCLUDED.video_url,
                duration_minutes = EXCLUDED.duration_minutes,
                pdf_attachment = EXCLUDED.pdf_attachment,
                pdf_filename = EXCLUDED.pdf_filename
            """,
            (
                row["id"],
                row.get("name"),
                row.get("sequence"),
                row.get("course_id"),
                row.get("description"),
                row.get("video_url"),
                row.get("duration_minutes"),
                pdf_bytes,
                pdf_name,
            ),
        )

    # students
    for row in data.get("students", []):
        cur.execute(
            """
            INSERT INTO lms_student (
                id, name, email, phone, current_level, learning_goals, desired_skills, is_active, image_1920
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE
            SET name = EXCLUDED.name,
                email = EXCLUDED.email,
                phone = EXCLUDED.phone,
                current_level = EXCLUDED.current_level,
                learning_goals = EXCLUDED.learning_goals,
                desired_skills = EXCLUDED.desired_skills,
                is_active = EXCLUDED.is_active,
                image_1920 = EXCLUDED.image_1920
            """,
            (
                row["id"],
                row.get("name"),
                row.get("email"),
                row.get("phone"),
                row.get("current_level"),
                row.get("learning_goals"),
                row.get("desired_skills"),
                row.get("is_active", True),
                shared_avatar_bytes,
            ),
        )

    # student_courses
    for row in data.get("student_courses", []):
        cur.execute(
            """
            INSERT INTO lms_student_course (
                id, student_id, course_id, enrollment_date, start_date, completion_date, status, progress, final_score
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE
            SET student_id = EXCLUDED.student_id,
                course_id = EXCLUDED.course_id,
                enrollment_date = EXCLUDED.enrollment_date,
                start_date = EXCLUDED.start_date,
                completion_date = EXCLUDED.completion_date,
                status = EXCLUDED.status,
                progress = EXCLUDED.progress,
                final_score = EXCLUDED.final_score
            """,
            (
                row["id"],
                row.get("student_id"),
                row.get("course_id"),
                parse_date(row.get("enrollment_date")),
                parse_date(row.get("start_date")),
                parse_date(row.get("completion_date")),
                row.get("status"),
                row.get("progress"),
                row.get("final_score"),
            ),
        )

    # Preload course mapping for learning history fallback.
    cur.execute("SELECT id, course_id FROM lms_lesson")
    lesson_course_map = {int(r[0]): int(r[1]) for r in cur.fetchall()}

    # learning_histories
    for row in data.get("learning_histories", []):
        lesson_id = row.get("lesson_id")
        course_id = row.get("course_id") or lesson_course_map.get(int(lesson_id)) if lesson_id else None
        cur.execute(
            """
            INSERT INTO lms_learning_history (
                id, student_id, student_course_id, course_id, lesson_id, date, study_duration, status, notes
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE
            SET student_id = EXCLUDED.student_id,
                student_course_id = EXCLUDED.student_course_id,
                course_id = EXCLUDED.course_id,
                lesson_id = EXCLUDED.lesson_id,
                date = EXCLUDED.date,
                study_duration = EXCLUDED.study_duration,
                status = EXCLUDED.status,
                notes = EXCLUDED.notes
            """,
            (
                row["id"],
                row.get("student_id"),
                row.get("student_course_id"),
                course_id,
                row.get("lesson_id"),
                parse_datetime(row.get("date")),
                row.get("study_duration"),
                row.get("status"),
                row.get("notes"),
            ),
        )

    # roadmaps
    for row in data.get("roadmaps", []):
        # `reviewed_by` used to point to lms.mentor, but we've removed the mentor model.
        # So we intentionally leave it empty during JSON import.
        cur.execute(
            """
            INSERT INTO lms_roadmap (
                id, student_id, valid_from, valid_to, state, reviewed_by, ai_recommendation_reason, recommendation_method
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE
            SET student_id = EXCLUDED.student_id,
                valid_from = EXCLUDED.valid_from,
                valid_to = EXCLUDED.valid_to,
                state = EXCLUDED.state,
                reviewed_by = EXCLUDED.reviewed_by,
                ai_recommendation_reason = EXCLUDED.ai_recommendation_reason,
                recommendation_method = EXCLUDED.recommendation_method
            """,
            (
                row["id"],
                row.get("student_id"),
                parse_date(row.get("valid_from")),
                parse_date(row.get("valid_to")),
                row.get("state"),
                None,
                row.get("ai_recommendation_reason"),
                row.get("recommendation_method"),
            ),
        )

    # roadmap_courses
    for row in data.get("roadmap_courses", []):
        cur.execute(
            """
            INSERT INTO lms_roadmap_course (
                id, roadmap_id, course_id, sequence, priority, timeframe, status, recommendation_reason, similarity_score
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE
            SET roadmap_id = EXCLUDED.roadmap_id,
                course_id = EXCLUDED.course_id,
                sequence = EXCLUDED.sequence,
                priority = EXCLUDED.priority,
                timeframe = EXCLUDED.timeframe,
                status = EXCLUDED.status,
                recommendation_reason = EXCLUDED.recommendation_reason,
                similarity_score = EXCLUDED.similarity_score
            """,
            (
                row["id"],
                row.get("roadmap_id"),
                row.get("course_id"),
                row.get("sequence"),
                row.get("priority"),
                row.get("timeframe"),
                row.get("status"),
                row.get("recommendation_reason"),
                row.get("similarity_score"),
            ),
        )

    # Refresh stored count fields on courses.
    cur.execute(
        """
        UPDATE lms_course c
        SET total_lessons = sub.cnt
        FROM (
            SELECT course_id, COUNT(*)::int AS cnt
            FROM lms_lesson
            GROUP BY course_id
        ) sub
        WHERE c.id = sub.course_id
        """
    )
    cur.execute(
        """
        UPDATE lms_course c
        SET enrolled_students_count = sub.cnt
        FROM (
            SELECT course_id, COUNT(*)::int AS cnt
            FROM lms_student_course
            GROUP BY course_id
        ) sub
        WHERE c.id = sub.course_id
        """
    )

    for table in [
        "lms_course_category",
        "lms_course_level",
        "lms_course_tag",
        "lms_course",
        "lms_lesson",
        "lms_student",
        "lms_student_course",
        "lms_learning_history",
        "lms_roadmap",
        "lms_roadmap_course",
    ]:
        reset_sequence(cur, table)

    conn.commit()

    for section in [
        "categories",
        "levels",
        "tags",
        "courses",
        "lessons",
        "students",
        "student_courses",
        "learning_histories",
        "roadmaps",
        "roadmap_courses",
    ]:
        print(f"{section}: {len(data.get(section, []))}")

    cur.close()
    conn.close()
    print("Import completed successfully.")


def connect_db():
    return psycopg2.connect(**DB_CONFIG)


def list_lms_tables() -> None:
    conn = connect_db()
    cur = conn.cursor()
    cur.execute(
        """
        select table_name
        from information_schema.tables
        where table_schema='public' and table_name like 'lms\\_%'
        order by table_name
        """
    )
    rows = cur.fetchall()
    print("lms_* tables count:", len(rows))
    for r in rows[:200]:
        print(r[0])
    cur.close()
    conn.close()


def print_lms_student_columns() -> None:
    conn = connect_db()
    cur = conn.cursor()
    cur.execute(
        """
        select column_name
        from information_schema.columns
        where table_schema='public' and table_name='lms_student'
        order by ordinal_position
        """
    )
    cols = [r[0] for r in cur.fetchall()]
    print("lms_student columns:", cols)
    cur.close()
    conn.close()


def inspect_lms_student_schema() -> None:
    conn = connect_db()
    cur = conn.cursor()
    cur.execute(
        """
        select column_name, is_nullable
        from information_schema.columns
        where table_schema='public' and table_name='lms_student'
        order by ordinal_position
        """
    )
    rows = cur.fetchall()
    not_null = [r[0] for r in rows if r[1] == "NO"]
    print("lms_student not_null:", not_null)
    cur.close()
    conn.close()


def inspect_lms_tables() -> None:
    def list_cols(cur, table_name: str) -> None:
        cur.execute(
            """
            select column_name, data_type
            from information_schema.columns
            where table_name = %s
            order by ordinal_position
            """,
            (table_name,),
        )
        cols = cur.fetchall()
        print(table_name, "col_count", len(cols))
        print([c[0] for c in cols[:60]])

    conn = connect_db()
    cur = conn.cursor()
    for t in ["lms_course", "lms_course_category", "lms_course_level", "lms_course_tag"]:
        try:
            list_cols(cur, t)
        except Exception as e:
            print(t, "FAILED", e)
    cur.close()
    conn.close()


def debug_fetch_odoo_db_selector() -> None:
    if requests is None:
        raise RuntimeError("Missing dependency 'requests'. Install requests to use debug-db-selector.")

    url = "http://localhost:8069/web/database/selector"
    r = requests.get(url, timeout=30)
    print("status", r.status_code, "len", len(r.text))
    print(r.text[:250])

    for needle in ['name="db"', "name='db'", 'id="db"', "id='db'", 'db"']:
        if needle in r.text:
            print("FOUND_NEEDLE", needle)

    idx = r.text.lower().find("database")
    if idx != -1:
        print("first_database_idx", idx)
        print(r.text[idx : idx + 400])

    m = re.search(r'<select[^>]+name=["\']db["\'][^>]*>(.*?)</select>', r.text, flags=re.DOTALL | re.IGNORECASE)
    if not m:
        dbs = re.findall(
            r'<option[^>]+value=["\']([^"\']+)["\'][^>]*>[^<]*</option>',
            r.text,
            flags=re.IGNORECASE,
        )
        print("db_select_not_found_fallback_count", len(dbs))
        print("first_options", dbs[:30])
        return

    options_html = m.group(1)
    dbs = re.findall(r'<option[^>]+value=["\']([^"\']+)["\']', options_html, flags=re.IGNORECASE)
    print("db_options_count", len(dbs))
    print("dbs_first_50", dbs[:50])


def debug_odoo_xmlrpc_methods() -> None:
    base_url = "http://localhost:8069"
    common = xmlrpc.client.ServerProxy(f"{base_url}/xmlrpc/2/common", allow_none=True)

    print("== try system.listMethods() ==")
    methods = common.system.listMethods()
    interesting = [m for m in methods if any(k in m.lower() for k in ["auth", "db", "list", "version"])]
    print("interesting_count", len(interesting))
    for m in sorted(interesting)[:200]:
        print(m)

    print("== call candidates ==")
    for fn in ["version", "list", "list_db", "db_list"]:
        try:
            f = getattr(common, fn)
            res = f()
            print(fn, "->", res)
        except Exception as e:
            print(fn, "FAILED:", str(e))


def cli_main() -> None:
    parser = argparse.ArgumentParser(description="Import/inspect LMS data utilities.")
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("import", help="Import data from data/data_lms.json into DB. (default)")
    sub.add_parser("list-tables", help="List lms_* tables.")
    sub.add_parser("inspect-student-schema", help="Print lms_student columns not-null constraints.")
    sub.add_parser("inspect-tables", help="Print columns for core LMS tables.")
    sub.add_parser("print-student-columns", help="Print lms_student column names.")
    sub.add_parser("debug-db-selector", help="Debug Odoo /web/database/selector HTML (requires requests).")
    sub.add_parser("debug-xmlrpc-methods", help="Debug xmlrpc/2/common methods.")

    if len(sys.argv) <= 1:
        main()
        return

    args = parser.parse_args()
    cmd = args.cmd or "import"

    if cmd == "import":
        main()
    elif cmd == "list-tables":
        list_lms_tables()
    elif cmd == "inspect-student-schema":
        inspect_lms_student_schema()
    elif cmd == "inspect-tables":
        inspect_lms_tables()
    elif cmd == "print-student-columns":
        print_lms_student_columns()
    elif cmd == "debug-db-selector":
        debug_fetch_odoo_db_selector()
    elif cmd == "debug-xmlrpc-methods":
        debug_odoo_xmlrpc_methods()
    else:
        raise SystemExit(f"Unknown command: {cmd}")


if __name__ == "__main__":
    cli_main()
