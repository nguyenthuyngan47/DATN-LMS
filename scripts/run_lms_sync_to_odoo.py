#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Chạy pipeline: crawl dữ liệu → import trực tiếp vào Odoo (XML-RPC).

Biến môi trường bắt buộc: ODOO_DB, ODOO_PASSWORD
Tùy chọn: ODOO_URL (http://localhost:8069), ODOO_LOGIN (admin)

  python run_lms_sync_to_odoo.py
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent


def main() -> int:
    db = os.environ.get("ODOO_DB")
    pwd = os.environ.get("ODOO_PASSWORD")
    if not db or not pwd:
        print("Thiếu ODOO_DB hoặc ODOO_PASSWORD trong môi trường.", file=sys.stderr)
        return 1
    subprocess.check_call([sys.executable, str(SCRIPTS / "crawl_lms_data.py")])
    cmd = [
        sys.executable,
        str(SCRIPTS / "odoo_import_crawl.py"),
        "--db",
        db,
    ]
    if os.environ.get("ODOO_URL"):
        cmd.extend(["--url", os.environ["ODOO_URL"]])
    if os.environ.get("ODOO_LOGIN"):
        cmd.extend(["--login", os.environ["ODOO_LOGIN"]])
    if os.environ.get("ODOO_PASSWORD"):
        cmd.extend(["--password", os.environ["ODOO_PASSWORD"]])
    subprocess.check_call(cmd)
    return 0


if __name__ == "__main__":
    sys.exit(main())
