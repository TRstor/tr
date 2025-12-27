#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
إعدادات التطبيق
================
جميع الإعدادات والمتغيرات البيئية
"""

import os
import secrets
from datetime import timedelta

# === إعدادات البوت ===
ADMIN_ID = int(os.environ.get("ADMIN_ID", 123456789))
BOT_TOKEN = os.environ.get("BOT_TOKEN", "default_token_123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefgh")
SITE_URL = os.environ.get("SITE_URL", "http://localhost:5000")

# === إعدادات بوابة الدفع EdfaPay ===
EDFAPAY_MERCHANT_ID = os.environ.get("ADFALY_MERCHANT_ID", "")
EDFAPAY_PASSWORD = os.environ.get("ADFALY_PASSWORD", "")
EDFAPAY_API_URL = "https://api.edfapay.com/payment/initiate"

# === إعدادات Firebase ===
FIREBASE_CREDENTIALS = os.environ.get("FIREBASE_CREDENTIALS")
FIREBASE_CREDENTIALS_FILE = "serviceAccountKey.json"

# === إعدادات الأمان ===
SECRET_KEY = os.environ.get("SECRET_KEY")
if not SECRET_KEY or SECRET_KEY == "your-secret-key-here-change-it":
    SECRET_KEY = secrets.token_hex(32)
    print("⚠️ تم توليد مفتاح سري جديد (يُفضل تعيين SECRET_KEY في متغيرات البيئة)")

# === بيئة التشغيل ===
IS_PRODUCTION = os.environ.get("RENDER", False) or os.environ.get("PRODUCTION", False)

# === إعدادات الجلسة ===
SESSION_CONFIG = {
    'SESSION_COOKIE_SECURE': IS_PRODUCTION,
    'SESSION_COOKIE_HTTPONLY': True,
    'SESSION_COOKIE_SAMESITE': 'Strict',
    'PERMANENT_SESSION_LIFETIME': timedelta(minutes=30),
    'SESSION_COOKIE_NAME': 'tr_session',
}

# === إعدادات Rate Limiting ===
RATE_LIMIT_DEFAULT = ["200 per day", "50 per hour"]
RATE_LIMIT_STORAGE = "memory://"

# === إعدادات السلة ===
CART_EXPIRY_HOURS = 3  # ساعات انتهاء السلة

# === الأقسام الافتراضية ===
DEFAULT_CATEGORIES = [
    {'id': '1', 'name': 'نتفلكس', 'image_url': 'https://i.imgur.com/netflix.png', 'order': 1, 'delivery_type': 'instant'},
    {'id': '2', 'name': 'شاهد', 'image_url': 'https://i.imgur.com/shahid.png', 'order': 2, 'delivery_type': 'instant'},
    {'id': '3', 'name': 'ديزني بلس', 'image_url': 'https://i.imgur.com/disney.png', 'order': 3, 'delivery_type': 'instant'},
    {'id': '4', 'name': 'اوسن بلس', 'image_url': 'https://i.imgur.com/osn.png', 'order': 4, 'delivery_type': 'instant'},
    {'id': '5', 'name': 'فديو بريميم', 'image_url': 'https://i.imgur.com/vedio.png', 'order': 5, 'delivery_type': 'instant'},
    {'id': '6', 'name': 'اشتراكات أخرى', 'image_url': 'https://i.imgur.com/other.png', 'order': 6, 'delivery_type': 'manual'}
]

# === إعدادات العرض ===
DISPLAY_SETTINGS = {
    'categories_columns': 3
}
