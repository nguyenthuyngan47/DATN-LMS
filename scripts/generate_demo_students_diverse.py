import argparse
import random
import datetime
import sys
from typing import Dict, List
import os

import psycopg2


SURNAME = [
    "Nguyễn", "Trần", "Lê", "Phạm", "Hoàng", "Vũ", "Đặng", "Bùi", "Đỗ", "Mai",
    "Võ", "Hồ", "Dương", "Lý", "Tạ",
]
MIDDLE = ["Văn", "Thị", "Minh", "Gia", "Quang", "Hoàng", "Hữu", "Tuấn", "Thanh", "Tú"]
GIVEN_MALE = ["Anh", "Duy", "Huy", "Khoa", "Khôi", "Khang", "Nam", "Phong", "Quân", "Sơn", "Tuấn", "Vũ", "Yên"]
GIVEN_FEMALE = ["An", "Bảo", "Chi", "Hà", "Hân", "Huyền", "Khánh", "Linh", "Lệ", "Mai", "Nhung", "Ngọc", "Phương", "Thư", "Yến"]

GOALS = [
    "Nâng cao kỹ năng lập trình và làm dự án thực tế.",
    "Tập trung xây nền tảng để theo kịp lộ trình công nghệ.",
    "Học có mục tiêu: từ cơ bản tới ứng dụng thực hành.",
    "Phát triển kỹ năng phân tích dữ liệu và trực quan hóa.",
    "Rèn tư duy giải quyết bài toán thông qua bài tập thực hành.",
]

SKILLS = {
    "Beginner": [
        "Python basics", "Cú pháp lập trình", "Tin học nền tảng", "Tư duy thuật toán",
    ],
    "Intermediate": [
        "Data Analysis", "SQL", "OOP", "API", "Git", "Debugging",
    ],
    "Advanced": [
        "Machine Learning", "Modeling", "Architecture", "Optimization", "MLOps", "Security",
    ],
}


def make_name(rng: random.Random, idx: int) -> str:
    # Create a reasonably natural Vietnamese full name (synthetic demo).
    surname = rng.choice(SURNAME)
    middle = rng.choice(MIDDLE)
    if idx % 2 == 0:
        given1 = rng.choice(GIVEN_FEMALE)
        # Sometimes use 2-syllable given name
        given2 = rng.choice(GIVEN_MALE) if rng.random() < 0.25 else ""
        if given2:
            return f"{surname} {middle} {given1} {given2}"
        return f"{surname} {middle} {given1}"
    else:
        given1 = rng.choice(GIVEN_MALE)
        given2 = rng.choice(GIVEN_FEMALE) if rng.random() < 0.25 else ""
        if given2:
            return f"{surname} {middle} {given1} {given2}"
        return f"{surname} {middle} {given1}"


