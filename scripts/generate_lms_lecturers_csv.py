#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Sinh file ``lms_lecturer.csv`` (50 bản ghi) tương thích import LMS.

Đầu ra mặc định: ``scripts/export/lms_lecturer.csv`` (cùng thư mục với các CSV khác).

Import vào hệ thống:
- Khi Odoo chạy đồng bộ CSV (``lms.csv.registry.hook`` / ``csv_runtime_sync``), file
  ``lms_lecturer.csv`` được đọc sau khi import khóa học/sinh viên; tạo ``res.users``
  + ``lms.lecturer`` và phân bổ ``lms.course.instructor_id`` luân phiên.

Chạy:
  python scripts/generate_lms_lecturers_csv.py
  python scripts/generate_lms_lecturers_csv.py --out D:/path/lms_lecturer.csv
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
DEFAULT_OUT = SCRIPTS_DIR / "export" / "lms_lecturer.csv"

# Bộ dữ liệu gốc — xoay vòng để có 50 dòng đa dạng, khớp domain LMS (CNTT / Data / Web / Mobile).
FACULTIES = [
    "Khoa Công nghệ thông tin",
    "Khoa Khoa học dữ liệu & AI",
    "Khoa Công nghệ phần mềm",
    "Khoa Hệ thống thông tin",
    "Khoa An toàn thông tin",
    "Khoa Phát triển Web & Mobile",
]
DEPTS = [
    "Bộ môn Lập trình",
    "Bộ môn Cơ sở dữ liệu",
    "Bộ môn Trí tuệ nhân tạo",
    "Bộ môn Mạng & DevOps",
    "Bộ môn Phát triển ứng dụng",
    "Bộ môn Phân tích dữ liệu",
]
SPECS = [
    "Machine Learning",
    "Web Full-stack",
    "Mobile Flutter",
    "Cloud & Kubernetes",
    "Backend Python/Java",
    "Data Engineering",
    "Cybersecurity",
    "UI/UX Engineering",
]
DEGREES = ["ThS.", "TS.", "PGS.", "GS.", "ThS. Kiện tướng sư"]
SUBJECTS = [
    "Python, SQL, thuật toán",
    "Deep Learning, NLP, Computer Vision",
    "React, Node.js, API design",
    "AWS/Azure, CI/CD, Docker",
    "Statistics, ETL, Big Data",
    "OWASP, mã hóa, pentest cơ bản",
]
CERTS = [
    "AWS SAA; Google Data Analytics",
    "TensorFlow Developer Certificate",
    "CKA (Kubernetes)",
    "Oracle Java SE Professional",
    "Microsoft Azure Fundamentals",
    "",
]
FIRST = [
    "Nguyễn", "Trần", "Lê", "Phạm", "Hoàng", "Huỳnh", "Võ", "Đặng", "Bùi", "Đỗ",
    "Ngô", "Dương", "Lý", "Mai", "Đinh", "Vũ", "Tôn", "Thái", "Hồ", "Quách",
]
LAST = [
    "An", "Bình", "Chi", "Dũng", "Giang", "Hải", "Hương", "Khoa", "Lan", "Minh",
    "Nam", "Oanh", "Phúc", "Quang", "Sơn", "Thảo", "Tuấn", "Uyên", "Việt", "Yến",
    "Đức", "Hạnh", "Kiên", "Linh", "Mạnh", "Nga", "Phong", "Quyên", "Tài", "Vân",
]


def build_rows(count: int = 50) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    genders = ("male", "female")
    levels = ("beginner", "intermediate", "advanced", "expert")
    types = ("online", "offline", "hybrid")

    for i in range(1, count + 1):
        login = f"lecturer{i:02d}"
        fn = FIRST[i % len(FIRST)]
        ln = LAST[i % len(LAST)]
        full_name = f"{fn} {ln}"
        email = f"{login}@lms.training.local"
        faculty = FACULTIES[i % len(FACULTIES)]
        dept = DEPTS[i % len(DEPTS)]
        spec = SPECS[i % len(SPECS)]
        degree = DEGREES[i % len(DEGREES)]
        subj = SUBJECTS[i % len(SUBJECTS)]
        cert = CERTS[i % len(CERTS)]
        yoe = str(3 + (i % 18))
        yob = 1978 + (i % 25)
        mob = 1 + (i % 12)
        dob = f"{yob}-{mob:02d}-15"
        gender = genders[i % 2]
        tlevel = levels[i % 4]
        ttype = types[i % 3]
        addr = f"Số {100 + i}, đường LMS, Q.{(i % 12) + 1}, TP.HCM"

        rows.append(
            {
                "id": str(i),
                "login": login,
                "password": "Lecturer@123",
                "full_name": full_name,
                "email": email,
                "phone": f"090{1000000 + i:07d}"[-10:],
                "gender": gender,
                "date_of_birth": dob,
                "address": addr,
                "department": dept,
                "specialization": spec,
                "academic_degree": degree,
                "years_of_experience": yoe,
                "faculty": faculty,
                "subject_expertise": subj,
                "certifications": cert,
                "teaching_level": tlevel,
                "teaching_type": ttype,
                "avatar_url": "",
                "active": "1",
            }
        )
    return rows


def main() -> int:
    p = argparse.ArgumentParser(description="Generate lms_lecturer.csv (50 rows).")
    p.add_argument("--out", type=Path, default=DEFAULT_OUT, help="Output CSV path")
    p.add_argument("--count", type=int, default=50, help="Number of rows (default 50)")
    args = p.parse_args()

    out: Path = args.out
    out.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = [
        "id",
        "login",
        "password",
        "full_name",
        "email",
        "phone",
        "gender",
        "date_of_birth",
        "address",
        "department",
        "specialization",
        "academic_degree",
        "years_of_experience",
        "faculty",
        "subject_expertise",
        "certifications",
        "teaching_level",
        "teaching_type",
        "avatar_url",
        "active",
    ]
    rows = build_rows(min(500, max(1, args.count)))

    with out.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    print(f"Wrote {len(rows)} rows to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
