# -*- coding: utf-8 -*-

from odoo import models, fields, api


class RoadmapWizard(models.TransientModel):
    _name = 'lms.roadmap.wizard'
    _description = 'Wizard tạo roadmap'

    student_id = fields.Many2one('lms.student', string='Học viên', required=True)
    method = fields.Selection([
        ('content_based', 'Content-Based Filtering'),
        ('rule_based', 'Rule-Based Recommendation'),
        ('hybrid', 'Hybrid (Kết hợp)'),
    ], string='Phương pháp đề xuất', default='hybrid', required=True)

    def action_generate(self):
        """Tạo roadmap"""
        self.ensure_one()
        ai_analysis = self.env['lms.ai.analysis']
        
        if self.method == 'content_based':
            recommendations = ai_analysis.content_based_filtering(self.student_id.id)
            # Tạo roadmap từ content-based
            roadmap = self._create_roadmap_from_recommendations(recommendations, 'content_based')
        elif self.method == 'rule_based':
            recommendations = ai_analysis.rule_based_recommendation(self.student_id.id)
            # Tạo roadmap từ rule-based
            roadmap = self._create_roadmap_from_recommendations(recommendations, 'rule_based')
        else:
            # Hybrid
            roadmap = ai_analysis.generate_roadmap(self.student_id.id)
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Roadmap đã tạo',
            'res_model': 'lms.roadmap',
            'res_id': roadmap.id,
            'view_mode': 'form',
            'target': 'current',
        }
    
    def _create_roadmap_from_recommendations(self, recommendations, method):
        """Tạo roadmap từ danh sách đề xuất"""
        roadmap = self.env['lms.roadmap'].create({
            'student_id': self.student_id.id,
            'state': 'suggested',
            'recommendation_method': method,
            'ai_recommendation_reason': f'Đề xuất dựa trên {len(recommendations)} khóa học phù hợp',
        })
        
        for idx, rec in enumerate(recommendations[:20]):
            course = self.env['lms.course'].browse(rec['course_id'])
            
            if course.duration_hours <= 20:
                timeframe = 'short'
            elif course.duration_hours <= 60:
                timeframe = 'medium'
            else:
                timeframe = 'long'
            
            priority = rec.get('priority', 'medium')
            
            self.env['lms.roadmap.course'].create({
                'roadmap_id': roadmap.id,
                'course_id': rec['course_id'],
                'sequence': idx + 1,
                'priority': priority,
                'timeframe': timeframe,
                'recommendation_reason': rec.get('reason', ''),
                'similarity_score': rec.get('similarity_score', 0.0),
            })
        
        return roadmap