def generate_demo_students(count: int, seed: int) -> None:
    """Generate synthetic demo students + enrollments + histories."""

    conn = psycopg2.connect(
        host="localhost",
        port=5432,
        dbname=os.environ.get("LMS_DB_NAME", "lms"),
        user="odoo",
        password="odoo",
    )
    conn.autocommit = False
    cur = conn.cursor()

    rng = random.Random(seed)
    today = datetime.date.today()

    # Level ids
    cur.execute("select id, name from lms_course_level")
    level_rows = cur.fetchall()
    level_id_by_name = {str(name): int(cid) for cid, name in level_rows}
    # Your expected names
    lvl_beginner = level_id_by_name.get("Beginner")
    lvl_intermediate = level_id_by_name.get("Intermediate")
    lvl_advanced = level_id_by_name.get("Advanced")
    if not all([lvl_beginner, lvl_intermediate, lvl_advanced]):
        raise RuntimeError("Missing required course levels in lms_course_level.")

    # Courses with at least 1 lesson
    cur.execute(
        """
        select c.id, c.level_id
        from lms_course c
        where c.is_active=true and c.state='published'
          and exists(select 1 from lms_lesson l where l.course_id=c.id)
        """
    )
    course_rows = cur.fetchall()
    if not course_rows:
        raise RuntimeError("No publishable courses with lessons found.")

    courses_by_level: Dict[int, List[int]] = {}
    for cid, level_id in course_rows:
        courses_by_level.setdefault(int(level_id), []).append(int(cid))

    # Lessons per course
    cur.execute("select id, course_id, sequence from lms_lesson")
    lesson_rows = cur.fetchall()
    lessons_by_course: Dict[int, List[int]] = {}
    for lid, course_id, seq in lesson_rows:
        lessons_by_course.setdefault(int(course_id), []).append((int(seq), int(lid)))
    # sort by sequence
    for course_id in list(lessons_by_course.keys()):
        lessons_by_course[course_id].sort(key=lambda x: x[0])
        lessons_by_course[course_id] = [lid for _, lid in lessons_by_course[course_id]]

    # Create students
    # Ensure email uniqueness: existing emails count
    cur.execute("select email from lms_student")
    existing_emails = {r[0] for r in cur.fetchall()}

    new_student_ids: List[int] = []
    created = 0

    def pick_level_name() -> str:
        # Distribution: 60% Beginner, 30% Intermediate, 10% Advanced
        x = rng.random()
        if x < 0.60:
            return "beginner"
        if x < 0.90:
            return "intermediate"
        return "advanced"

    level_to_id = {
        "beginner": lvl_beginner,
        "intermediate": lvl_intermediate,
        "advanced": lvl_advanced,
    }

    for i in range(count):
        level_key = pick_level_name()
        level_id = level_to_id[level_key]
        if level_id not in courses_by_level or not courses_by_level[level_id]:
            # Fallback: any course
            level_key = "beginner"
            level_id = lvl_beginner

        # Generate unique email
        email = ""
        tries = 0
        while not email or email in existing_emails:
            tries += 1
            email = f"demo.student.{len(existing_emails)+tries}.{i}@example.com"
            if tries > 10_000:
                raise RuntimeError("Could not generate unique emails.")

        name = make_name(rng, i)
        learning_goals = rng.choice(GOALS)
        desired = ", ".join(rng.sample(SKILLS.get(level_key.capitalize(), SKILLS["Beginner"]), k=3))

        cur.execute(
            """
            insert into lms_student
            (name, email, phone, current_level, learning_goals, desired_skills, is_active)
            values (%s,%s,%s,%s,%s,%s,%s)
            returning id
            """,
            (
                name,
                email,
                None,  # phone
                level_key,
                learning_goals,
                desired,
                True,
            ),
        )
        sid = int(cur.fetchone()[0])
        existing_emails.add(email)
        new_student_ids.append(sid)
        created += 1

        # Enroll each new student into 1 course to make the Kanban non-empty.
        candidate_courses = courses_by_level.get(level_id, [])
        if not candidate_courses:
            candidate_courses = [int(cid) for cid, _ in course_rows]
        course_id = rng.choice(candidate_courses)

        lesson_ids = lessons_by_course.get(course_id, [])
        if not lesson_ids:
            continue
        total_lessons = len(lesson_ids)
        # Make some students complete the course if only 1 lesson.
        if total_lessons == 1 or rng.random() < 0.30:
            completed_lessons = 1
        else:
            completed_lessons = 1

        status = "completed" if completed_lessons >= total_lessons else "in_progress"
        progress = 100.0 * float(completed_lessons) / float(total_lessons)
        enrollment_date = today - datetime.timedelta(days=rng.randint(0, 25))

        # Student course
        cur.execute(
            """
            insert into lms_student_course
            (student_id, course_id, status, enrollment_date, start_date, progress, final_score)
            values (%s,%s,%s,%s,%s,%s,%s)
            returning id
            """,
            (
                sid,
                course_id,
                status,
                enrollment_date,
                enrollment_date,
                progress,
                80.0 if status == "completed" else 50.0,
            ),
        )
        sc_id = int(cur.fetchone()[0])

        # Learning history for the first lesson only (keep light)
        lesson_id = int(lesson_ids[0])
        study_duration = float(rng.randint(20, 180)) / 60.0  # hours
        hist_date = datetime.datetime.now() - datetime.timedelta(days=rng.randint(0, 25), hours=rng.randint(0, 23))

        cur.execute(
            """
            insert into lms_learning_history
            (student_id, student_course_id, course_id, lesson_id, status, study_duration, notes, date)
            values (%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                sid,
                sc_id,
                course_id,
                lesson_id,
                "completed" if status == "completed" else "in_progress",
                study_duration,
                None,
                hist_date,
            ),
        )

    # Update stored fields for the whole dataset
    cur.execute(
        """
        with sc_stats as (
          select
            student_id,
            count(*)::int as total_courses,
            sum(case when status='completed' then 1 else 0 end)::int as completed_courses
          from lms_student_course
          group by student_id
        ),
        lh_stats as (
          select
            student_id,
            coalesce(sum(study_duration),0.0)::numeric as total_study_time,
            max(date)::date as last_activity_date
          from lms_learning_history
          group by student_id
        )
        update lms_student s
        set
          total_courses = coalesce(sc_stats.total_courses, 0),
          completed_courses = coalesce(sc_stats.completed_courses, 0),
          average_score = 0,
          total_study_time = coalesce(lh_stats.total_study_time, 0),
          last_activity_date = lh_stats.last_activity_date,
          inactive_days = case
              when lh_stats.last_activity_date is null then 0
              else (current_date - lh_stats.last_activity_date)::int
          end
        from sc_stats
        full join lh_stats
          on lh_stats.student_id = sc_stats.student_id
        where s.id = coalesce(sc_stats.student_id, lh_stats.student_id)
        """
    )

    # Update course enrolled count
    cur.execute(
        """
        update lms_course c
        set enrolled_students_count = sub.cnt
        from (
          select course_id, count(*)::int as cnt
          from lms_student_course
          group by course_id
        ) sub
        where c.id = sub.course_id
        """
    )

    conn.commit()
    cur.close()
    conn.close()

    print(f"Generated demo students: count={created}")


def beautify_demo_student_names() -> None:
    """Rename demo students to nicer Vietnamese names (hardcoded by email)."""
    EMAIL_TO_NEW_NAME = {
        "nguyenvana@example.com": "Nguyễn Hoàng Nam",
        "tranthib@example.com": "Trần Minh Thư",
        "levanc@example.com": "Lê Quốc Anh",
        "phamthid@example.com": "Phạm Nhật Linh",
        "hoangvane@example.com": "Hoàng Thu Huyền",
        "vothif@example.com": "Võ Thanh Tâm",
        "dangvang@example.com": "Đặng Gia Hân",
    }

    conn = psycopg2.connect(
        host="localhost",
        port=5432,
        dbname="lms",
        user="odoo",
        password="odoo",
    )
    cur = conn.cursor()

    updated = 0
    for email, new_name in EMAIL_TO_NEW_NAME.items():
        cur.execute(
            """
            update lms_student
            set name=%s
            where email=%s
            """,
            (new_name, email),
        )
        updated += cur.rowcount

    conn.commit()
    cur.close()
    conn.close()
    print(f"beautify done. updated_rows={updated}")


def add_two_lessons_to_reach_50() -> None:
    """Add 2 lessons to 2 courses that currently have exactly 1 lesson."""
    conn = psycopg2.connect(
        host="localhost",
        port=5432,
        dbname="lms",
        user="odoo",
        password="odoo",
    )
    cur = conn.cursor()

    cur.execute(
        """
        select course_id
        from lms_lesson
        group by course_id
        having count(*)=1
        order by course_id
        limit 2
        """
    )
    course_ids = [int(r[0]) for r in cur.fetchall()]
    print("picked_course_ids", course_ids)
    if len(course_ids) < 2:
        raise RuntimeError("Not enough courses with exactly 1 lesson to add 2 lessons.")

    cur.execute("select id, name from lms_course where id = any(%s)", (course_ids,))
    name_map = {int(r[0]): (r[1] or "") for r in cur.fetchall()}

    created = 0
    for cid in course_ids:
        cur.execute(
            """
            insert into lms_lesson (course_id, sequence, name, duration_minutes)
            values (%s,%s,%s,%s)
            """,
            (cid, 20, f"{name_map.get(cid,'Course')} - Lesson 2", 30),
        )
        created += 1

    # Update stored total_lessons in lms_course
    cur.execute(
        """
        update lms_course c
        set total_lessons = sub.cnt
        from (
          select course_id, count(*)::int as cnt
          from lms_lesson
          where course_id = any(%s)
          group by course_id
        ) sub
        where c.id = sub.course_id
        """,
        (course_ids,),
    )

    conn.commit()
    cur.close()
    conn.close()
    print("created_lessons", created)


def main() -> None:
    # Backward compatible mode:
    # - If user runs: python generate_demo_students_diverse.py --count 100 --seed 1
    #   we treat it as "generate".
    # - Otherwise, support subcommands: generate | beautify | add-two-lessons-to-reach-50
    old_parser = argparse.ArgumentParser(add_help=False)
    old_parser.add_argument("--count", type=int, default=300)
    old_parser.add_argument("--seed", type=int, default=42)

    if len(sys.argv) <= 1 or sys.argv[1].startswith("--"):
        args = old_parser.parse_args()
        generate_demo_students(args.count, args.seed)
        return

    cmd = sys.argv[1]
    if cmd == "generate":
        args = old_parser.parse_args(sys.argv[2:])
        generate_demo_students(args.count, args.seed)
    elif cmd == "beautify":
        beautify_demo_student_names()
    elif cmd == "add-two-lessons-to-reach-50":
        add_two_lessons_to_reach_50()
    else:
        raise SystemExit(f"Unknown command: {cmd}")


if __name__ == "__main__":
    main()

