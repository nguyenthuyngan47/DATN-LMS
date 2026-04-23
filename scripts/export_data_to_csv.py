#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Export dữ liệu từ Database (PostgreSQL / MySQL / SQL Server) hoặc API sang CSV.

Tính năng:
- Cấu hình qua file JSON (host, port, user, password, database, datasets).
- Nhiều bảng / nhiều endpoint → mỗi dataset một file CSV.
- Lọc theo điều kiện, truy vấn tùy chỉnh (JOIN), batch/chunk cho dữ liệu lớn.
- Chuẩn hóa null, UTF-8, validate trước khi ghi file.
- Logging và xử lý lỗi.

Chạy:
  python export_data_to_csv.py --config data_export_config.json

Với LMS: python crawl_lms_data.py tạo lms_export_demo.db (crawl API); sau đó export ra scripts/export/*.csv.

Yêu cầu gói: pandas, sqlalchemy, requests
Driver DB tùy loại: psycopg2 (PostgreSQL), pymysql (MySQL), pyodbc (SQL Server), ...
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any, Iterator, Optional

import pandas as pd
import requests
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

LOG_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
logger = logging.getLogger("data_export")


# ---------------------------------------------------------------------------
# Cấu hình & kết nối
# ---------------------------------------------------------------------------


def load_config(path: Path) -> dict[str, Any]:
    """Đọc file JSON cấu hình."""
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def build_sqlalchemy_url(conn: dict[str, Any], config_path: Optional[Path] = None) -> str:
    """
    Tạo URL kết nối SQLAlchemy từ block connection.

    type: sqlite | postgresql | mysql | mssql
    Với sqlite: `database` là đường dẫn file .db (tương đối theo thư mục chứa file cấu hình nếu không tuyệt đối).
    """
    ctype = (conn.get("type") or "postgresql").lower()

    if ctype == "sqlite":
        raw = conn.get("database") or conn.get("path")
        if not raw:
            raise ValueError("SQLite cần trường 'database' (đường dẫn file .db).")
        p = Path(raw)
        if not p.is_absolute():
            base = config_path.parent if config_path else Path.cwd()
            p = (base / p).resolve()
        else:
            p = p.resolve()
        p.parent.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{p.as_posix()}"

    user = conn["username"]
    password = conn.get("password") or ""
    host = conn["host"]
    port = conn.get("port")
    database = conn["database"]
    extra = conn.get("extra_params") or {}

    from urllib.parse import quote_plus

    user_enc = quote_plus(str(user))
    pwd_enc = quote_plus(str(password))

    if ctype == "postgresql":
        driver = "postgresql+psycopg2"
    elif ctype in ("mysql", "mariadb"):
        driver = "mysql+pymysql"
    elif ctype in ("mssql", "sqlserver"):
        # Cần ODBC Driver và connection string chi tiết có thể đặt trong extra_params
        driver = "mssql+pyodbc"
    else:
        raise ValueError(f"Loại DB không hỗ trợ: {ctype}")

    port_part = f":{port}" if port is not None else ""

    if ctype == "mssql":
        # ODBC: dùng tham số query (driver) nếu có
        odbc_extra = ";".join(f"{k}={v}" for k, v in extra.items()) if extra else "Driver=ODBC Driver 17 for SQL Server"
        odbc = quote_plus(odbc_extra)
        return f"{driver}://{user_enc}:{pwd_enc}@{host}{port_part}/{database}?odbc_connect={odbc}"

    base = f"{driver}://{user_enc}:{pwd_enc}@{host}{port_part}/{database}"
    if extra:
        from urllib.parse import urlencode

        base = f"{base}?{urlencode(extra)}"
    return base


def create_db_engine(conn: dict[str, Any], config_path: Optional[Path] = None) -> Engine:
    """Tạo SQLAlchemy Engine."""
    url = build_sqlalchemy_url(conn, config_path=config_path)
    # pool_pre_ping: kiểm tra kết nối trước khi dùng (SQLite bỏ qua pool phức tạp)
    return create_engine(url, pool_pre_ping=True, future=True)


# ---------------------------------------------------------------------------
# Truy vấn SQL có điều kiện & chunk
# ---------------------------------------------------------------------------


class ValueFilterError(ValueError):
    """Lỗi cấu hình filter."""


def _build_where_and_params(
    table: str,
    columns: Optional[list[str]],
    filters: dict[str, Any],
    _date_format: str,
) -> tuple[str, dict[str, Any]]:
    """
    Xây SELECT ... FROM table WHERE ...
    Hỗ trợ suffix: __gte, __lte, __gt, __lt, __ne (không dùng __in; dùng trường query tùy chỉnh).
    """
    cols = ", ".join(f'"{c}"' if not c.isidentifier() else c for c in columns) if columns else "*"
    params: dict[str, Any] = {}
    clauses: list[str] = []
    for i, (key, val) in enumerate((filters or {}).items()):
        if "__" in key:
            field, op = key.rsplit("__", 1)
        else:
            field, op = key, "eq"

        pname = f"p{i}"
        if op == "eq":
            clauses.append(f"{field} = :{pname}")
            params[pname] = val
        elif op == "ne":
            clauses.append(f"{field} != :{pname}")
            params[pname] = val
        elif op == "gte":
            clauses.append(f"{field} >= :{pname}")
            params[pname] = val
        elif op == "lte":
            clauses.append(f"{field} <= :{pname}")
            params[pname] = val
        elif op == "gt":
            clauses.append(f"{field} > :{pname}")
            params[pname] = val
        elif op == "lt":
            clauses.append(f"{field} < :{pname}")
            params[pname] = val
        elif op == "in":
            raise ValueFilterError(
                "Dùng trường 'query' trong dataset với SQL IN (...) thay cho filter __in."
            )
        else:
            raise ValueFilterError(f"Toán tử lọc không hỗ trợ: {op}")

    where_sql = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = f"SELECT {cols} FROM {table}{where_sql}"
    return sql, params


def read_sql_chunks(
    engine: Engine,
    sql: str,
    params: Optional[dict[str, Any]],
    chunk_size: int,
) -> Iterator[pd.DataFrame]:
    """Đọc kết quả SQL theo chunk (pandas read_sql với chunksize)."""
    with engine.connect() as conn:
        stmt = text(sql)
        bind = dict(params or {})
        for chunk in pd.read_sql(stmt, conn, params=bind, chunksize=chunk_size):
            yield chunk


# ---------------------------------------------------------------------------
# API + pagination
# ---------------------------------------------------------------------------


def _get_nested(obj: Any, path: str) -> Any:
    """Lấy giá trị theo path dạng 'a.b.c'."""
    cur = obj
    for part in path.split("."):
        if cur is None:
            return None
        cur = cur[part] if isinstance(cur, dict) else getattr(cur, part, None)
    return cur


def fetch_api_paginated(
    base_url: str,
    headers: dict[str, str],
    endpoint: str,
    method: str,
    params: dict[str, Any],
    pagination: dict[str, Any],
    timeout: float,
) -> list[dict[str, Any]]:
    """
    Thu thập toàn bộ item từ API (offset hoặc page).

    pagination.type: 'offset' | 'page'
    """
    ptype = pagination.get("type", "page")
    items_path = pagination.get("items_path", "data")
    total_pages_path = pagination.get("total_pages_path")
    page_param = pagination.get("page_param", "page")
    offset_param = pagination.get("offset_param", "offset")
    page_size_param = pagination.get("page_size_param", "page_size")

    all_rows: list[dict[str, Any]] = []
    page = 1
    offset = 0
    page_size = int(params.get(page_size_param, pagination.get("default_page_size", 100)))

    while True:
        q = dict(params)
        if ptype == "offset":
            q[offset_param] = offset
            q[page_size_param] = page_size
        else:
            q[page_param] = page
            q[page_size_param] = page_size

        url = base_url.rstrip("/") + "/" + endpoint.lstrip("/")
        try:
            r = requests.request(method.upper(), url, headers=headers, params=q, timeout=timeout)
            r.raise_for_status()
            data = r.json()
        except requests.RequestException as e:
            logger.exception("Lỗi gọi API: %s", url)
            raise

        items = _get_nested(data, items_path)
        if items is None:
            items = data if isinstance(data, list) else []

        if isinstance(items, list):
            if not items:
                break
            all_rows.extend(items)
        else:
            logger.warning("items_path không trả về list, dừng pagination")
            break

        if total_pages_path:
            total_pages = _get_nested(data, total_pages_path)
            if total_pages is not None and page >= int(total_pages):
                break

        if ptype == "offset":
            if len(items) < page_size:
                break
            offset += page_size
        else:
            if len(items) < page_size:
                break
            page += 1

    return all_rows


# ---------------------------------------------------------------------------
# Validate & làm sạch
# ---------------------------------------------------------------------------


def validate_and_clean_df(
    df: pd.DataFrame,
    validation: dict[str, Any],
    date_format: str,
) -> pd.DataFrame:
    """
    - Bỏ dòng thiếu cột bắt buộc (required_columns).
    - drop_duplicates theo subset nếu có.
    - Chuẩn hóa NaN → chuỗi rỗng cho export CSV (có thể đổi sang 'NULL').
    """
    if df.empty:
        return df

    out = df.copy()
    req = validation.get("required_columns") or []
    for col in req:
        if col not in out.columns:
            logger.warning("Cột bắt buộc không tồn tại trong dữ liệu: %s", col)
            continue
        out = out[out[col].notna() & (out[col].astype(str).str.strip() != "")]

    subset = validation.get("drop_duplicates_subset")
    if subset:
        out = out.drop_duplicates(subset=[c for c in subset if c in out.columns])

    # Chuẩn hóa datetime sang chuỗi ổn định
    for col in out.columns:
        if pd.api.types.is_datetime64_any_dtype(out[col]):
            out[col] = pd.to_datetime(out[col], errors="coerce").dt.strftime(date_format)

    # Null → rỗng (CSV sạch; hệ thống downstream có thể map lại)
    out = out.where(pd.notnull(out), None)
    return out


def join_dataframes(
    left: pd.DataFrame,
    right: pd.DataFrame,
    on: str | list[str],
    how: str = "inner",
    suffixes: tuple[str, str] = ("_x", "_y"),
) -> pd.DataFrame:
    """
    Join hai DataFrame (khi không muốn JOIN trong SQL — ví dụ sau khi đọc từ CSV).
    how: inner | left | right | outer
    """
    return pd.merge(left, right, on=on, how=how, suffixes=suffixes)


def dataframe_to_csv_safe(
    df: pd.DataFrame,
    path: Path,
    encoding: str = "utf-8",
) -> None:
    """Ghi CSV UTF-8 với BOM tùy chọn (Excel): utf-8-sig nếu cần mở bằng Excel."""
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(
        path,
        index=False,
        encoding=encoding,
        na_rep="",
        lineterminator="\n",
    )


# ---------------------------------------------------------------------------
# Export một dataset (database)
# ---------------------------------------------------------------------------


def export_database_dataset(
    engine: Engine,
    ds: dict[str, Any],
    export_opts: dict[str, Any],
    config_path: Optional[Path] = None,
) -> Path:
    """Xuất một dataset từ DB ra CSV."""
    name = ds["name"]
    chunk_size = int(export_opts.get("chunk_size", 10000))
    encoding = export_opts.get("csv_encoding", "utf-8")
    date_format = export_opts.get("date_format", "%Y-%m-%d")
    out_dir = Path(export_opts["output_dir"])
    if not out_dir.is_absolute() and config_path:
        out_dir = (config_path.parent / out_dir).resolve()
    else:
        out_dir = out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    reject_empty = bool(export_opts.get("reject_empty_output", False))
    validation = ds.get("validation") or {}

    logger.info("Đang lấy dữ liệu dataset: %s (bảng/query đã cấu hình)", name)

    if ds.get("query"):
        sql = ds["query"]
        params = dict(ds.get("query_params") or {})
    else:
        sql, params = _build_where_and_params(
            ds["table"],
            ds.get("columns"),
            ds.get("filters") or {},
            date_format,
        )
        order = ds.get("order_by")
        if order:
            sql = f"{sql} ORDER BY {order}"

    outfile = out_dir / f"{name}.csv"
    first = True
    total_rows = 0

    try:
        for chunk in read_sql_chunks(engine, sql, params if params else None, chunk_size):
            chunk = validate_and_clean_df(chunk, validation, date_format)
            if chunk.empty and first:
                logger.info("Dataset %s: chunk đầu rỗng sau validate (vẫn ghi header nếu có cột).", name)
            mode = "w" if first else "a"
            header = first
            chunk.to_csv(
                outfile,
                mode=mode,
                header=header,
                index=False,
                encoding=encoding,
                na_rep="",
                lineterminator="\n",
            )
            total_rows += len(chunk)
            first = False
        # Iterator không sinh chunk nào (một số phiên bản pandas với kết quả rỗng)
        if first:
            fallback_cols = ds.get("columns") or []
            pd.DataFrame(columns=fallback_cols).to_csv(
                outfile,
                index=False,
                encoding=encoding,
                na_rep="",
                lineterminator="\n",
            )
            logger.warning(
                "Dataset %s: không có chunk từ DB; tạo file CSV với cột từ cấu hình (%s cột).",
                name,
                len(fallback_cols),
            )
    except SQLAlchemyError:
        logger.exception("Lỗi SQL khi export dataset: %s", name)
        raise

    if reject_empty and total_rows == 0:
        if outfile.is_file():
            outfile.unlink()
        raise ValueError(
            f"Dataset '{name}' không có bản ghi hợp lệ sau validate (reject_empty_output=true)."
        )

    logger.info("Hoàn tất %s: %s bản ghi đã ghi → %s", name, total_rows, outfile.resolve())
    return outfile


def export_api_dataset(
    api_cfg: dict[str, Any],
    ds: dict[str, Any],
    export_opts: dict[str, Any],
    config_path: Optional[Path] = None,
) -> Path:
    """Xuất dataset từ API."""
    name = ds["name"]
    encoding = export_opts.get("csv_encoding", "utf-8")
    date_format = export_opts.get("date_format", "%Y-%m-%d")
    out_dir = Path(export_opts["output_dir"])
    if not out_dir.is_absolute() and config_path:
        out_dir = (config_path.parent / out_dir).resolve()
    else:
        out_dir = out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    validation = ds.get("validation") or {}

    logger.info("Đang gọi API cho dataset: %s", name)

    base = api_cfg["base_url"]
    headers = dict(api_cfg.get("headers") or {})
    timeout = float(api_cfg.get("timeout_seconds", 60))
    endpoint = ds["endpoint"]
    method = ds.get("method", "GET")
    params = dict(ds.get("params") or {})
    pagination = dict(ds.get("pagination") or {"type": "page", "items_path": "data"})

    rows = fetch_api_paginated(base, headers, endpoint, method, params, pagination, timeout)
    df = pd.DataFrame(rows)
    df = validate_and_clean_df(df, validation, date_format)
    outfile = out_dir / f"{name}.csv"
    reject_empty = bool(export_opts.get("reject_empty_output", False))
    if reject_empty and len(df) == 0:
        raise ValueError(f"Dataset API '{name}' không có dữ liệu (reject_empty_output=true).")
    dataframe_to_csv_safe(df, outfile, encoding=encoding)
    logger.info("Hoàn tất %s: %s bản ghi → %s", name, len(df), outfile.resolve())
    return outfile


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def run_export(config_path: Path) -> list[Path]:
    """Chạy export theo cấu hình; trả về danh sách file CSV đã tạo."""
    cfg = load_config(config_path)
    export_opts = cfg.get("export") or {}
    export_opts.setdefault("output_dir", "./export_output")
    export_opts.setdefault("chunk_size", 10000)
    export_opts.setdefault("csv_encoding", "utf-8")
    export_opts.setdefault("date_format", "%Y-%m-%d")

    datasets = cfg.get("datasets") or []
    if not datasets:
        logger.warning("Không có dataset nào trong cấu hình.")

    engine: Optional[Engine] = None
    conn_block = cfg.get("connection")
    api_block = cfg.get("api") or {}
    written: list[Path] = []

    try:
        for ds in datasets:
            src = (ds.get("source") or "database").lower()
            if src == "database":
                if engine is None:
                    if not conn_block:
                        raise ValueError("Thiếu 'connection' cho dataset database.")
                    engine = create_db_engine(conn_block, config_path=config_path.resolve())
                written.append(
                    export_database_dataset(engine, ds, export_opts, config_path=config_path.resolve())
                )
            elif src == "api":
                written.append(
                    export_api_dataset(api_block, ds, export_opts, config_path=config_path.resolve())
                )
            else:
                raise ValueError(f"source không hợp lệ: {src}")

    finally:
        if engine is not None:
            engine.dispose()
            logger.info("Đã đóng kết nối database.")

    return written


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8")
            except (OSError, ValueError):
                pass
    parser = argparse.ArgumentParser(description="Export dữ liệu ra CSV (DB/API).")
    parser.add_argument(
        "--config",
        "-c",
        type=Path,
        default=Path(__file__).resolve().parent / "data_export_config.json",
        help="Đường dẫn file JSON cấu hình",
    )
    args = parser.parse_args()

    if not args.config.is_file():
        logger.error("Không tìm thấy file cấu hình: %s", args.config)
        logger.info("Tạo file data_export_config.json (xem data_export_config.json hiện có trong scripts).")
        return 1

    try:
        paths = run_export(args.config)
        print("\n--- Export OK ---")
        for p in paths:
            print(f"  - {p}")
        print(f"Total files: {len(paths)}")
    except Exception:
        logger.exception("Export thất bại.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
