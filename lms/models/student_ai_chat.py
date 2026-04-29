# -*- coding: utf-8 -*-

import json
import logging
import os
import re
from html import escape

from odoo import _, fields, models
from odoo.exceptions import UserError

from ..services import groq_client
from ..services.groq_client import GroqConfigError

_logger = logging.getLogger(__name__)


class LmsStudentAiChat(models.TransientModel):
    _name = 'lms.student.ai.chat'
    _description = 'Tư vấn roadmap AI cho học viên'

    title = fields.Char(string='Tiêu đề phiên tư vấn', default='Tư vấn roadmap học tập')
    student_id = fields.Many2one(
        'lms.student',
        string='Học viên',
        readonly=True,
        default=lambda self: self.env['lms.student'].sudo().search([('user_id', '=', self.env.user.id)], limit=1),
    )
    allow_personal_data = fields.Boolean(string='Cho phép AI đọc dữ liệu cá nhân')
    question_target = fields.Integer(
        string='Số câu hỏi mục tiêu',
        readonly=True,
        default=lambda self: self._get_question_target_from_env(),
    )
    asked_count = fields.Integer(string='Số câu đã hỏi', readonly=True, default=0)
    session_state = fields.Selection(
        [('draft', 'Chưa bắt đầu'), ('chatting', 'Đang trò chuyện'), ('done', 'Đã hoàn thành')],
        string='Trạng thái',
        default='draft',
        readonly=True,
    )
    user_message = fields.Text(string='Tin nhắn của bạn')
    conversation_json = fields.Text(string='Lịch sử hội thoại (JSON)', readonly=True, default='[]')
    useful_answers_json = fields.Text(string='Trả lời hữu ích (JSON)', readonly=True, default='[]')
    roadmap_options_json = fields.Text(string='Danh sách roadmap (JSON)', readonly=True, default='[]')
    selected_roadmap_index = fields.Integer(string='Roadmap đã chọn', readonly=True, default=0)
    debug_last_ai_request = fields.Text(string='Debug request AI', readonly=True)
    debug_last_ai_response = fields.Text(string='Debug response AI', readonly=True)
    conversation_html = fields.Html(
        string='Cuộc trò chuyện',
        compute='_compute_conversation_html',
        sanitize=False,
    )
    is_chat_locked = fields.Boolean(string='Khóa chat', compute='_compute_is_chat_locked')
    has_available_courses = fields.Boolean(
        string='Có khóa học khả dụng',
        compute='_compute_has_available_courses',
    )
    unavailable_reason = fields.Char(
        string='Thông báo không khả dụng',
        compute='_compute_has_available_courses',
    )
    roadmap_options_html = fields.Html(
        string='Danh sách roadmap',
        compute='_compute_roadmap_options_html',
        sanitize=False,
    )
    has_roadmap_options = fields.Boolean(string='Có roadmap để chọn', compute='_compute_roadmap_choice_ui')
    has_roadmap_selected = fields.Boolean(string='Đã chọn roadmap', compute='_compute_roadmap_choice_ui')
    roadmap_option_1_available = fields.Boolean(string='Roadmap 1', compute='_compute_roadmap_choice_ui')
    roadmap_option_2_available = fields.Boolean(string='Roadmap 2', compute='_compute_roadmap_choice_ui')
    roadmap_option_3_available = fields.Boolean(string='Roadmap 3', compute='_compute_roadmap_choice_ui')
    roadmap_option_1_label = fields.Char(string='Nhãn roadmap 1', compute='_compute_roadmap_choice_ui')
    roadmap_option_2_label = fields.Char(string='Nhãn roadmap 2', compute='_compute_roadmap_choice_ui')
    roadmap_option_3_label = fields.Char(string='Nhãn roadmap 3', compute='_compute_roadmap_choice_ui')

    @staticmethod
    def _no_course_message():
        return 'Hiện hệ thống chưa có khóa học nào, chưa thể dùng chức năng tư vấn roadmap.'

    @staticmethod
    def _chat_ephemeral_notice():
        return 'Lưu ý: Bạn hãy lưu lại gợi ý roadmap này. Nếu thoát khỏi màn hình này, toàn bộ cuộc trò chuyện sẽ bị xóa.'

    def _get_available_courses_count(self):
        return self.env['lms.course'].sudo().search_count([])

    @staticmethod
    def _format_vnd(amount):
        try:
            value = int(amount or 0)
        except (TypeError, ValueError):
            value = 0
        return f'{value:,}'.replace(',', '.') + 'VND'

    def _reopen_chat_form_action(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Tư vấn roadmap cho học viên'),
            'res_model': 'lms.student.ai.chat',
            'view_mode': 'form',
            'view_id': self.env.ref('lms.view_lms_student_ai_chat_form').id,
            'res_id': self.id,
            'target': 'current',
        }

    def _compute_is_chat_locked(self):
        for rec in self:
            rec.is_chat_locked = rec.session_state == 'done'

    def _compute_has_available_courses(self):
        count = self._get_available_courses_count()
        has_courses = count > 0
        for rec in self:
            rec.has_available_courses = has_courses
            rec.unavailable_reason = (
                False
                if has_courses
                else self._no_course_message()
            )

    def _roadmap_options(self):
        self.ensure_one()
        try:
            data = json.loads(self.roadmap_options_json or '[]')
        except json.JSONDecodeError:
            return []
        if not isinstance(data, list):
            return []
        out = []
        for item in data[:3]:
            if not isinstance(item, dict):
                continue
            try:
                idx = int(item.get('index') or 0)
            except (TypeError, ValueError):
                idx = 0
            title = str(item.get('title') or '').strip()
            if idx < 1 or not title:
                continue
            courses = item.get('courses') or []
            if not isinstance(courses, list):
                courses = []
            clean_courses = [str(name).strip() for name in courses if str(name).strip()]
            out.append(
                {
                    'index': idx,
                    'title': title,
                    'strategy': str(item.get('strategy') or '').strip(),
                    'summary': str(item.get('summary') or '').strip(),
                    'fit_when': str(item.get('fit_when') or '').strip(),
                    'difference': str(item.get('difference') or '').strip(),
                    'courses': clean_courses,
                    'total_cost_vnd': int(item.get('total_cost_vnd') or 0),
                }
            )
        return out

    def _compute_roadmap_choice_ui(self):
        for rec in self:
            options = rec._roadmap_options()
            by_index = {x['index']: x for x in options}
            rec.has_roadmap_options = bool(options)
            rec.has_roadmap_selected = rec.selected_roadmap_index > 0
            rec.roadmap_option_1_available = 1 in by_index
            rec.roadmap_option_2_available = 2 in by_index
            rec.roadmap_option_3_available = 3 in by_index
            rec.roadmap_option_1_label = by_index.get(1, {}).get('title') or 'Phương án A'
            rec.roadmap_option_2_label = by_index.get(2, {}).get('title') or 'Phương án B'
            rec.roadmap_option_3_label = by_index.get(3, {}).get('title') or 'Phương án C'

    def _compute_roadmap_options_html(self):
        for rec in self:
            options = rec._roadmap_options()
            if not options:
                rec.roadmap_options_html = (
                    '<div style="padding:8px 10px;border:1px dashed #d1d5db;border-radius:8px;color:#6b7280;">'
                    'Chưa có roadmap để chọn.</div>'
                )
                continue
            lines = ['<div style="display:flex;flex-direction:column;gap:10px;">']
            for opt in options:
                is_selected = rec.selected_roadmap_index == opt['index']
                title = escape(opt['title'])
                badge = (
                    '<span style="margin-left:8px;color:#166534;background:#dcfce7;padding:2px 8px;border-radius:999px;">Đã chọn</span>'
                    if is_selected
                    else ''
                )
                lines.append(
                    '<div style="border:1px solid #d1d5db;border-radius:10px;padding:10px;background:#fff;">'
                    '<div style="font-weight:600;">%s%s</div>' % (title, badge)
                )
                if opt['strategy']:
                    lines.append('<div><b>Chiến lược:</b> %s</div>' % escape(opt['strategy']))
                if opt['summary']:
                    lines.append('<div><b>Tóm tắt:</b> %s</div>' % escape(opt['summary']))
                if opt['courses']:
                    lines.append('<div><b>Khóa học gợi ý:</b><ul style="margin:4px 0 0 18px;">')
                    for course_name in opt['courses']:
                        course = rec.env['lms.course'].sudo().search([('name', '=', course_name)], limit=1)
                        price_text = self._format_vnd(course.price) if course else '0VND'
                        lines.append('<li>%s (%s)</li>' % (escape(course_name), escape(price_text)))
                    lines.append('</ul></div>')
                lines.append('<div><b>Tổng chi phí roadmap:</b> %s</div>' % escape(self._format_vnd(opt['total_cost_vnd'])))
                if opt['difference']:
                    lines.append('<div><b>Khác biệt chính:</b> %s</div>' % escape(opt['difference']))
                if opt['fit_when']:
                    lines.append('<div><b>Phù hợp khi:</b> %s</div>' % escape(opt['fit_when']))
                lines.append('</div>')
            lines.append('</div>')
            rec.roadmap_options_html = ''.join(lines)

    def read(self, fields=None, load='_classic_read'):
        rows = super().read(fields=fields, load=load)
        if not rows:
            return rows
        has_courses = self._get_available_courses_count() > 0
        for row in rows:
            if 'has_available_courses' in row:
                row['has_available_courses'] = has_courses
            if 'unavailable_reason' in row:
                row['unavailable_reason'] = False if has_courses else self._no_course_message()
        return rows

    def _compute_conversation_html(self):
        for rec in self:
            msgs = rec._conversation_messages()
            if not msgs:
                rec.conversation_html = (
                    '<div style="padding:12px;border:1px solid #ddd;border-radius:8px;">'
                    '<i>Hãy bấm "Bắt đầu tư vấn" để AI đặt câu hỏi.</i></div>'
                )
                continue
            lines = ['<div style="border:1px solid #ddd;border-radius:10px;padding:12px;min-height:280px;background:#fafafa;">']
            for item in msgs:
                role = item.get('role')
                content = escape(str(item.get('content') or '')).replace('\n', '<br/>')
                if role == 'assistant':
                    lines.append(
                        '<div style="margin:8px 0;text-align:left;">'
                        '<span style="display:inline-block;max-width:80%%;background:#e9f3ff;'
                        'padding:8px 12px;border-radius:14px;color:#1f2937;">%s</span></div>' % content
                    )
                else:
                    lines.append(
                        '<div style="margin:8px 0;text-align:right;">'
                        '<span style="display:inline-block;max-width:80%%;background:#dcfce7;'
                        'padding:8px 12px;border-radius:14px;color:#14532d;">%s</span></div>' % content
                    )
            lines.append('</div>')
            rec.conversation_html = ''.join(lines)

    @staticmethod
    def _get_question_target_from_env():
        raw = (os.environ.get('LMS_AI_ROADMAP_QUESTION_COUNT') or '').strip()
        if not raw:
            return 5
        try:
            val = int(raw)
        except ValueError:
            return 5
        return max(1, val)

    def _conversation_messages(self):
        self.ensure_one()
        try:
            data = json.loads(self.conversation_json or '[]')
        except json.JSONDecodeError:
            return []
        if not isinstance(data, list):
            return []
        out = []
        for item in data:
            if not isinstance(item, dict):
                continue
            role = (item.get('role') or '').strip().lower()
            if role not in ('assistant', 'user'):
                continue
            content = str(item.get('content') or '').strip()
            if not content:
                continue
            out.append({'role': role, 'content': content})
        return out

    def _useful_pairs(self):
        self.ensure_one()
        try:
            data = json.loads(self.useful_answers_json or '[]')
        except json.JSONDecodeError:
            return []
        if not isinstance(data, list):
            return []
        out = []
        for item in data:
            if not isinstance(item, dict):
                continue
            q = str(item.get('question') or '').strip()
            a = str(item.get('answer') or '').strip()
            if q and a:
                out.append({'question': q, 'answer': a})
        return out

    def _set_conversation(self, messages):
        self.ensure_one()
        self.conversation_json = json.dumps(messages, ensure_ascii=False)

    def _set_useful_pairs(self, pairs):
        self.ensure_one()
        self.useful_answers_json = json.dumps(pairs, ensure_ascii=False)

    @staticmethod
    def _extract_json_object(raw_text):
        text = (raw_text or '').strip()
        if not text:
            raise ValueError(_('AI trả về rỗng.'))
        fenced = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, flags=re.S | re.I)
        if fenced:
            text = fenced.group(1)
        start = text.find('{')
        end = text.rfind('}')
        if start == -1 or end == -1 or end <= start:
            raise ValueError(_('Không đọc được JSON từ phản hồi AI.'))
        return json.loads(text[start : end + 1])

    def _build_personal_data_text(self):
        self.ensure_one()
        if not self.student_id:
            return 'Không có hồ sơ học viên liên kết.'
        s = self.student_id.sudo()
        enrolled = s.enrolled_courses_ids.mapped('course_id.name')
        learned = s.enrolled_courses_ids.filtered(lambda x: x.status == 'completed').mapped('course_id.name')
        parts = [
            'Mục tiêu học tập: %s' % (s.learning_goals or 'Chưa cung cấp'),
            'Kỹ năng mong muốn: %s' % (s.desired_skills or 'Chưa cung cấp'),
            'Khóa học đã đăng ký: %s' % (', '.join(enrolled) if enrolled else 'Chưa có'),
            'Khóa học đã học tập: %s' % (', '.join(learned) if learned else 'Chưa có'),
        ]
        return '\n'.join(parts)

    def _build_course_catalog_text(self):
        courses = self.env['lms.course'].sudo().search([])
        if not courses:
            return 'Hiện chưa có khóa học nào.'
        lines = []
        for idx, c in enumerate(courses, start=1):
            prereq_names = ', '.join(c.prerequisite_ids.mapped('name')) or 'Không có'
            lines.append(
                '%s) %s | Danh mục: %s | Cấp độ: %s | Thời lượng: %s giờ | Chi phí: %s | Tiên quyết: %s'
                % (
                    idx,
                    c.name,
                    c.category_id.name or 'N/A',
                    c.level_id.name or 'N/A',
                    c.duration_hours or 0,
                    self._format_vnd(c.price),
                    prereq_names,
                )
            )
        return '\n'.join(lines)

    def _build_allowed_course_names_text(self):
        courses = self.env['lms.course'].sudo().search([])
        names = [c.name for c in courses if c.name]
        if not names:
            return '- (trống)'
        return '\n'.join(['- %s' % name for name in names])

    def _normalize_roadmap_options(self, payload):
        if not isinstance(payload, dict):
            return []
        raw_roadmaps = payload.get('roadmaps') or []
        if not isinstance(raw_roadmaps, list):
            return []
        courses = self.env['lms.course'].sudo().search([])
        name_map = {c.name.strip().lower(): c for c in courses if (c.name or '').strip()}
        out = []
        for idx, raw in enumerate(raw_roadmaps[:3], start=1):
            if not isinstance(raw, dict):
                continue
            title = str(raw.get('title') or '').strip() or ('Phương án %s' % chr(64 + idx))
            strategy = str(raw.get('strategy') or '').strip()
            summary = str(raw.get('summary') or '').strip()
            fit_when = str(raw.get('fit_when') or '').strip()
            difference = str(raw.get('difference') or '').strip()
            raw_courses = raw.get('courses') or []
            if not isinstance(raw_courses, list):
                raw_courses = []
            picked_courses = []
            seen = set()
            for item in raw_courses:
                if isinstance(item, dict):
                    cname = str(item.get('name') or '').strip()
                else:
                    cname = str(item).strip()
                if not cname:
                    continue
                key = cname.lower()
                course = name_map.get(key)
                if not course:
                    continue
                if course.id in seen:
                    continue
                seen.add(course.id)
                picked_courses.append(course)
            if not picked_courses:
                continue
            total = sum(c.price or 0 for c in picked_courses)
            out.append(
                {
                    'index': idx,
                    'title': title,
                    'strategy': strategy,
                    'summary': summary,
                    'fit_when': fit_when,
                    'difference': difference,
                    'courses': [c.name for c in picked_courses],
                    'total_cost_vnd': int(total),
                }
            )
        return out

    def _ai_chat(self, messages, *, temperature=0.6, max_tokens=900):
        self._debug_ai_console('REQUEST', messages, temperature=temperature, max_tokens=max_tokens)
        self._set_browser_debug(request_payload={'messages': messages, 'temperature': temperature, 'max_tokens': max_tokens})
        try:
            response_text = groq_client.chat_completion(messages, temperature=temperature, max_tokens=max_tokens)
            self._debug_ai_console('RESPONSE', response_text)
            self._set_browser_debug(response_text=response_text)
            return response_text
        except GroqConfigError as e:
            raise UserError(str(e)) from e
        except Exception as e:
            self._debug_ai_console('ERROR', str(e))
            self._set_browser_debug(error_text=str(e))
            raise UserError(_('Không thể gọi Groq: %s') % str(e)) from e

    # ===== DEBUG BLOCK (dễ xóa) =====
    @staticmethod
    def _is_ai_debug_enabled():
        return (os.environ.get('LMS_AI_CHAT_DEBUG') or '').strip().lower() in ('1', 'true', 'yes', 'on')

    def _debug_ai_console(self, direction, payload, **meta):
        if not self._is_ai_debug_enabled():
            return
        preview = payload
        if isinstance(payload, (dict, list)):
            preview = json.dumps(payload, ensure_ascii=False)
        preview = str(preview)
        if len(preview) > 4000:
            preview = preview[:4000] + ' ...[truncated]'
        meta_text = (' | ' + ', '.join('%s=%s' % (k, v) for k, v in meta.items())) if meta else ''
        _logger.info('[AI_CHAT_DEBUG] %s%s\n%s', direction, meta_text, preview)

    def _set_browser_debug(self, request_payload=None, response_text=None, error_text=None):
        if not self._is_ai_debug_enabled():
            return
        self.ensure_one()
        vals = {}
        if request_payload is not None:
            req = json.dumps(request_payload, ensure_ascii=False)
            vals['debug_last_ai_request'] = req[:8000]
        if response_text is not None:
            vals['debug_last_ai_response'] = str(response_text)[:8000]
        if error_text is not None:
            vals['debug_last_ai_response'] = 'ERROR: %s' % str(error_text)[:7900]
        if vals:
            self.write(vals)

    def _generate_first_question(self):
        self.ensure_one()
        personal_data = self._build_personal_data_text() if self.allow_personal_data else 'Người học không cho phép dùng dữ liệu cá nhân.'
        prompt = (
            'Bạn là trợ lý tư vấn roadmap học tập.\n'
            'Nhiệm vụ: Sinh ra 1 câu hỏi mở đầu bằng tiếng Việt để hiểu mục tiêu người học.\n'
            'Quy tắc: câu ngắn gọn, rõ ràng, không quá 35 từ, chỉ trả về nội dung câu hỏi.\n'
            'Bối cảnh:\n%s' % personal_data
        )
        return self._ai_chat(
            [{'role': 'system', 'content': 'Luôn trả lời bằng tiếng Việt.'}, {'role': 'user', 'content': prompt}],
            temperature=0.8,
            max_tokens=120,
        )

    def _evaluate_answer_and_next_question(self, last_question, answer):
        self.ensure_one()
        remain = max(0, self.question_target - self.asked_count)
        prompt = (
            'Bạn đánh giá câu trả lời của học viên cho câu hỏi tư vấn roadmap.\n'
            'Trả về JSON hợp lệ duy nhất với schema:\n'
            '{\n'
            '  "is_useful": true/false,\n'
            '  "next_question": "string"\n'
            '}\n'
            'Quy tắc:\n'
            '- is_useful=false nếu câu trả lời hời hợt/lạc đề/không giúp tư vấn.\n'
            '- Nếu còn câu cần hỏi (remain > 0), next_question là một câu hỏi mới, ngắn gọn, phù hợp ngữ cảnh.\n'
            '- Nếu remain = 0 thì next_question để chuỗi rỗng.\n'
            '- Không được thêm bất kỳ text ngoài JSON.\n'
            'Dữ liệu:\n'
            'last_question: %s\n'
            'answer: %s\n'
            'remain: %s\n' % (last_question, answer, remain)
        )
        raw = self._ai_chat(
            [{'role': 'system', 'content': 'Bạn chỉ trả về JSON hợp lệ, tiếng Việt.'}, {'role': 'user', 'content': prompt}],
            temperature=0.5,
            max_tokens=220,
        )
        parsed = self._extract_json_object(raw)
        return {
            'is_useful': bool(parsed.get('is_useful')),
            'next_question': str(parsed.get('next_question') or '').strip(),
        }

    def _generate_roadmap_options(self):
        self.ensure_one()
        useful_pairs = self._useful_pairs()
        if not useful_pairs:
            return []
        qa_text = '\n'.join(
            ['- Câu hỏi: %s\n  Trả lời: %s' % (x['question'], x['answer']) for x in useful_pairs]
        )
        personal_data = self._build_personal_data_text() if self.allow_personal_data else 'Không dùng dữ liệu cá nhân.'
        course_text = self._build_course_catalog_text()
        allowed_course_names = self._build_allowed_course_names_text()
        course_count = self._get_available_courses_count()
        prompt = (
            'Hãy đề xuất roadmap học tập bằng tiếng Việt và trả về JSON hợp lệ DUY NHẤT.\n'
            'Schema bắt buộc:\n'
            '{\n'
            '  "roadmaps": [\n'
            '    {\n'
            '      "title": "Phương án A: ...",\n'
            '      "strategy": "Chiến lược chính",\n'
            '      "summary": "Mô tả ngắn",\n'
            '      "difference": "Khác biệt chính so với phương án còn lại",\n'
            '      "fit_when": "Phù hợp khi ...",\n'
            '      "courses": [\n'
            '        {"name": "Tên khóa học có trong hệ thống"}\n'
            '      ]\n'
            '    }\n'
            '  ]\n'
            '}\n'
            'Quy tắc:\n'
            '- Chỉ trả JSON, không thêm text ngoài JSON.\n'
            '- Chỉ dùng tên khóa học trong danh sách hợp lệ.\n'
            '- Đưa ra tối đa 3 phương án, tối thiểu 1 phương án.\n'
            '- Chỉ tạo nhiều phương án nếu khác biệt chiến lược rõ ràng.\n'
            '- Nếu dữ liệu khóa học hạn chế thì chỉ trả 1 phương án.\n\n'
            'Tổng số khóa học hiện có: %s\n'
            'Danh sách tên khóa học hợp lệ (chỉ dùng các tên này):\n%s\n\n'
            'Thông tin học viên:\n%s\n\n'
            'Các trả lời hữu ích:\n%s\n\n'
            'Danh mục khóa học hiện có:\n%s\n'
            % (course_count, allowed_course_names, personal_data, qa_text, course_text)
        )
        raw = self._ai_chat(
            [{'role': 'system', 'content': 'Bạn là chuyên gia tư vấn lộ trình học tập. Luôn trả lời tiếng Việt.'}, {'role': 'user', 'content': prompt}],
            temperature=0.4,
            max_tokens=1200,
        )
        return self._normalize_roadmap_options(self._extract_json_object(raw))

    def _build_roadmap_result_text(self, options):
        if not options:
            return (
                'Mình chưa thể tạo danh sách roadmap hợp lệ từ dữ liệu hiện có. '
                'Bạn vui lòng thử lại với câu trả lời chi tiết hơn.'
            )
        lines = ['Mình đã tạo các roadmap gợi ý để bạn chọn 1 phương án:']
        for opt in options:
            lines.append('')
            lines.append('%s' % opt['title'])
            if opt['strategy']:
                lines.append('Chiến lược: %s' % opt['strategy'])
            if opt['summary']:
                lines.append('Tóm tắt: %s' % opt['summary'])
            lines.append('Khóa học gợi ý:')
            for course_name in opt['courses']:
                course = self.env['lms.course'].sudo().search([('name', '=', course_name)], limit=1)
                price_text = self._format_vnd(course.price if course else 0)
                lines.append('- %s (%s)' % (course_name, price_text))
            lines.append('Tổng chi phí roadmap: %s' % self._format_vnd(opt['total_cost_vnd']))
            if opt['difference']:
                lines.append('Khác biệt chính: %s' % opt['difference'])
            if opt['fit_when']:
                lines.append('Phù hợp khi: %s' % opt['fit_when'])
        lines.append('')
        lines.append('Hãy chọn đúng 1 roadmap bằng nút "Chọn roadmap".')
        return '\n'.join(lines)

    def _enroll_courses_from_option(self, option):
        self.ensure_one()
        if not self.student_id:
            raise UserError(_('Không tìm thấy hồ sơ học viên để đăng ký khóa học.'))
        course_names = option.get('courses') or []
        if not course_names:
            raise UserError(_('Roadmap được chọn không có khóa học hợp lệ.'))
        Course = self.env['lms.course'].sudo()
        Enrollment = self.env['lms.student.course'].sudo()
        created = 0
        skipped = 0
        for name in course_names:
            course = Course.search([('name', '=', name)], limit=1)
            if not course:
                skipped += 1
                continue
            existed = Enrollment.search(
                [
                    ('student_id', '=', self.student_id.id),
                    ('course_id', '=', course.id),
                    ('status', '!=', 'cancelled'),
                ],
                limit=1,
            )
            if existed:
                skipped += 1
                continue
            Enrollment.create(
                {
                    'student_id': self.student_id.id,
                    'course_id': course.id,
                    'status': 'pending',
                    'enrollment_date': fields.Date.today(),
                    'final_score': False,
                }
            )
            created += 1
        return created, skipped

    def _action_select_roadmap(self, index):
        self.ensure_one()
        if self.session_state != 'done':
            raise UserError(_('Bạn chỉ có thể chọn roadmap sau khi phiên tư vấn kết thúc.'))
        if self.selected_roadmap_index:
            raise UserError(_('Bạn đã chọn roadmap trước đó. Mỗi phiên chỉ được chọn 1 roadmap.'))
        options = self._roadmap_options()
        option = next((x for x in options if x['index'] == index), None)
        if not option:
            raise UserError(_('Roadmap không tồn tại hoặc không hợp lệ.'))
        created, skipped = self._enroll_courses_from_option(option)
        self.write({'selected_roadmap_index': index})
        conv = self._conversation_messages()
        conv.append(
            {
                'role': 'assistant',
                'content': 'Bạn đã chọn "%s". Đã tạo %s đăng ký mới, bỏ qua %s khóa học đã tồn tại/không hợp lệ.'
                % (option['title'], created, skipped),
            }
        )
        self._set_conversation(conv)
        return self._reopen_chat_form_action()

    def action_select_roadmap_1(self):
        return self._action_select_roadmap(1)

    def action_select_roadmap_2(self):
        return self._action_select_roadmap(2)

    def action_select_roadmap_3(self):
        return self._action_select_roadmap(3)

    def action_start_session(self):
        self.ensure_one()
        if self._get_available_courses_count() <= 0:
            raise UserError(_(self._no_course_message()))
        first_question = self._generate_first_question()
        self.write(
            {
                'session_state': 'chatting',
                'asked_count': 1,
                'user_message': False,
                'selected_roadmap_index': 0,
                'roadmap_options_json': '[]',
            }
        )
        self._set_useful_pairs([])
        self._set_conversation([{'role': 'assistant', 'content': first_question.strip()}])
        return self._reopen_chat_form_action()

    def action_send_message(self):
        self.ensure_one()
        if self.session_state != 'chatting':
            raise UserError(_('Phiên chat chưa bắt đầu hoặc đã kết thúc.'))
        if self.is_chat_locked:
            raise UserError(_('Phiên chat đã khóa sau khi sinh roadmap.'))
        user_text = (self.user_message or '').strip()
        if not user_text:
            raise UserError(_('Vui lòng nhập nội dung trước khi gửi.'))
        conv = self._conversation_messages()
        if not conv or conv[-1]['role'] != 'assistant':
            raise UserError(_('Không xác định được câu hỏi gần nhất từ AI.'))
        last_question = conv[-1]['content']
        conv.append({'role': 'user', 'content': user_text})
        eval_result = self._evaluate_answer_and_next_question(last_question, user_text)
        useful_pairs = self._useful_pairs()
        if eval_result['is_useful']:
            useful_pairs.append({'question': last_question, 'answer': user_text})
            self._set_useful_pairs(useful_pairs)
        if self.asked_count >= self.question_target:
            roadmap_options = self._generate_roadmap_options()
            self.roadmap_options_json = json.dumps(roadmap_options, ensure_ascii=False)
            roadmap_text = self._build_roadmap_result_text(roadmap_options)
            final_text = '%s\n\n%s' % (roadmap_text, self._chat_ephemeral_notice())
            conv.append({'role': 'assistant', 'content': final_text})
            self.write({'session_state': 'done', 'user_message': False})
            self._set_conversation(conv)
            return self._reopen_chat_form_action()
        next_question = eval_result['next_question'] or 'Bạn có thể chia sẻ thêm mục tiêu học tập cụ thể của mình?'
        conv.append({'role': 'assistant', 'content': next_question})
        self.write({'asked_count': self.asked_count + 1, 'user_message': False})
        self._set_conversation(conv)
        return self._reopen_chat_form_action()
