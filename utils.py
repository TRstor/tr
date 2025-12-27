#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
أدوات مساعدة
=============
دوال عامة مستخدمة في التطبيق
"""

import html
import secrets
import time
from flask import session

# === دالة تنظيف XSS ===
def sanitize(text):
    """تنظيف النص من أكواد HTML/JS الخبيثة"""
    if text is None:
        return None
    if not isinstance(text, str):
        return text
    return html.escape(str(text))

# === دالة تجديد الجلسة ===
def regenerate_session():
    """تجديد ID الجلسة لمنع Session Fixation"""
    old_data = dict(session)
    session.clear()
    session.update(old_data)
    session.modified = True

# === دالة توليد كود عشوائي ===
def generate_code(length=6):
    """توليد كود عشوائي"""
    return ''.join([str(secrets.randbelow(10)) for _ in range(length)])

# === دالة توليد معرف فريد ===
def generate_order_id(prefix='TR', user_id=''):
    """توليد معرف فريد للطلب"""
    return f"{prefix}{user_id}{int(time.time())}"

# === دالة التحقق من الوقت ===
def is_expired(timestamp, hours=1):
    """التحقق من انتهاء الصلاحية"""
    if not timestamp:
        return True
    return time.time() > timestamp + (hours * 3600)

# === دالة تنسيق الوقت ===
def format_time_remaining(expires_at):
    """تنسيق الوقت المتبقي"""
    remaining = expires_at - time.time()
    if remaining <= 0:
        return "منتهي"
    
    hours = int(remaining // 3600)
    minutes = int((remaining % 3600) // 60)
    
    if hours > 0:
        return f"{hours} ساعة و {minutes} دقيقة"
    return f"{minutes} دقيقة"

# === دالة التحقق من رقم الجوال ===
def validate_phone(phone):
    """التحقق من صحة رقم الجوال"""
    if not phone:
        return False
    # إزالة المسافات والرموز
    phone = phone.replace(' ', '').replace('-', '').replace('+', '')
    # التحقق من الطول
    return len(phone) >= 9 and phone.isdigit()

# === دالة تنظيف رقم الجوال ===
def clean_phone(phone, country_code='966'):
    """تنظيف وتنسيق رقم الجوال"""
    if not phone:
        return ''
    phone = phone.replace(' ', '').replace('-', '').replace('+', '')
    if phone.startswith('0'):
        phone = phone[1:]
    if not phone.startswith(country_code):
        phone = country_code + phone
    return phone

# === دالة التحقق من المبلغ ===
def validate_amount(amount, min_amount=10, max_amount=5000):
    """التحقق من صحة المبلغ"""
    try:
        amount = float(amount)
        return min_amount <= amount <= max_amount
    except (ValueError, TypeError):
        return False

# === محاولات الدخول الفاشلة ===
failed_login_attempts = {}

def check_rate_limit(ip, max_attempts=5, block_time=300):
    """التحقق من عدد المحاولات الفاشلة"""
    if ip not in failed_login_attempts:
        return True
    
    data = failed_login_attempts[ip]
    
    # التحقق من الحظر
    if data.get('blocked_until', 0) > time.time():
        return False
    
    # إعادة تعيين إذا انتهى الحظر
    if data.get('blocked_until', 0) < time.time():
        failed_login_attempts[ip] = {'count': 0, 'blocked_until': 0}
    
    return True

def record_failed_attempt(ip, max_attempts=5, block_time=300):
    """تسجيل محاولة فاشلة"""
    if ip not in failed_login_attempts:
        failed_login_attempts[ip] = {'count': 0, 'blocked_until': 0}
    
    failed_login_attempts[ip]['count'] += 1
    
    if failed_login_attempts[ip]['count'] >= max_attempts:
        failed_login_attempts[ip]['blocked_until'] = time.time() + block_time

def reset_failed_attempts(ip):
    """إعادة تعيين المحاولات الفاشلة"""
    if ip in failed_login_attempts:
        del failed_login_attempts[ip]
