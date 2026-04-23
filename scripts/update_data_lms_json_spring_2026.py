import json
import random
from datetime import date, datetime, timedelta
from pathlib import Path


START = date(2026, 3, 1)
END = date(2026, 5, 31)
SEED = 20260325


def rand_date(rng: random.Random, start: date = START, end: date = END) -> date:
    delta = (end - start).days
    return start + timedelta(days=rng.randint(0, delta))


def date_str(d: date) -> str:
    return d.strftime("%Y-%m-%d")


def normalize_date(value, rng: random.Random) -> str | None:
    if value is None:
        return None
    return date_str(rand_date(rng))


def normalize_datetime_string(value, rng: random.Random) -> str | None:
    if value is None:
        return None
    d = rand_date(rng)
    hh = rng.randint(7, 20)
    mm = rng.choice([0, 10, 15, 20, 30, 40, 45, 50])
    ss = rng.choice([0, 0, 0, 30])
    return f"{date_str(d)} {hh:02d}:{mm:02d}:{ss:02d}"


def next_id(items: list[dict]) -> int:
    return (max((int(x.get("id", 0)) for x in items), default=0) + 1)


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    json_path = root / "data" / "data_lms.json"
    data = json.loads(json_path.read_text(encoding="utf-8"))

    rng = random.Random(SEED)

    # 1) Normalize all existing dates to Spring 2026 (keep date logic consistent).
    for sc in data.get("student_courses", []):
        enroll = rand_date(rng)

        # keep nullability, but enforce ordering when present
        if sc.get("start_date") is None:
            start = None
        else:
            start = enroll + timedelta(days=rng.randint(0, 5))
            if start > END:
                start = END

        if sc.get("completion_date") is None:
            completion = None
        else:
            base = start if start is not None else enroll
            completion = base + timedelta(days=rng.randint(7, 40))
            if completion > END:
                completion = END

        sc["enrollment_date"] = date_str(enroll)
        sc["start_date"] = date_str(start) if start is not None else None
        sc["completion_date"] = date_str(completion) if completion is not None else None

    for lh in data.get("learning_histories", []):
        # file uses date string without time
        lh["date"] = normalize_date(lh.get("date"), rng)

    for rm in data.get("roadmaps", []):
        vf = rand_date(rng)
        vt = vf + timedelta(days=rng.randint(14, 90))
        if vt > END:
            vt = END
        rm["valid_from"] = date_str(vf)
        rm["valid_to"] = date_str(vt)

    # 2) Expand dataset: add more courses/lessons and related enrollments/histories/roadmaps.
    courses = data.setdefault("courses", [])
    lessons = data.setdefault("lessons", [])
    students = data.setdefault("students", [])
    student_courses = data.setdefault("student_courses", [])
    learning_histories = data.setdefault("learning_histories", [])
    roadmaps = data.setdefault("roadmaps", [])
    roadmap_courses = data.setdefault("roadmap_courses", [])

    # Build lookup sets
    existing_course_ids = {c["id"] for c in courses}
    existing_student_ids = {s["id"] for s in students}
    existing_lesson_ids = {l["id"] for l in lessons}
    existing_student_course_ids = {x["id"] for x in student_courses}
    existing_learning_history_ids = {x["id"] for x in learning_histories}
    existing_roadmap_ids = {x["id"] for x in roadmaps}
    existing_roadmap_course_ids = {x["id"] for x in roadmap_courses}

    cat_ids = [c["id"] for c in data.get("categories", [])]
    level_ids = [l["id"] for l in data.get("levels", [])]

    def make_unique_id(existing: set[int], start_from: int) -> int:
        i = start_from
        while i in existing:
            i += 1
        existing.add(i)
        return i

    # Add 20 new courses, each 6 lessons.
    base_course_id = next_id(courses)
    base_lesson_id = next_id(lessons)

    for idx in range(20):
        cid = make_unique_id(existing_course_ids, base_course_id + idx)
        course = {
            "id": cid,
            "name": f"Chuyên đề {cid}: Kỹ năng thực chiến ({idx + 1})",
            "description": "<p>Khóa học mở rộng để làm phong phú dữ liệu demo. Nội dung gồm nhiều bài học và bài kiểm tra để mô phỏng học tập thực tế.</p>",
            "category_id": rng.choice(cat_ids) if cat_ids else 1,
            "level_id": rng.choice(level_ids) if level_ids else 1,
            "duration_hours": int(rng.choice([12, 16, 18, 20, 24, 30, 36, 40])),
            "state": "published",
            "is_active": True,
            "average_rating": round(rng.uniform(4.0, 4.95), 1),
        }
        courses.append(course)

        for seq in range(1, 7):
            lid = make_unique_id(existing_lesson_ids, base_lesson_id)
            base_lesson_id += 1
            lesson = {
                "id": lid,
                "course_id": cid,
                "sequence": seq,
                "name": f"Bài {seq}: Nội dung {cid}.{seq}",
                "description": "<p>Bài học demo (mở rộng).</p>",
                "video_url": "",
                "duration_minutes": int(rng.choice([20, 25, 30, 35, 40, 45])),
            }
            lessons.append(lesson)

    # Add 30 new students
    base_student_id = next_id(students)
    for i in range(30):
        sid = make_unique_id(existing_student_ids, base_student_id + i)
        students.append(
            {
                "id": sid,
                "name": f"Sinh viên Demo {sid}",
                "email": f"demo.student.{sid}@example.com",
                "phone": "",
                "current_level": rng.choice(["beginner", "intermediate", "advanced"]),
                "learning_goals": "Mở rộng dữ liệu demo theo giai đoạn 03-05/2026.",
                "desired_skills": "Python, SQL, Web, AI",
                "is_active": True,
            }
        )

    # Enroll students into more courses + generate learning histories
    course_ids_all = [c["id"] for c in courses]
    lesson_ids_by_course: dict[int, list[int]] = {}
    for l in lessons:
        lesson_ids_by_course.setdefault(l["course_id"], []).append(l["id"])

    base_sc_id = next_id(student_courses)
    base_lh_id = next_id(learning_histories)

    for sid in [s["id"] for s in students]:
        # each student gets 3 enrollments
        for _ in range(3):
            cid = rng.choice(course_ids_all)
            scid = make_unique_id(existing_student_course_ids, base_sc_id)
            base_sc_id += 1
            enroll_d = rand_date(rng)
            start_d = enroll_d + timedelta(days=rng.randint(0, 5))
            if start_d > END:
                start_d = END
            status = rng.choice(["enrolled", "in_progress", "completed"])
            completion_d = None
            progress = 0.0
            final_score = None
            if status == "enrolled":
                progress = float(rng.choice([0, 5, 10, 15]))
            elif status == "in_progress":
                progress = float(rng.choice([20, 35, 50, 65, 80]))
            else:
                completion_d = start_d + timedelta(days=rng.randint(7, 30))
                if completion_d > END:
                    completion_d = END
                progress = 100.0
                final_score = float(rng.randint(60, 95))

            student_courses.append(
                {
                    "id": scid,
                    "student_id": sid,
                    "course_id": cid,
                    "enrollment_date": date_str(enroll_d),
                    "start_date": date_str(start_d),
                    "completion_date": date_str(completion_d) if completion_d else None,
                    "status": status,
                    "progress": progress,
                    "final_score": final_score,
                }
            )

            # learning history: 2-5 entries per enrollment
            lesson_ids = lesson_ids_by_course.get(cid) or []
            if not lesson_ids:
                continue
            for _h in range(rng.randint(2, 5)):
                lhid = make_unique_id(existing_learning_history_ids, base_lh_id)
                base_lh_id += 1
                lid = rng.choice(lesson_ids)
                hist_d = rand_date(rng, start=enroll_d, end=END)
                learning_histories.append(
                    {
                        "id": lhid,
                        "student_id": sid,
                        "student_course_id": scid,
                        "course_id": cid,
                        "lesson_id": lid,
                        "date": date_str(hist_d),
                        "study_duration": round(rng.uniform(0.2, 2.5), 2),
                        "status": rng.choice(["started", "in_progress", "completed"]),
                        "notes": "",
                    }
                )

    # Roadmaps: add more and keep valid dates in range; reviewed_by is ignored by import anyway.
    base_rm_id = next_id(roadmaps)
    base_rmc_id = next_id(roadmap_courses)

    student_ids_all = [s["id"] for s in students]
    for i in range(30):
        rmid = make_unique_id(existing_roadmap_ids, base_rm_id + i)
        sid = rng.choice(student_ids_all)
        vf = rand_date(rng)
        vt = vf + timedelta(days=rng.randint(14, 90))
        if vt > END:
            vt = END
        roadmaps.append(
            {
                "id": rmid,
                "student_id": sid,
                "valid_from": date_str(vf),
                "valid_to": date_str(vt),
                "state": rng.choice(["draft", "suggested", "approved", "locked", "rejected"]),
                "reviewed_by": None,
                "ai_recommendation_reason": "Dữ liệu demo mở rộng.",
                "recommendation_method": rng.choice(["content_based", "rule_based", "hybrid"]),
            }
        )
        # 2-4 roadmap courses
        for _ in range(rng.randint(2, 4)):
            rmcid = make_unique_id(existing_roadmap_course_ids, base_rmc_id)
            base_rmc_id += 1
            roadmap_courses.append(
                {
                    "id": rmcid,
                    "roadmap_id": rmid,
                    "course_id": rng.choice(course_ids_all),
                    "sequence": rng.randint(1, 20),
                    "priority": rng.choice(["high", "medium", "low"]),
                    "timeframe": rng.choice(["short", "medium", "long"]),
                    "status": rng.choice(["pending", "in_progress", "completed", "skipped"]),
                    "recommendation_reason": "Demo",
                    "similarity_score": round(rng.uniform(30.0, 99.0), 2),
                }
            )

    # Write back (keep structure; only values changed + added records)
    json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Updated", json_path)


if __name__ == "__main__":
    main()

