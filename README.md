# DATN — Learning Management System (LMS)

Module **Odoo 18** quản lý học tập: khóa học, giảng viên, sinh viên, lịch, lịch sử học tập và tích hợp AI (đề xuất / chat roadmap). Đây là phần addon `lms` trong kho mã nguồn LMS.

## Tính năng chính

- Quản lý **sinh viên**, **giảng viên**, **khóa học**, **roadmap** và **lịch sử học tập**
- **Đăng ký / portal** mở rộng (tham số signup LMS, tài liệu giảng viên)
- **AI**: phân tích / chat (Groq), tùy chọn Gemini qua cấu hình hệ thống hoặc biến môi trường
- **Google Calendar** (tùy chọn): service account hoặc OAuth — cấu hình qua biến môi trường
- **Đồng bộ dữ liệu CSV** (bootstrap, cron, hook) — thư mục dữ liệu có thể cấu hình qua `LMS_CSV_IMPORT_DIR` và các biến `LMS_CSV_*`

Phụ thuộc Odoo chuẩn: `base`, `base_setup`, `mail`, `portal`, `calendar`, `web`, `auth_signup`.

## Yêu cầu

- **Odoo 18** (Docker hoặc cài đặt thủ công)
- Python (theo Odoo): các gói bổ sung khai báo trong manifest — `requests`, `python-dotenv`, `google-auth`
- File **`.env`** ở thư mục gốc repo LMS (cùng cấp với thư mục `lms/`) hoặc đường dẫn tùy chỉnh qua `LMS_ENV_FILE`. Module **bắt buộc** tìm thấy `.env` khi khởi động (xem `lms/tools/env_loader.py`).

## Chạy nhanh bằng Docker

Từ thư mục `LMS/` (chứa `docker-compose.yml`):

1. Tạo và điền file `.env` (Groq và các biến liên quan AI — xem mục dưới). Với Docker, compose đã mount `./.env` vào `/mnt/extra-addons/.env` để `load_dotenv` hoạt động trong container.
2. Khởi chạy:

```bash
docker compose up -d --build
```

- Ứng dụng Odoo: **http://localhost:8069**
- Adminer (nếu bật trong compose): **http://localhost:8080**

**Lưu ý:** Dùng `docker compose down` khi tắt stack. Tránh `docker compose down -v` nếu không muốn xóa volume cơ sở dữ liệu.

Điều chỉnh volume khóa Google Calendar trong `docker-compose.yml` cho đúng file JSON service account trên máy bạn (hoặc bỏ mount nếu không dùng).

## Cài addon vào Odoo có sẵn

1. Sao chép (hoặc symlink) cả thư mục `lms` vào `addons` của instance Odoo 18.
2. Cài Python: `pip install requests python-dotenv google-auth` (trong môi trường Odoo).
3. Đặt `.env` tại thư mục cha của addon `lms` (repo LMS) hoặc set `LMS_ENV_FILE`.
4. Khởi động Odoo, bật chế độ developer, **Cập nhật danh sách ứng dụng**, cài **Learning Management System (LMS)**.

## Biến môi trường quan trọng

| Nhóm | Ví dụ |
|------|--------|
| **Bắt buộc cho AI Groq** | `GROQ_API_KEY`, `GROQ_MODEL`, `GROQ_CHAT_URL`, `GROQ_MAX_MESSAGES`, `GROQ_MAX_MESSAGE_CHARS`, `GROQ_REQUEST_TIMEOUT`, `GROQ_DEFAULT_TEMPERATURE`, `GROQ_DEFAULT_MAX_TOKENS`, `GROQ_TEMPERATURE_MIN`, `GROQ_TEMPERATURE_MAX`, `GROQ_MAX_OUTPUT_TOKENS_CAP` |
| **Gemini (tùy chọn)** | `GEMINI_API_KEY` hoặc tham số hệ thống `gemini.api_key` |
| **Google Calendar (tùy chọn)** | `GOOGLE_SERVICE_ACCOUNT_JSON`, `GOOGLE_SERVICE_ACCOUNT_FILE`, hoặc bộ OAuth (`GOOGLE_OAUTH_CLIENT_ID`, …) — xem `lms/services/google_calendar_client.py` |
| **CSV / dữ liệu** | `LMS_CSV_SYNC_ENABLED`, `LMS_CSV_ON_START`, `LMS_CSV_IMPORT_DIR`, … |
| **Khác** | `LMS_MAX_VIDEO_UPLOAD_MB`, `LMS_AI_ROADMAP_QUESTION_COUNT`, `LMS_AI_CHAT_DEBUG`, … |

Chi tiết đầy đủ nằm trong mã nguồn các service tương ứng.

## CSV / dữ liệu (Docker)

Thư mục host `LMS/data` được mount vào container (`/mnt/extra-addons/data`). CSV bootstrap/sync mặc định đọc `data/export/` (hoặc ghi đè bằng `LMS_CSV_IMPORT_DIR`).

## Giấy phép

Module phát hành theo **LGPL-3** (theo `lms/__manifest__.py`).

## Phiên bản

Phiên bản module hiện tại: **18.0.1.0.15** (xem `lms/__manifest__.py`).
