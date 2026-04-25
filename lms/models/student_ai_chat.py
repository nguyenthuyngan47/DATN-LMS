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

    @staticmethod
    def _no_course_message():
        return 'Hiện hệ thống chưa có khóa học nào, chưa thể dùng chức năng tư vấn roadmap.'

    @staticmethod
    def _chat_ephemeral_notice():
        return 'Lưu ý: Bạn hãy lưu lại gợi ý roadmap này. Nếu thoát khỏi màn hình này, toàn bộ cuộc trò chuyện sẽ bị xóa.'

    def _get_available_courses_count(self):
        return self.env['lms.course'].sudo().search_count([])

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
                '%s) %s | Danh mục: %s | Cấp độ: %s | Thời lượng: %s giờ | Chi phí: %s VND | Tiên quyết: %s'
                % (
                    idx,
                    c.name,
                    c.category_id.name or 'N/A',
                    c.level_id.name or 'N/A',
                    c.duration_hours or 0,
                    c.price or 0,
                    prereq_names,
                )
            )
        return '\n'.join(lines)

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

    def _generate_roadmap_text(self):
        self.ensure_one()
        useful_pairs = self._useful_pairs()
        if not useful_pairs:
            return (
                'Mình chưa thể gợi ý roadmap vì các câu trả lời chưa đủ thông tin hữu ích. '
                'Bạn hãy bắt đầu lại và cung cấp câu trả lời cụ thể hơn.'
            )
        qa_text = '\n'.join(
            ['- Câu hỏi: %s\n  Trả lời: %s' % (x['question'], x['answer']) for x in useful_pairs]
        )
        personal_data = self._build_personal_data_text() if self.allow_personal_data else 'Không dùng dữ liệu cá nhân.'
        course_text = self._build_course_catalog_text()
        prompt = (
            'Hãy đề xuất roadmap học tập bằng tiếng Việt, rõ ràng và thực tế.\n'
            'Yêu cầu đầu ra:\n'
            '- Chỉ trả về text thường, không markdown phức tạp, không JSON.\n'
            '- Đưa ra 2 đến 3 roadmap theo các phương án A/B/C để học viên có thể lựa chọn.\n'
            '- Mỗi phương án phải có: mục tiêu, các giai đoạn học, khóa học gợi ý theo thứ tự, và lý do ngắn gọn.\n'
            '- Cuối mỗi phương án, thêm một dòng "Phù hợp khi: ..." để nêu đối tượng phù hợp.\n'
            '- Tối ưu dựa trên dữ liệu học viên + danh mục khóa học.\n\n'
            'Thông tin học viên:\n%s\n\n'
            'Các trả lời hữu ích:\n%s\n\n'
            'Danh mục khóa học hiện có:\n%s\n' % (personal_data, qa_text, course_text)
        )
        return self._ai_chat(
            [{'role': 'system', 'content': 'Bạn là chuyên gia tư vấn lộ trình học tập. Luôn trả lời tiếng Việt.'}, {'role': 'user', 'content': prompt}],
            temperature=0.4,
            max_tokens=1200,
        )

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
            roadmap_text = self._generate_roadmap_text()
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
