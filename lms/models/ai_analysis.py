# -*- coding: utf-8 -*-

from odoo import models, fields, api
import logging
import requests
import json
import os
import time

_logger = logging.getLogger(__name__)


class AIAnalysis(models.Model):
    _name = 'lms.ai.analysis'
    _description = 'AI Analysis and Recommendations'
    
    def _call_gemini_api(self, prompt, max_retries=3, retry_delay=1):
        """
        Gọi Gemini API để phân tích và đề xuất
        Trả về danh sách course IDs được đề xuất
        Có retry mechanism với exponential backoff
        """
        # Ưu tiên lấy từ System Parameter, sau đó mới lấy từ environment variable
        api_key = self.env['ir.config_parameter'].sudo().get_param('gemini.api_key') or os.getenv('GEMINI_API_KEY')
        
        if not api_key:
            _logger.warning("GEMINI_API_KEY không được cấu hình, sử dụng rule-based")
            return None
        
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-pro:generateContent?key={api_key}"
        headers = {
            'Content-Type': 'application/json',
        }
        data = {
            "contents": [{
                "parts": [{
                    "text": prompt
                }]
            }]
        }
        
        # Retry mechanism với exponential backoff
        for attempt in range(max_retries):
            try:
                _logger.info(f"Gọi Gemini API (lần thử {attempt + 1}/{max_retries})")
                response = requests.post(url, headers=headers, json=data, timeout=30)
                response.raise_for_status()
                
                result = response.json()
                if 'candidates' in result and len(result['candidates']) > 0:
                    content = result['candidates'][0]['content']['parts'][0]['text']
                    _logger.info(f"Gemini API thành công: nhận được {len(content)} ký tự")
                    return content
                else:
                    _logger.warning("Gemini API không trả về kết quả hợp lệ")
                    if attempt < max_retries - 1:
                        time.sleep(retry_delay * (2 ** attempt))
                        continue
                    return None
                    
            except requests.exceptions.Timeout as e:
                _logger.warning(f"Gemini API timeout (lần thử {attempt + 1}/{max_retries}): {str(e)}")
                if attempt < max_retries - 1:
                    time.sleep(retry_delay * (2 ** attempt))
                    continue
                return None
                
            except requests.exceptions.HTTPError as e:
                # Không retry cho lỗi 4xx (client error)
                if 400 <= response.status_code < 500:
                    _logger.error(f"Gemini API client error (HTTP {response.status_code}): {str(e)}")
                    return None
                # Retry cho lỗi 5xx (server error)
                _logger.warning(f"Gemini API server error (HTTP {response.status_code}, lần thử {attempt + 1}/{max_retries}): {str(e)}")
                if attempt < max_retries - 1:
                    time.sleep(retry_delay * (2 ** attempt))
                    continue
                return None
                
            except requests.exceptions.RequestException as e:
                _logger.warning(f"Gemini API request error (lần thử {attempt + 1}/{max_retries}): {str(e)}")
                if attempt < max_retries - 1:
                    time.sleep(retry_delay * (2 ** attempt))
                    continue
                return None
                
            except Exception as e:
                _logger.error(f"Lỗi không xác định khi gọi Gemini API: {str(e)}", exc_info=True)
                return None
        
        _logger.error(f"Gemini API thất bại sau {max_retries} lần thử")
        return None
    
    @api.model
    def ai_based_recommendation(self, student_id):
        """
        Sử dụng AI (Gemini) để đề xuất khóa học
        Kết hợp với rule-based và content-based
        """
        student = self.env['lms.student'].browse(student_id)
        if not student:
            return []
        
        # Chuẩn bị dữ liệu cho AI
        completed_courses = student.enrolled_courses_ids.filtered(
            lambda x: x.status in ['completed', 'learning']
        ).mapped('course_id')
        
        completed_course_names = ', '.join(completed_courses.mapped('name')) if completed_courses else 'Chưa có'
        
        all_courses = self.env['lms.course'].search([
            ('state', '=', 'published'),
            ('is_active', '=', True),
            ('id', 'not in', completed_courses.ids),
        ])
        
        available_courses_info = []
        for course in all_courses[:50]:  # Giới hạn 50 khóa học để tránh prompt quá dài
            available_courses_info.append(
                f"- {course.name} (Category: {course.category_id.name}, Level: {course.level_id.name})"
            )
        
        prompt = f"""
Bạn là một hệ thống AI đề xuất khóa học thông minh.

Thông tin học viên:
- Trình độ hiện tại: {student.current_level}
- Mục tiêu học tập: {student.learning_goals or 'Chưa có'}
- Kỹ năng mong muốn: {student.desired_skills or 'Chưa có'}
- Khóa học đã hoàn thành: {completed_course_names}
- Điểm trung bình: {student.average_score}
- Số ngày không hoạt động: {student.inactive_days}

Danh sách khóa học có sẵn:
{chr(10).join(available_courses_info)}

Hãy đề xuất top 10 khóa học phù hợp nhất cho học viên này. 
Trả về kết quả dưới dạng JSON array với format:
[
  {{"course_name": "Tên khóa học", "reason": "Lý do đề xuất", "priority": "high|medium|low"}},
  ...
]

Chỉ trả về JSON, không có text thêm.
"""
        
        ai_response = self._call_gemini_api(prompt)
        
        if not ai_response:
            # Fallback về rule-based nếu AI không hoạt động
            return self.rule_based_recommendation(student_id)
        
        # Parse kết quả từ AI
        try:
            # Loại bỏ markdown code blocks nếu có
            ai_response = ai_response.strip()
            if ai_response.startswith('```'):
                ai_response = ai_response.split('```')[1]
                if ai_response.startswith('json'):
                    ai_response = ai_response[4:]
                ai_response = ai_response.strip()
            
            ai_recommendations = json.loads(ai_response)
            
            recommendations = []
            for rec in ai_recommendations:
                course_name = rec.get('course_name', '')
                course = self.env['lms.course'].search([
                    ('name', '=', course_name),
                    ('state', '=', 'published'),
                ], limit=1)
                
                if course:
                    recommendations.append({
                        'course_id': course.id,
                        'reason': f"AI đề xuất: {rec.get('reason', '')}",
                        'priority': rec.get('priority', 'medium'),
                    })
            
            return recommendations[:10]
            
        except json.JSONDecodeError as e:
            _logger.error(f"Lỗi parse JSON từ AI: {str(e)}")
            # Fallback về rule-based
            return self.rule_based_recommendation(student_id)
        except Exception as e:
            _logger.error(f"Lỗi xử lý kết quả AI: {str(e)}")
            return self.rule_based_recommendation(student_id)
    
    @api.model
    def content_based_filtering(self, student_id):
        """
        Content-Based Filtering: So sánh Category, Level
        giữa khóa học đã học và khóa học mới
        """
        student = self.env['lms.student'].browse(student_id)
        if not student:
            return []
        
        # Lấy các khóa học đã học
        completed_courses = student.enrolled_courses_ids.filtered(
            lambda x: x.status in ['completed', 'learning']
        ).mapped('course_id')
        
        if not completed_courses:
            # Nếu chưa có khóa học nào, đề xuất theo trình độ hiện tại
            return self._recommend_by_level(student.current_level)
        
        # Lấy thông tin từ khóa học đã học
        learned_categories = completed_courses.mapped('category_id')
        learned_levels = completed_courses.mapped('level_id')
        
        # Tìm khóa học tương tự
        all_courses = self.env['lms.course'].search([
            ('state', '=', 'published'),
            ('is_active', '=', True),
            ('id', 'not in', completed_courses.ids),
        ])
        
        recommendations = []
        for course in all_courses:
            similarity_score = 0.0
            reasons = []
            
            # So sánh Category (trọng số 0.4)
            if course.category_id in learned_categories:
                similarity_score += 0.4
                reasons.append(f"Cùng danh mục: {course.category_id.name}")
            
            # So sánh Level (trọng số 0.3)
            if course.level_id in learned_levels:
                similarity_score += 0.3
                reasons.append(f"Cùng cấp độ: {course.level_id.name}")
            elif self._is_next_level(learned_levels, course.level_id):
                similarity_score += 0.2
                reasons.append(f"Cấp độ tiếp theo: {course.level_id.name}")
            
            if similarity_score > 0.2:  # Ngưỡng tối thiểu
                recommendations.append({
                    'course_id': course.id,
                    'reason': ' | '.join(reasons),
                    'priority': 'high' if similarity_score >= 0.7 else 'medium' if similarity_score >= 0.4 else 'low',
                })
        
        priority_rank = {'high': 3, 'medium': 2, 'low': 1}
        recommendations.sort(key=lambda x: priority_rank.get(x.get('priority', 'medium'), 0), reverse=True)
        return recommendations[:10]  # Top 10
    
    @api.model
    def rule_based_recommendation(self, student_id):
        """
        Rule-Based Recommendation: Áp dụng các luật rõ ràng
        """
        student = self.env['lms.student'].browse(student_id)
        if not student:
            return []
        
        recommendations = []
        
        # Luật 1: Đã học Python Beginner → Gợi ý Python Intermediate
        python_beginner = self.env['lms.course'].search([
            ('name', 'ilike', 'python'),
            ('level_id.name', 'ilike', 'beginner'),
            ('state', '=', 'published'),
        ], limit=1)
        
        if python_beginner:
            student_has_python_beginner = student.enrolled_courses_ids.filtered(
                lambda x: x.course_id.id == python_beginner.id and x.status == 'completed'
            )
            if student_has_python_beginner:
                python_intermediate = self.env['lms.course'].search([
                    ('name', 'ilike', 'python'),
                    ('level_id.name', 'ilike', 'intermediate'),
                    ('state', '=', 'published'),
                    ('id', 'not in', student.enrolled_courses_ids.mapped('course_id').ids),
                ], limit=1)
                if python_intermediate:
                    recommendations.append({
                        'course_id': python_intermediate.id,
                        'reason': 'Luật: Đã hoàn thành Python Beginner → Gợi ý Python Intermediate',
                        'priority': 'high',
                    })
        
        # Luật 2: Không hoạt động > 7 ngày → Gợi ý khóa học dễ hoặc nhắc nhở
        if student.inactive_days > 7:
            easy_courses = self.env['lms.course'].search([
                ('level_id.name', 'ilike', 'beginner'),
                ('state', '=', 'published'),
                ('id', 'not in', student.enrolled_courses_ids.mapped('course_id').ids),
            ], limit=3)
            
            for course in easy_courses:
                recommendations.append({
                    'course_id': course.id,
                    'reason': f'Luật: Không hoạt động {student.inactive_days} ngày → Gợi ý khóa học dễ để khởi động lại',
                    'priority': 'medium',
                })
        
        # Luật 3: Hoàn thành khóa học → Gợi ý khóa học tiếp theo (prerequisite)
        completed_courses = student.enrolled_courses_ids.filtered(
            lambda x: x.status == 'completed'
        ).mapped('course_id')
        
        for course in completed_courses:
            next_courses = self.env['lms.course'].search([
                ('prerequisite_ids', 'in', [course.id]),
                ('state', '=', 'published'),
                ('id', 'not in', student.enrolled_courses_ids.mapped('course_id').ids),
            ])
            
            for next_course in next_courses:
                recommendations.append({
                    'course_id': next_course.id,
                    'reason': f'Luật: Đã hoàn thành {course.name} → Gợi ý khóa học tiếp theo',
                    'priority': 'high',
                })
        
        return recommendations
    
    @api.model
    def _recommend_by_level(self, level):
        """Đề xuất khóa học theo trình độ"""
        courses = self.env['lms.course'].search([
            ('level_id.name', 'ilike', level),
            ('state', '=', 'published'),
            ('is_active', '=', True),
        ], limit=10)
        
        return [{
            'course_id': course.id,
            'reason': f'Đề xuất theo trình độ: {level}',
            'priority': 'medium',
        } for course in courses]
    
    @api.model
    def _is_next_level(self, learned_levels, course_level):
        """Kiểm tra xem course_level có phải là cấp độ tiếp theo không"""
        level_sequence = {
            'beginner': 1,
            'intermediate': 2,
            'advanced': 3,
        }
        
        if not learned_levels or not course_level:
            return False
        
        max_learned = max([level_sequence.get(level.name.lower(), 0) for level in learned_levels])
        course_level_num = level_sequence.get(course_level.name.lower(), 0)
        
        return course_level_num == max_learned + 1

