#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
extensions.py - الكائنات المشتركة بين الملفات
يحل مشكلة Circular Imports
"""

import os
import logging
import firebase_admin
from firebase_admin import credentials, firestore

# إعداد التسجيل
logging.basicConfig(level=logging.ERROR)
logger = logging.getLogger(__name__)

# --- Firebase ---
db = None
FIREBASE_AVAILABLE = False

def init_firebase():
    """تهيئة Firebase"""
    global db, FIREBASE_AVAILABLE
    
    try:
        if not firebase_admin._apps:
            cred_path = os.getenv('FIREBASE_CREDENTIALS', 'serviceAccountKey.json')
            if os.path.exists(cred_path):
                cred = credentials.Certificate(cred_path)
                firebase_admin.initialize_app(cred)
                db = firestore.client()
                FIREBASE_AVAILABLE = True
                print("✅ تم الاتصال بـ Firebase بنجاح")
            else:
                print(f"⚠️ ملف Firebase غير موجود: {cred_path}")
    except Exception as e:
        print(f"⚠️ Firebase غير متاح: {e}")
        FIREBASE_AVAILABLE = False
    
    return db

# --- الإعدادات الأساسية ---
ADMIN_ID = os.getenv('ADMIN_ID', '8185aboraa')
TOKEN = os.getenv('BOT_TOKEN', 'default_token_change_me')
SITE_URL = os.getenv('SITE_URL', 'https://tr-ozni.onrender.com')
SECRET_KEY = os.getenv('SECRET_KEY', '')

# EdfaPay
EDFAPAY_MERCHANT_ID = os.getenv('EDFAPAY_MERCHANT_ID', '')
EDFAPAY_PASSWORD = os.getenv('EDFAPAY_PASSWORD', '')

# --- البيانات المشتركة (في الذاكرة) ---
users_wallets = {}          # أرصدة المستخدمين
marketplace_items = []       # المنتجات
categories_list = []         # الأقسام
verification_codes = {}      # أكواد التحقق
user_states = {}            # حالات المستخدمين (للبوت)
user_carts = {}             # سلات المستخدمين
display_settings = {'categories_columns': 3}

# --- تهيئة Firebase عند الاستيراد ---
init_firebase()
