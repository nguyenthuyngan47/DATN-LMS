# -*- coding: utf-8 -*-
{
    'name': 'Learning Management System (LMS)',
    'version': '18.0.1.0.15',
    'category': 'Education',
    'summary': 'Hệ thống quản lý học tập với AI đề xuất khóa học',
    # Giữ description dạng plain text để tránh docutils/RST parse lỗi.
    'description': 'Learning Management System (LMS) với AI đề xuất khóa học.',
    'author': 'LMS Team',
    'depends': ['base', 'base_setup', 'mail', 'portal', 'calendar', 'web', 'auth_signup'],
    'external_dependencies': {
        'python': ['requests', 'python-dotenv', 'google-auth'],
    },
    'pre_init_hook': 'pre_init_hook',
    'post_init_hook': 'post_init_hook',
    'data': [
        'security/lms_groups.xml',
        'security/ir.model.access.csv',
        'security/lms_security.xml',
        'views/student_views.xml',
        'views/student_ai_chat_views.xml',
        'views/course_views.xml',
        'views/lecturer_views.xml',
        'views/learning_history_views.xml',
        'views/roadmap_views.xml',
        'views/analytics_views.xml',
        'views/settings_views.xml',
        'views/menu_views.xml',
        'views/web_login_templates.xml',
        'views/auth_signup_templates.xml',
        'views/res_users_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'lms/static/src/js/ai_roadmap_chat_bridge.js',
            'lms/static/src/js/lesson_video_progress_tracker.js',
        ],
    },
    'demo': [],
    'installable': True,
    'application': True,
    'auto_install': False,
    'license': 'LGPL-3',
}

