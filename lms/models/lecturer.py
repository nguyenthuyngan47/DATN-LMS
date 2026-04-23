# -*- coding: utf-8 -*-
"""
Mọi bản ghi lms.lecturer mới (form, Import CSV, RPC, code) đều đi qua ORM ``create()`` —
logic tự tạo ``res.users`` gắn ở đây tương đương “trigger” cấp ứng dụng (không dùng SQL trigger).
"""

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError
from odoo.tools.mail import email_normalize

# Mật khẩu ban đầu cho user mới tạo tự động (Odoo mã hóa qua write ``password``).
_DEFAULT_LECTURER_AUTO_PASSWORD = "123456"


class LmsLecturer(models.Model):
    _name = "lms.lecturer"
    _description = "Giảng viên LMS"
    _order = "full_name, id"

    lecturer_id = fields.Char(string="Lecturer ID", required=True, copy=False, default="New")
    user_id = fields.Many2one(
        "res.users",
        string="Tài khoản",
        required=False,
        ondelete="cascade",
        index=True,
        help="Để trống: mỗi lần tạo bản ghi (kể cả import CSV), hệ thống tự tạo res.users với login = email.",
    )

    username = fields.Char(string="Username", related="user_id.login", store=True, readonly=True)
    password_hash = fields.Char(string="Password Hash", compute="_compute_password_hash", readonly=True)
    email = fields.Char(string="Email", store=True, readonly=False)
    phone_number = fields.Char(string="Số điện thoại", store=True, readonly=False)
    role = fields.Selection(
        [("lecturer", "Lecturer")], string="Role", default="lecturer", required=True, readonly=True
    )
    status = fields.Selection(
        [("active", "Active"), ("inactive", "Inactive")], compute="_compute_status", store=True
    )
    active = fields.Boolean(string="Active", default=True, store=True, readonly=False)
    created_at = fields.Datetime(string="Created At", related="create_date", store=False, readonly=True)
    updated_at = fields.Datetime(string="Updated At", related="write_date", store=False, readonly=True)
    last_login = fields.Datetime(string="Last Login", related="user_id.login_date", store=True, readonly=True)

    full_name = fields.Char(string="Họ và tên", required=True)
    gender = fields.Selection(
        [("male", "Nam"), ("female", "Nữ"), ("other", "Khác")], string="Giới tính"
    )
    date_of_birth = fields.Date(string="Ngày sinh")
    avatar_url = fields.Char(string="Avatar URL")
    address = fields.Char(string="Địa chỉ")
    department = fields.Char(string="Bộ môn")
    specialization = fields.Char(string="Chuyên môn")
    academic_degree = fields.Char(string="Học vị")
    years_of_experience = fields.Integer(string="Số năm kinh nghiệm")

    faculty = fields.Char(string="Khoa")
    subject_expertise = fields.Text(string="Lĩnh vực giảng dạy")
    certifications = fields.Text(string="Chứng chỉ")
    teaching_level = fields.Selection(
        [
            ("beginner", "Beginner"),
            ("intermediate", "Intermediate"),
            ("advanced", "Advanced"),
            ("expert", "Expert"),
        ],
        string="Teaching Level",
    )
    teaching_type = fields.Selection(
        [("online", "Online"), ("offline", "Offline"), ("hybrid", "Hybrid")],
        string="Teaching Type",
        default="hybrid",
    )

    course_assigned = fields.Many2many("lms.course", compute="_compute_operational_relations", string="Khóa học phụ trách")
    class_assigned = fields.Many2many(
        "lms.student.course", compute="_compute_operational_relations", string="Lớp phụ trách"
    )
    teaching_schedule = fields.Many2many(
        "calendar.event", compute="_compute_operational_relations", string="Lịch giảng dạy"
    )
    lesson_created = fields.Many2many("lms.lesson", compute="_compute_operational_relations", string="Bài giảng")
    assignment_created = fields.Integer(string="Bài tập đã tạo", compute="_compute_operational_metrics")
    exam_created = fields.Integer(string="Đề thi đã tạo", compute="_compute_operational_metrics")
    student_managed = fields.Many2many("lms.student", compute="_compute_operational_relations", string="Học viên")

    total_courses = fields.Integer(string="Total Courses", compute="_compute_activity_metrics")
    total_students = fields.Integer(string="Total Students", compute="_compute_activity_metrics")
    total_lessons_uploaded = fields.Integer(
        string="Total Lessons Uploaded", compute="_compute_activity_metrics"
    )
    total_assignments = fields.Integer(string="Total Assignments", compute="_compute_activity_metrics")
    average_course_rating = fields.Float(
        string="Average Course Rating", compute="_compute_activity_metrics", digits=(16, 2)
    )
    login_frequency = fields.Float(
        string="Login Frequency (weekly)", compute="_compute_activity_metrics", digits=(16, 2)
    )
    last_active_at = fields.Datetime(string="Last Active At", compute="_compute_activity_metrics")

    _sql_constraints = [
        ("lecturer_user_unique", "unique(user_id)", "Mỗi tài khoản chỉ gắn với một giảng viên."),
    ]

    @api.model
    def _needs_auto_lecturer_user(self, vals):
        """True khi chưa có user hợp lệ (form, CSV, API đều truyền vals qua create)."""
        uid = vals.get("user_id")
        if uid in (False, None, "", 0):
            return True
        if isinstance(uid, str) and not uid.strip():
            return True
        return False

    @api.model
    def _prepare_lecturer_user_on_create(self, vals):
        """
        Gán vals['user_id'] trước super().create — chạy cho mọi insert ORM (thủ công / import / RPC).
        """
        if not self._needs_auto_lecturer_user(vals):
            return
        full_name = (vals.get("full_name") or "").strip()
        if not full_name:
            raise ValidationError(_("Thiếu họ và tên: bắt buộc để tạo hoặc gán tài khoản đăng nhập."))
        email_norm = email_normalize(vals.get("email"))
        if not email_norm:
            raise ValidationError(
                _("Email không hợp lệ hoặc trống. Dùng định dạng email chuẩn (kể cả khi import CSV).")
            )
        vals["email"] = email_norm
        login = email_norm
        Users = self.env["res.users"].sudo()
        existing = Users.search([("login", "=", login)], limit=1)
        if existing:
            if self.sudo().search_count([("user_id", "=", existing.id)]):
                raise ValidationError(_("Email/login đã được dùng cho giảng viên khác: %s") % login)
            vals["user_id"] = existing.id
            return
        instructor_group = self.env.ref("lms.group_lms_instructor", raise_if_not_found=False)
        internal_group = self.env.ref("base.group_user")
        group_ids = [internal_group.id]
        if instructor_group:
            group_ids.append(instructor_group.id)
        company = self.env.company
        user = Users.with_context(no_reset_password=True).create(
            {
                "name": full_name,
                "login": login,
                "email": email_norm,
                "company_id": company.id,
                "company_ids": [(6, 0, [company.id])],
                "groups_id": [(6, 0, group_ids)],
            }
        )
        user.write({"password": _DEFAULT_LECTURER_AUTO_PASSWORD})
        phone = (vals.get("phone_number") or "").strip()
        if phone:
            user.partner_id.sudo().write({"phone": phone})
        vals["user_id"] = user.id

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("lecturer_id", "New") == "New":
                vals["lecturer_id"] = self.env["ir.sequence"].next_by_code("lms.lecturer") or "New"
            vals.setdefault("role", "lecturer")
            # Tránh NULL ở cột active khi tạo từ luồng tự động / import.
            vals.setdefault("active", True)
            self._prepare_lecturer_user_on_create(vals)
            if vals.get("user_id") and not vals.get("full_name"):
                user = self.env["res.users"].browse(vals["user_id"])
                vals["full_name"] = user.name
        return super().create(vals_list)

    @api.depends("active")
    def _compute_status(self):
        for rec in self:
            rec.status = "active" if rec.active else "inactive"

    def _compute_password_hash(self):
        for rec in self:
            rec.password_hash = rec.user_id.sudo().password or False

    @api.depends("user_id")
    def _compute_operational_relations(self):
        Calendar = self.env["calendar.event"].sudo()
        for rec in self:
            courses = self.env["lms.course"].sudo().search([("instructor_id", "=", rec.user_id.id)])
            enrollments = courses.mapped("student_course_ids")
            students = enrollments.mapped("student_id")
            lessons = courses.mapped("lesson_ids")
            schedules = Calendar.search([("lms_learning_history_id.course_id.instructor_id", "=", rec.user_id.id)])
            rec.course_assigned = [(6, 0, courses.ids)]
            rec.class_assigned = [(6, 0, enrollments.ids)]
            rec.student_managed = [(6, 0, students.ids)]
            rec.lesson_created = [(6, 0, lessons.ids)]
            rec.teaching_schedule = [(6, 0, schedules.ids)]

    @api.depends("user_id")
    def _compute_operational_metrics(self):
        for rec in self:
            rec.assignment_created = 0
            rec.exam_created = 0

    @api.depends("user_id", "last_login")
    def _compute_activity_metrics(self):
        now = fields.Datetime.now()
        for rec in self:
            courses = self.env["lms.course"].sudo().search([("instructor_id", "=", rec.user_id.id)])
            rec.total_courses = len(courses)
            rec.total_students = len(courses.mapped("student_course_ids.student_id"))
            rec.total_lessons_uploaded = len(courses.mapped("lesson_ids"))
            rec.total_assignments = rec.assignment_created
            ratings = [r for r in courses.mapped("average_rating") if r]
            rec.average_course_rating = (sum(ratings) / len(ratings)) if ratings else 0.0
            if rec.create_date and rec.last_login:
                days = max(1.0, (now - rec.create_date).days or 1.0)
                rec.login_frequency = round(7.0 / days, 2)
            else:
                rec.login_frequency = 0.0
            rec.last_active_at = rec.last_login

    @api.model
    def action_sync_from_existing_instructors(self):
        """
        Đồng bộ giảng viên từ dữ liệu hiện có:
        - nguồn chuẩn: lms.course.instructor_id (res.users)
        - không phá quan hệ cũ, chỉ tạo hồ sơ lms.lecturer còn thiếu
        """
        self.action_normalize_course_instructors()
        instructor_users = (
            self.env["lms.course"]
            .sudo()
            .search([])
            .mapped("instructor_id")
            .filtered(lambda u: u.id and u.active and not u.share and u.login not in self._excluded_logins())
        )
        if not instructor_users:
            return 0
        group = self.env.ref("lms.group_lms_instructor", raise_if_not_found=False)
        created = 0
        for user in instructor_users:
            if group and group not in user.groups_id:
                # Không ép thay đổi user-type group để tránh xung đột nhóm nội bộ/portal.
                try:
                    user.sudo().write({"groups_id": [(4, group.id)]})
                except Exception:  # noqa: BLE001
                    pass
            if not self.sudo().search_count([("user_id", "=", user.id)]):
                self.sudo().create(
                    {
                        "user_id": user.id,
                        "full_name": user.name,
                        "email": user.partner_id.email,
                        "phone_number": user.partner_id.phone,
                    }
                )
                created += 1
        return created

    @api.model
    def _excluded_logins(self):
        return {"__system__", "public", "portaltemplate", "default"}

    @api.model
    def action_normalize_course_instructors(self):
        """
        Chuẩn hóa instructor_id về tài khoản nội bộ đang active.
        Bản ghi trỏ tới user template/system sẽ được thay bằng admin.
        """
        admin = self.env.ref("base.user_admin", raise_if_not_found=False)
        if not admin:
            return 0
        courses = self.env["lms.course"].sudo().search([])
        changed = 0
        excluded = self._excluded_logins()
        for course in courses:
            u = course.instructor_id
            if not u or (not u.active) or u.share or (u.login in excluded):
                course.write({"instructor_id": admin.id})
                changed += 1
        return changed
