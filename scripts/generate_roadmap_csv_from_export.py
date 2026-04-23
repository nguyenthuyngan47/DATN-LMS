# -*- coding: utf-8 -*-
"""Sinh lms_roadmap.csv + lms_roadmap_course.csv từ lms_student.csv / lms_course.csv đã có (không đụng file khác)."""
from __future__ import annotations

import csv
from pathlib import Path

EXPORT_DIR = Path(__file__).resolve().parent / "export"


def main() -> None:
    st_path = EXPORT_DIR / "lms_student.csv"
    co_path = EXPORT_DIR / "lms_course.csv"
    with st_path.open(encoding="utf-8-sig", newline="") as f:
        students = list(csv.DictReader(f))
    with co_path.open(encoding="utf-8-sig", newline="") as f:
        courses = list(csv.DictReader(f))

    n_st = len(students)
    n_co = len(courses)
    st_ids = sorted(int(r["id"]) for r in students)
    co_ids = sorted(int(r["id"]) for r in courses)
    if st_ids != list(range(1, n_st + 1)):
        raise SystemExit(f"lms_student.csv id không liên tục 1..N: {st_ids[:8]}...")
    if co_ids != list(range(1, n_co + 1)):
        raise SystemExit(f"lms_course.csv id không liên tục 1..N: {co_ids[:8]}...")

    roadmap_states = ["draft", "suggested", "approved", "locked", "rejected"]
    methods = ["content_based", "rule_based", "hybrid"]
    priorities = ["high", "medium", "low"]
    timeframes = ["short", "medium", "long"]
    line_statuses = ["pending", "in_progress", "completed", "skipped"]

    rm_path = EXPORT_DIR / "lms_roadmap.csv"
    rmc_path = EXPORT_DIR / "lms_roadmap_course.csv"

    with rm_path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(
            f,
            fieldnames=[
                "id",
                "student_id",
                "valid_from",
                "valid_to",
                "state",
                "reviewed_by",
                "ai_recommendation_reason",
                "recommendation_method",
            ],
        )
        w.writeheader()
        for i in range(n_st):
            sid = i + 1
            w.writerow(
                {
                    "id": str(sid),
                    "student_id": str(sid),
                    "valid_from": "2026-04-01",
                    "valid_to": "2026-05-31",
                    "state": roadmap_states[i % len(roadmap_states)],
                    "reviewed_by": "",
                    "ai_recommendation_reason": (
                        f"Roadmap demo khớp dataset export (student/course id 1..{n_st})."
                    ),
                    "recommendation_method": methods[i % len(methods)],
                }
            )

    rc_id = 1
    with rmc_path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(
            f,
            fieldnames=[
                "id",
                "roadmap_id",
                "course_id",
                "sequence",
                "priority",
                "timeframe",
                "status",
                "recommendation_reason",
                "similarity_score",
            ],
        )
        w.writeheader()
        for i in range(n_st):
            rid = i + 1
            for j in range(3):
                cid = ((i + j * 11) % n_co) + 1
                w.writerow(
                    {
                        "id": str(rc_id),
                        "roadmap_id": str(rid),
                        "course_id": str(cid),
                        "sequence": str((j + 1) * 10),
                        "priority": priorities[(i + j) % len(priorities)],
                        "timeframe": timeframes[(i + j) % len(timeframes)],
                        "status": line_statuses[(i + j) % len(line_statuses)],
                        "recommendation_reason": f"Gợi ý khóa #{cid} cho roadmap {rid}",
                        "similarity_score": f"{55.0 + ((i + j * 7) % 40):.2f}",
                    }
                )
                rc_id += 1

    print(f"Wrote {rm_path} ({n_st} rows + header)")
    print(f"Wrote {rmc_path} ({rc_id - 1} rows + header)")


if __name__ == "__main__":
    main()
