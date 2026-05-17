# -*- coding: utf-8 -*-

import json
import logging
import os
import re
from html import escape

from odoo import _, fields, models
from odoo.exceptions import UserError
from odoo.tools import format_date

from ..services import groq_client
from ..services.groq_client import GroqConfigError

_logger = logging.getLogger(__name__)


class LmsStudentAiChat(models.TransientModel):
    _name = 'lms.student.ai.chat'
    _description = 'AI Roadmap Counseling for Students'

    title = fields.Char(string='Session Title', default='Learning Roadmap Counseling')
    student_id = fields.Many2one(
        'lms.student',
        string='Student',
        readonly=True,
        default=lambda self: self.env['lms.student'].sudo().search([('user_id', '=', self.env.user.id)], limit=1),
    )
    allow_personal_data = fields.Boolean(string='Allow AI to Read Personal Data')
    question_target = fields.Integer(
        string='Target Questions',
        readonly=True,
        default=lambda self: self._get_question_target_from_env(),
    )
    asked_count = fields.Integer(string='Questions Asked', readonly=True, default=0)
    session_state = fields.Selection(
        [('draft', 'Not Started'), ('chatting', 'Chatting'), ('done', 'Completed')],
        string='Status',
        default='draft',
        readonly=True,
    )
    user_message = fields.Text(string='Your Message')
    conversation_json = fields.Text(string='Conversation History (JSON)', readonly=True, default='[]')
    useful_answers_json = fields.Text(string='Useful Answers (JSON)', readonly=True, default='[]')
    roadmap_options_json = fields.Text(string='Roadmap List (JSON)', readonly=True, default='[]')
    selected_roadmap_index = fields.Integer(string='Selected Roadmap', readonly=True, default=0)
    created_roadmap_id = fields.Many2one(
        'lms.roadmap',
        string='Created Roadmap',
        readonly=True,
        ondelete='set null',
    )
    debug_last_ai_request = fields.Text(string='Debug AI Request', readonly=True)
    debug_last_ai_response = fields.Text(string='Debug AI Response', readonly=True)
    conversation_html = fields.Html(
        string='Conversation',
        compute='_compute_conversation_html',
        sanitize=False,
    )
    is_chat_locked = fields.Boolean(string='Chat Locked', compute='_compute_is_chat_locked')
    has_available_courses = fields.Boolean(
        string='Courses Available',
        compute='_compute_has_available_courses',
    )
    unavailable_reason = fields.Char(
        string='Unavailable Notice',
        compute='_compute_has_available_courses',
    )
    roadmap_options_html = fields.Html(
        string='Roadmap List',
        compute='_compute_roadmap_options_html',
        sanitize=False,
    )
    roadmap_option_1_html = fields.Html(
        string='Roadmap Option 1',
        compute='_compute_roadmap_options_html',
        sanitize=False,
    )
    roadmap_option_2_html = fields.Html(
        string='Roadmap Option 2',
        compute='_compute_roadmap_options_html',
        sanitize=False,
    )
    roadmap_option_3_html = fields.Html(
        string='Roadmap Option 3',
        compute='_compute_roadmap_options_html',
        sanitize=False,
    )
    has_roadmap_options = fields.Boolean(string='Roadmaps Available', compute='_compute_roadmap_choice_ui')
    has_roadmap_selected = fields.Boolean(string='Roadmap Selected', compute='_compute_roadmap_choice_ui')
    roadmap_option_1_available = fields.Boolean(string='Roadmap 1', compute='_compute_roadmap_choice_ui')
    roadmap_option_2_available = fields.Boolean(string='Roadmap 2', compute='_compute_roadmap_choice_ui')
    roadmap_option_3_available = fields.Boolean(string='Roadmap 3', compute='_compute_roadmap_choice_ui')
    roadmap_option_1_label = fields.Char(string='Roadmap 1 Label', compute='_compute_roadmap_choice_ui')
    roadmap_option_2_label = fields.Char(string='Roadmap 2 Label', compute='_compute_roadmap_choice_ui')
    roadmap_option_3_label = fields.Char(string='Roadmap 3 Label', compute='_compute_roadmap_choice_ui')

    def _no_course_message(self):
        return _(
            'No courses available in the system. Roadmap counseling cannot be used yet.'
        )

    def _chat_ephemeral_notice(self):
        return _(
            'Note: Please save this roadmap suggestion. If you leave this screen, the entire '
            'conversation will be deleted.'
        )

    def _get_available_courses_count(self):
        return self.env['lms.course'].sudo().search_count([])

    @staticmethod
    def _format_vnd(amount):
        try:
            value = int(amount or 0)
        except (TypeError, ValueError):
            value = 0
        return f'{value:,}'.replace(',', '.') + 'VND'

    def _format_course_period_plain(self, course):
        """Localized start/end for roadmap text (plain, not HTML)."""
        if not course:
            return ''
        if course.start_date or course.end_date:
            sd = format_date(self.env, course.start_date) if course.start_date else '—'
            ed = format_date(self.env, course.end_date) if course.end_date else '—'
            return _('from %(start)s to %(end)s') % {'start': sd, 'end': ed}
        return _('(course dates not set)')

    def _reopen_chat_form_action(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('AI Roadmap Counseling'),
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
            rec.roadmap_option_1_label = by_index.get(1, {}).get('title') or _('Option A')
            rec.roadmap_option_2_label = by_index.get(2, {}).get('title') or _('Option B')
            rec.roadmap_option_3_label = by_index.get(3, {}).get('title') or _('Option C')

    def _compute_roadmap_options_html(self):
        for rec in self:
            options = rec._roadmap_options()
            if not options:
                rec.roadmap_options_html = (
                    '<div style="padding:8px 10px;border:1px dashed #d1d5db;border-radius:8px;color:#6b7280;">'
                    '%s</div>' % escape(_('No roadmaps to choose from.'))
                )
                rec.roadmap_option_1_html = False
                rec.roadmap_option_2_html = False
                rec.roadmap_option_3_html = False
                continue
            lines = ['<div style="display:flex;flex-direction:column;gap:10px;">']
            individual_htmls = {}
            for opt in options:
                is_selected = rec.selected_roadmap_index == opt['index']
                title = escape(opt['title'])
                badge = (
                    '<span style="margin-left:8px;color:#166534;background:#dcfce7;padding:2px 8px;border-radius:999px;">'
                    '%s</span>' % escape(_('Selected'))
                    if is_selected
                    else ''
                )
                opt_lines = []
                opt_lines.append(
                    '<div style="border:1px solid #d1d5db;border-radius:10px;padding:10px;background:#fff;">'
                    '<div style="font-weight:600;">%s%s</div>' % (title, badge)
                )
                if opt['summary']:
                    opt_lines.append(
                        '<div><b>%s</b> %s</div>'
                        % (escape(_('Summary:')), escape(opt['summary']))
                    )
                if opt['courses']:
                    opt_lines.append(
                        '<div><b>%s</b><ul style="margin:4px 0 0 18px;">' % escape(_('Suggested Courses:'))
                    )
                    for course_name in opt['courses']:
                        course = rec.env['lms.course'].sudo().search([('name', '=', course_name)], limit=1)
                        price_text = self._format_vnd(course.price) if course else '0VND'
                        period = ''
                        if course:
                            ptxt = rec._format_course_period_plain(course)
                            if ptxt:
                                period = ' — %s' % escape(ptxt)
                        opt_lines.append(
                            '<li>%s (%s)%s</li>'
                            % (escape(course_name), escape(price_text), period)
                        )
                    opt_lines.append('</ul></div>')
                opt_lines.append(
                    '<div><b>%s</b> %s</div>'
                    % (escape(_('Total Roadmap Cost:')), escape(self._format_vnd(opt['total_cost_vnd'])))
                )
                if opt['difference']:
                    opt_lines.append(
                        '<div><b>%s</b> %s</div>'
                        % (escape(_('Key Difference:')), escape(opt['difference']))
                    )
                if opt['fit_when']:
                    opt_lines.append(
                        '<div><b>%s</b> %s</div>'
                        % (escape(_('Suitable When:')), escape(opt['fit_when']))
                    )
                opt_lines.append('</div>')
                opt_html = ''.join(opt_lines)
                individual_htmls[opt['index']] = opt_html
                lines.append(opt_html)
            lines.append('</div>')
            rec.roadmap_options_html = ''.join(lines)
            rec.roadmap_option_1_html = individual_htmls.get(1, False)
            rec.roadmap_option_2_html = individual_htmls.get(2, False)
            rec.roadmap_option_3_html = individual_htmls.get(3, False)

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
                    '<i>Click "Start Counseling" for AI to ask questions.</i></div>'
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
            raise ValueError(_('AI returned empty response.'))
        fenced = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, flags=re.S | re.I)
        if fenced:
            text = fenced.group(1)
        start = text.find('{')
        end = text.rfind('}')
        if start == -1 or end == -1 or end <= start:
            raise ValueError(_('Could not parse JSON from AI response.'))
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
            title = str(raw.get('title') or '').strip() or ('Option %s' % chr(64 + idx))
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
            raise UserError(_('Could not call Groq: %s') % str(e)) from e

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
            return _(
                'Could not create valid roadmap options from the current data. '
                'Please try again with more detailed answers.'
            )
        lines = [_('I have created roadmap suggestions for you to choose from:')]
        for opt in options:
            lines.append('')
            lines.append('%s' % opt['title'])
            if opt['summary']:
                lines.append('%s %s' % (_('Summary:'), opt['summary']))
            lines.append(_('Suggested Courses:'))
            for course_name in opt['courses']:
                course = self.env['lms.course'].sudo().search([('name', '=', course_name)], limit=1)
                price_text = self._format_vnd(course.price if course else 0)
                period = ''
                if course:
                    ptxt = self._format_course_period_plain(course)
                    if ptxt:
                        period = ' — %s' % ptxt
                lines.append('- %s (%s)%s' % (course_name, price_text, period))
            lines.append('%s %s' % (_('Total Roadmap Cost:'), self._format_vnd(opt['total_cost_vnd'])))
            if opt['difference']:
                lines.append('%s %s' % (_('Key Difference:'), opt['difference']))
            if opt['fit_when']:
                lines.append('%s %s' % (_('Suitable When:'), opt['fit_when']))
        lines.append('')
        lines.append(
            _(
                'Please select exactly one roadmap option. The system will save your roadmap '
                'and open it so you can register for each course individually (pending approval).'
            )
        )
        return '\n'.join(lines)

    def _priority_and_timeframe_for_sequence(self, index, total):
        """Gán priority/timeframe theo thứ tự khóa trong phương án AI."""
        if total <= 1:
            return 'high', 'short'
        third = max(1, total // 3)
        if index < third:
            return 'high', 'short'
        if index < 2 * third:
            return 'medium', 'medium'
        return 'low', 'long'

    def _create_roadmap_from_option(self, option):
        """Tạo lms.roadmap + các dòng khóa từ phương án AI (không đăng ký khóa)."""
        self.ensure_one()
        if not self.student_id:
            raise UserError(_('Student profile not found.'))
        course_names = option.get('courses') or []
        if not course_names:
            raise UserError(_('Selected roadmap has no valid courses.'))

        Course = self.env['lms.course'].sudo()
        Roadmap = self.env['lms.roadmap'].sudo()
        RoadmapCourse = self.env['lms.roadmap.course'].sudo()

        reason_parts = []
        if option.get('summary'):
            reason_parts.append(option['summary'])
        if option.get('strategy'):
            reason_parts.append(_('Strategy: %s') % option['strategy'])
        if option.get('fit_when'):
            reason_parts.append(_('Suitable when: %s') % option['fit_when'])

        roadmap = Roadmap.create(
            {
                'student_id': self.student_id.id,
                'state': 'suggested',
                'recommendation_method': 'hybrid',
                'ai_recommendation_reason': '\n'.join(reason_parts) or option.get('title') or '',
            }
        )

        valid_names = [n for n in course_names if n]
        total = len(valid_names)
        seq = 10
        for idx, name in enumerate(valid_names):
            course = Course.search([('name', '=', name)], limit=1)
            if not course:
                continue
            priority, timeframe = self._priority_and_timeframe_for_sequence(idx, total)
            RoadmapCourse.create(
                {
                    'roadmap_id': roadmap.id,
                    'course_id': course.id,
                    'sequence': seq,
                    'priority': priority,
                    'timeframe': timeframe,
                    'recommendation_reason': option.get('summary') or option.get('title') or '',
                }
            )
            seq += 10

        if not roadmap.course_line_ids:
            roadmap.unlink()
            raise UserError(
                _(
                    'Could not create roadmap: no matching courses in the system. '
                    'Please contact an administrator.'
                )
            )
        return roadmap

    def _open_roadmap_form_action(self, roadmap):
        self.ensure_one()
        return roadmap.action_open_form()

    def action_open_created_roadmap(self):
        """Mở lại roadmap đã tạo từ session chat (nút dự phòng trên form chat)."""
        self.ensure_one()
        if not self.created_roadmap_id:
            raise UserError(_('No roadmap has been created for this session yet.'))
        return self._open_roadmap_form_action(self.created_roadmap_id)

    def _action_select_roadmap(self, index):
        self.ensure_one()
        if self.session_state != 'done':
            raise UserError(_('You can only select a roadmap after the counseling session ends.'))
        if self.selected_roadmap_index:
            raise UserError(_('You have already selected a roadmap. Each session allows only one selection.'))
        options = self._roadmap_options()
        option = next((x for x in options if x['index'] == index), None)
        if not option:
            raise UserError(_('Roadmap does not exist or is invalid.'))
        roadmap = self._create_roadmap_from_option(option)
        self.write(
            {
                'selected_roadmap_index': index,
                'created_roadmap_id': roadmap.id,
            }
        )
        conv = self._conversation_messages()
        conv.append(
            {
                'role': 'assistant',
                'content': _(
                    'You selected "%(title)s". Your learning roadmap has been saved (%(count)s courses).\n\n'
                    'Next step: on the roadmap screen (opening now), go to the '
                    '"Recommended Courses" tab and click **Enroll** for each course you want to take. '
                    'Each registration will be **Pending approval** until an instructor or administrator approves it.\n\n'
                    'You can reopen this roadmap anytime from your student profile → Roadmap.'
                )
                % {'title': option['title'], 'count': len(roadmap.course_line_ids)},
            }
        )
        self._set_conversation(conv)
        return self._open_roadmap_form_action(roadmap)

    def action_select_roadmap_1(self):
        return self._action_select_roadmap(1)

    def action_select_roadmap_2(self):
        return self._action_select_roadmap(2)

    def action_select_roadmap_3(self):
        return self._action_select_roadmap(3)

    def action_start_session(self):
        self.ensure_one()
        if self._get_available_courses_count() <= 0:
            raise UserError(self._no_course_message())
        first_question = self._generate_first_question()
        self.write(
            {
                'session_state': 'chatting',
                'asked_count': 1,
                'user_message': False,
                'selected_roadmap_index': 0,
                'created_roadmap_id': False,
                'roadmap_options_json': '[]',
            }
        )
        self._set_useful_pairs([])
        self._set_conversation([{'role': 'assistant', 'content': first_question.strip()}])
        return self._reopen_chat_form_action()

    def action_send_message(self):
        self.ensure_one()
        if self.session_state != 'chatting':
            raise UserError(_('Chat session has not started or has already ended.'))
        if self.is_chat_locked:
            raise UserError(_('Chat session is locked after generating roadmaps.'))
        user_text = (self.user_message or '').strip()
        if not user_text:
            raise UserError(_('Please enter a message before sending.'))
        conv = self._conversation_messages()
        if not conv or conv[-1]['role'] != 'assistant':
            raise UserError(_('Could not determine the latest question from AI.'))
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
        next_question = eval_result['next_question'] or _(
            'Could you share more about your specific learning goals?'
        )
        conv.append({'role': 'assistant', 'content': next_question})
        self.write({'asked_count': self.asked_count + 1, 'user_message': False})
        self._set_conversation(conv)
        return self._reopen_chat_form_action()
