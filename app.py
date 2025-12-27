#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import html
import logging
import telebot
from telebot import types
from flask import Flask, request, render_template_string, render_template, redirect, session, jsonify
import json
import random
import hashlib
import time
import uuid
import requests
import firebase_admin
from firebase_admin import credentials, firestore

# إعداد التسجيل للأخطاء (السيرفر فقط)
logging.basicConfig(level=logging.ERROR)
logger = logging.getLogger(__name__)

# محاولة استيراد FieldFilter للنسخ الجديدة
try:
    from google.cloud.firestore_v1.base_query import FieldFilter
    USE_FIELD_FILTER = True
except ImportError:
    USE_FIELD_FILTER = False

# --- إعدادات Firebase ---
# التحقق من وجود متغير البيئة أولاً (للإنتاج في Render)
firebase_credentials_json = os.environ.get("FIREBASE_CREDENTIALS")
db = None

try:
    if firebase_credentials_json:
        # استخدام المتغير البيئي (Render)
        cred_dict = json.loads(firebase_credentials_json)
        cred = credentials.Certificate(cred_dict)
        print("✅ Firebase: استخدام المتغير البيئي (Production)")
    else:
        # استخدام الملف المحلي (للتطوير)
        if os.path.exists('serviceAccountKey.json'):
            cred = credentials.Certificate('serviceAccountKey.json')
            print("✅ Firebase: استخدام الملف المحلي (Development)")
        else:
            raise FileNotFoundError("Firebase credentials not found")

    firebase_admin.initialize_app(cred)
    db = firestore.client()
except Exception as e:
    print(f"⚠️ Firebase غير متاح: {e}")
    print("⚠️ سيتم العمل بدون قاعدة بيانات Firebase (في الذاكرة فقط)")
    db = None

# --- إعدادات البوت ---
# آيدي المالك - يجب تعيينه في متغيرات البيئة (ADMIN_ID) في Render
# القيمة الافتراضية وهمية للأمان - لن تعمل بدون تعيين الآيدي الحقيقي
ADMIN_ID = int(os.environ.get("ADMIN_ID", 123456789))
TOKEN = os.environ.get("BOT_TOKEN", "default_token_123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefgh")
SITE_URL = os.environ.get("SITE_URL", "http://localhost:5000")

# --- إعدادات بوابة الدفع EdfaPay ---
EDFAPAY_MERCHANT_ID = os.environ.get("ADFALY_MERCHANT_ID", "")
EDFAPAY_PASSWORD = os.environ.get("ADFALY_PASSWORD", "")
EDFAPAY_API_URL = "https://api.edfapay.com/payment/initiate"

# دالة تسجيل Callback URL في EdfaPay
def register_edfapay_callback():
    """تسجيل رابط الـ webhook في EdfaPay"""
    if not EDFAPAY_MERCHANT_ID:
        print("⚠️ لا يوجد MERCHANT_ID لتسجيل الـ callback")
        return False
    
    try:
        callback_url = f"{SITE_URL}/payment/edfapay_webhook"
        
        response = requests.post(
            "https://api.edfapay.com/payment/merchants/callback-url",
            json={
                "action": "post",
                "id": EDFAPAY_MERCHANT_ID,
                "url": callback_url
            },
            timeout=30
        )
        
        print(f"📡 تسجيل Callback URL: {response.status_code}")
        print(f"📡 Response: {response.text}")
        
        if response.status_code == 200:
            print(f"✅ تم تسجيل Callback URL: {callback_url}")
            return True
        else:
            print(f"❌ فشل تسجيل Callback URL")
            return False
    except Exception as e:
        print(f"❌ خطأ في تسجيل Callback: {e}")
        return False

# دالة التحقق من Callback URL المسجل
def check_edfapay_callback():
    """التحقق من رابط الـ webhook المسجل في EdfaPay"""
    if not EDFAPAY_MERCHANT_ID:
        return None
    
    try:
        response = requests.post(
            "https://api.edfapay.com/payment/merchants/callback-url",
            json={
                "action": "get",
                "id": EDFAPAY_MERCHANT_ID
            },
            timeout=30
        )
        
        if response.status_code == 200:
            return response.json()
        return None
    except Exception as e:
        print(f"❌ خطأ في التحقق من Callback: {e}")
        return None

# التحقق من أن التوكن صحيح (ليس القيمة الافتراضية)
if TOKEN.startswith("default_token"):
    print("⚠️ BOT_TOKEN غير محدد - استخدم متغير البيئة BOT_TOKEN")
    bot = telebot.TeleBot("123456789:dummy_token")  # إنشاء بوت وهمي لتجنب الأخطاء
    BOT_ACTIVE = False
    BOT_USERNAME = ""
else:
    try:
        bot = telebot.TeleBot(TOKEN)
        # إعداد البوت لتجنب خطأ 429 (Too Many Requests)
        telebot.apihelper.RETRY_ON_ERROR = True
        BOT_ACTIVE = True
        # جلب اسم البوت
        try:
            bot_info = bot.get_me()
            BOT_USERNAME = bot_info.username
            print(f"✅ البوت: متصل بنجاح (@{BOT_USERNAME})")
        except:
            BOT_USERNAME = ""
            print(f"✅ البوت: متصل بنجاح")
    except Exception as e:
        BOT_ACTIVE = False
        BOT_USERNAME = ""
        bot = telebot.TeleBot("dummy_token")  # إنشاء بوت وهمي لتجنب الأخطاء
        print(f"⚠️ البوت غير متاح: {e}")

app = Flask(__name__)

# --- إعدادات الأمان للجلسات ---
import secrets
from datetime import timedelta

# توليد مفتاح سري قوي (أو استخدام المتغير البيئي)
SECRET_KEY = os.environ.get("SECRET_KEY")
if not SECRET_KEY or SECRET_KEY == "your-secret-key-here-change-it":
    SECRET_KEY = secrets.token_hex(32)  # 64 حرف عشوائي
    print("⚠️ تم توليد مفتاح سري جديد (يُفضل تعيين SECRET_KEY في متغيرات البيئة)")

app.secret_key = SECRET_KEY

# إعدادات الكوكيز الآمنة
# SESSION_COOKIE_SECURE=False للتطوير المحلي، True للإنتاج
IS_PRODUCTION = os.environ.get("RENDER", False) or os.environ.get("PRODUCTION", False)
app.config.update(
    SESSION_COOKIE_SECURE=IS_PRODUCTION,        
    SESSION_COOKIE_HTTPONLY=True,     
    SESSION_COOKIE_SAMESITE='Strict',  # ✅ تقوية من Lax إلى Strict
    PERMANENT_SESSION_LIFETIME=timedelta(minutes=30),  
    SESSION_COOKIE_NAME='tr_session',  
)

# --- Rate Limiting (تحديد المحاولات) ---
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    default_limits=["200 per day", "50 per hour"],
    storage_uri="memory://",
)

# --- دالة تنظيف XSS ---
def sanitize(text):
    """تنظيف النص من أكواد HTML/JS الخبيثة"""
    if text is None:
        return None
    if not isinstance(text, str):
        return text
    return html.escape(str(text))

# --- Security Headers ---
@app.after_request
def add_security_headers(response):
    """إضافة رؤوس أمان للحماية من الهجمات"""
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    return response

# دالة لتجديد الجلسة بعد تسجيل الدخول
def regenerate_session():
    """تجديد ID الجلسة لمنع Session Fixation"""
    old_data = dict(session)
    session.clear()
    session.update(old_data)
    session.modified = True

# --- قواعد البيانات ---
# جميع البيانات تُحفظ في Firebase (الإنتاج) وتُحمل في الذاكرة للعرض السريع

# قائمة المنتجات/الخدمات
# الشكل: { item_name, price, seller_id, seller_name, hidden_data, image_url, category }
marketplace_items = []

# الطلبات النشطة (قيد التنفيذ بواسطة المشرفين)
# الشكل: { order_id: {buyer_info, item_info, admin_id, status, message_id} }
active_orders = {}

# بيانات المستخدمين (الرصيد)
# الشكل: { user_id: balance }
users_wallets = {}

# العمليات المعلقة (المبالغ المحجوزة)
transactions = {}

# رموز التحقق للمستخدمين
# الشكل: { user_id: {code, name, created_at} }
verification_codes = {}

# أكواد دخول لوحة التحكم المؤقتة
# الشكل: { 'code': code, 'created_at': time, 'used': False, 'ip': ip }
admin_login_codes = {}

# محاولات الدخول الفاشلة (للحماية من brute force)
# الشكل: { ip: {'count': n, 'blocked_until': time} }
failed_login_attempts = {}

# مفاتيح الشحن المولدة
# الشكل: { key_code: {amount, used, used_by, created_at} }
charge_keys = {}

# حالات المستخدمين (لمتابعة المحادثة - مثل انتظار إدخال مبلغ الشحن)
# الشكل: { user_id: {'state': 'waiting_amount', 'data': {}} }
user_states = {}

# سلة التسوق (تُحمل من Firebase)
# الشكل: { user_id: { items: [...], created_at, expires_at } }
user_carts = {}

# طلبات الدفع المعلقة (تنتظر الدفع من Adfaly Pay)
# الشكل: { invoice_id: {user_id, amount, status, created_at} }
pending_payments = {}

# الفواتير المنشأة من التجار (للعملاء)
# الشكل: { invoice_id: {merchant_id, merchant_name, amount, customer_phone, status, created_at} }
merchant_invoices = {}

# قائمة الأقسام الديناميكية
# الشكل: { id: {name, image_url, order, delivery_type, created_at} }
categories_list = [
    {'id': '1', 'name': 'نتفلكس', 'image_url': 'https://i.imgur.com/netflix.png', 'order': 1, 'delivery_type': 'instant'},
    {'id': '2', 'name': 'شاهد', 'image_url': 'https://i.imgur.com/shahid.png', 'order': 2, 'delivery_type': 'instant'},
    {'id': '3', 'name': 'ديزني بلس', 'image_url': 'https://i.imgur.com/disney.png', 'order': 3, 'delivery_type': 'instant'},
    {'id': '4', 'name': 'اوسن بلس', 'image_url': 'https://i.imgur.com/osn.png', 'order': 4, 'delivery_type': 'instant'},
    {'id': '5', 'name': 'فديو بريميم', 'image_url': 'https://i.imgur.com/vedio.png', 'order': 5, 'delivery_type': 'instant'},
    {'id': '6', 'name': 'اشتراكات أخرى', 'image_url': 'https://i.imgur.com/other.png', 'order': 6, 'delivery_type': 'manual'}
]

# إعدادات العرض (ترتيب الأقسام)
display_settings = {
    'categories_columns': 3  # عدد الأعمدة: 2 أو 3 أو 4
}

# دالة تحميل جميع البيانات من Firebase عند بدء التطبيق
def load_all_data_from_firebase():
    """تحميل جميع البيانات من Firebase عند بدء التطبيق"""
    global marketplace_items, users_wallets, charge_keys, active_orders, categories_list, display_settings
    
    if not db:
        print("⚠️ Firebase غير متاح - سيتم استخدام البيانات الفارغة")
        return
    
    try:
        print("📥 جاري تحميل البيانات من Firebase...")
        
        # 1️⃣ تحميل المنتجات (المتاحة فقط)
        try:
            products_ref = query_where(db.collection('products'), 'sold', '==', False)
            marketplace_items = []
            count = 0
            for doc in products_ref.stream():
                data = doc.to_dict()
                data['id'] = doc.id
                marketplace_items.append(data)
                count += 1
            print(f"✅ تم تحميل {count} منتج متاح")
        except Exception as e:
            print(f"⚠️ خطأ في تحميل المنتجات: {e}")
        
        # 2️⃣ تحميل أرصدة المستخدمين
        try:
            users_ref = db.collection('users')
            users_wallets = {}
            count = 0
            for doc in users_ref.stream():
                data = doc.to_dict()
                users_wallets[doc.id] = data.get('balance', 0.0)
                count += 1
            print(f"✅ تم تحميل أرصدة {count} مستخدم")
        except Exception as e:
            print(f"⚠️ خطأ في تحميل أرصدة المستخدمين: {e}")
        
        # 3️⃣ تحميل مفاتيح الشحن (غير المستخدمة)
        try:
            keys_ref = query_where(db.collection('charge_keys'), 'used', '==', False)
            charge_keys = {}
            count = 0
            for doc in keys_ref.stream():
                data = doc.to_dict()
                charge_keys[doc.id] = {
                    'amount': data.get('amount', 0),
                    'used': data.get('used', False),
                    'used_by': data.get('used_by'),
                    'created_at': data.get('created_at', time.time())
                }
                count += 1
            print(f"✅ تم تحميل {count} مفتاح شحن نشط")
        except Exception as e:
            print(f"⚠️ خطأ في تحميل مفاتيح الشحن: {e}")
        
        # 4️⃣ تحميل الطلبات النشطة (pending أو claimed)
        try:
            active_orders = {}
            # تحميل الطلبات النشطة
            orders_ref = db.collection('orders')
            orders_query = orders_ref.where('status', 'in', ['pending', 'claimed'])
            for doc in orders_query.stream():
                data = doc.to_dict()
                active_orders[doc.id] = data
            print(f"✅ تم تحميل {len(active_orders)} طلب نشط")
        except Exception as e:
            print(f"⚠️ خطأ في تحميل الطلبات: {e}")
        
        # 5️⃣ تحميل الأقسام
        try:
            cats_ref = db.collection('categories').order_by('order')
            loaded_cats = []
            for doc in cats_ref.stream():
                data = doc.to_dict()
                data['id'] = doc.id
                loaded_cats.append(data)
            if loaded_cats:
                categories_list = loaded_cats
                print(f"✅ تم تحميل {len(categories_list)} قسم")
            else:
                print(f"ℹ️ لا توجد أقسام في Firebase - استخدام الأقسام الافتراضية ({len(categories_list)})")
        except Exception as e:
            print(f"⚠️ خطأ في تحميل الأقسام: {e}")
        
        # 6️⃣ تحميل إعدادات العرض
        try:
            settings_doc = db.collection('settings').document('display').get()
            if settings_doc.exists:
                settings_data = settings_doc.to_dict()
                display_settings['categories_columns'] = settings_data.get('categories_columns', 3)
                print(f"✅ تم تحميل إعدادات العرض (أعمدة: {display_settings['categories_columns']})")
        except Exception as e:
            print(f"⚠️ خطأ في تحميل إعدادات العرض: {e}")
        
        # 7️⃣ تحميل سلات التسوق النشطة
        try:
            global user_carts
            carts_ref = db.collection('carts')
            for doc in carts_ref.stream():
                data = doc.to_dict()
                # التحقق من صلاحية السلة (3 ساعات)
                expires_at = data.get('expires_at')
                if expires_at:
                    from datetime import datetime
                    if isinstance(expires_at, datetime):
                        if expires_at.replace(tzinfo=None) > datetime.utcnow():
                            user_carts[doc.id] = data
                else:
                    user_carts[doc.id] = data
            print(f"✅ تم تحميل {len(user_carts)} سلة تسوق نشطة")
        except Exception as e:
            print(f"⚠️ خطأ في تحميل سلات التسوق: {e}")
        
        print("🎉 اكتمل تحميل البيانات من Firebase!")
        
    except Exception as e:
        print(f"❌ خطأ عام في تحميل البيانات: {e}")

# دالة للتعامل مع where بالطريقة المتوافقة
def query_where(collection_ref, field, op, value):
    """استخدام where بطريقة متوافقة مع جميع النسخ"""
    if USE_FIELD_FILTER:
        return collection_ref.where(filter=FieldFilter(field, op, value))
    else:
        return collection_ref.where(field, op, value)

# --- دوال مساعدة ---

def get_user_profile_photo(user_id):
    """جلب صورة البروفايل من تيليجرام"""
    try:
        photos = bot.get_user_profile_photos(int(user_id), limit=1)
        if photos.total_count > 0:
            file_id = photos.photos[0][0].file_id
            file_info = bot.get_file(file_id)
            photo_url = f"https://api.telegram.org/file/bot{TOKEN}/{file_info.file_path}"
            return photo_url
        return None
    except Exception as e:
        print(f"⚠️ خطأ في جلب صورة البروفايل: {e}")
        return None

def get_balance(user_id):
    """جلب الرصيد من Firebase"""
    try:
        uid = str(user_id)
        doc = db.collection('users').document(uid).get()
        if doc.exists:
            return doc.to_dict().get('balance', 0.0)
        return 0.0
    except Exception as e:
        print(f"⚠️ خطأ في جلب الرصيد: {e}")
        return users_wallets.get(str(user_id), 0.0)

def add_balance(user_id, amount):
    """إضافة رصيد للمستخدم في Firebase والذاكرة"""
    uid = str(user_id)
    if uid not in users_wallets:
        users_wallets[uid] = 0.0
    users_wallets[uid] += float(amount)
    
    # حفظ في Firebase
    try:
        db.collection('users').document(uid).set({
            'balance': users_wallets[uid],
            'telegram_id': uid,
            'updated_at': firestore.SERVER_TIMESTAMP
        }, merge=True)
        print(f"✅ تم حفظ رصيد المستخدم {uid}: {users_wallets[uid]} ريال في Firestore")
    except Exception as e:
        print(f"❌ خطأ في حفظ الرصيد إلى Firebase: {e}")

# إضافة UUID للمنتجات الموجودة (إذا لم يكن لديها ID)
def ensure_product_ids():
    for item in marketplace_items:
        if 'id' not in item:
            item['id'] = str(uuid.uuid4())

# دالة لرفع البيانات من الذاكرة إلى Firebase
def migrate_data_to_firebase():
    """نقل البيانات من المتغيرات في الذاكرة إلى Firebase"""
    try:
        print("🔄 بدء نقل البيانات إلى Firebase...")
        
        # 1. رفع المنتجات
        if marketplace_items:
            products_ref = db.collection('products')
            for item in marketplace_items:
                product_id = item.get('id', str(uuid.uuid4()))
                products_ref.document(product_id).set({
                    'item_name': item.get('item_name', ''),
                    'price': float(item.get('price', 0)),
                    'seller_id': str(item.get('seller_id', '')),
                    'seller_name': item.get('seller_name', ''),
                    'hidden_data': item.get('hidden_data', ''),
                    'image_url': item.get('image_url', ''),
                    'category': item.get('category', 'أخرى'),
                    'details': item.get('details', ''),
                    'sold': item.get('sold', False),
                    'created_at': firestore.SERVER_TIMESTAMP
                })
            print(f"✅ تم رفع {len(marketplace_items)} منتج")
        
        # 2. رفع أرصدة المستخدمين
        if users_wallets:
            users_ref = db.collection('users')
            for user_id, balance in users_wallets.items():
                users_ref.document(str(user_id)).set({
                    'balance': float(balance),
                    'telegram_id': str(user_id),
                    'updated_at': firestore.SERVER_TIMESTAMP
                }, merge=True)
            print(f"✅ تم رفع {len(users_wallets)} مستخدم")
        
        # 3. رفع الطلبات النشطة
        if active_orders:
            orders_ref = db.collection('orders')
            for order_id, order_data in active_orders.items():
                orders_ref.document(str(order_id)).set({
                    'item_name': order_data.get('item_name', ''),
                    'price': float(order_data.get('price', 0)),
                    'buyer_id': str(order_data.get('buyer_id', '')),
                    'buyer_name': order_data.get('buyer_name', ''),
                    'seller_id': str(order_data.get('seller_id', '')),
                    'status': order_data.get('status', 'pending'),
                    'admin_id': str(order_data.get('admin_id', '')) if order_data.get('admin_id') else '',
                    'created_at': firestore.SERVER_TIMESTAMP
                })
            print(f"✅ تم رفع {len(active_orders)} طلب")
        
        # 4. رفع مفاتيح الشحن
        if charge_keys:
            keys_ref = db.collection('charge_keys')
            for key_code, key_data in charge_keys.items():
                keys_ref.document(key_code).set({
                    'amount': float(key_data.get('amount', 0)),
                    'used': key_data.get('used', False),
                    'used_by': str(key_data.get('used_by', '')) if key_data.get('used_by') else '',
                    'created_at': key_data.get('created_at', time.time())
                })
            print(f"✅ تم رفع {len(charge_keys)} مفتاح شحن")
        
        print("🎉 تم رفع جميع البيانات إلى Firebase بنجاح!")
        return True
        
    except Exception as e:
        print(f"❌ خطأ في رفع البيانات: {e}")
        return False

# دالة لتحميل البيانات من Firebase إلى الذاكرة (عند بدء التشغيل)
def load_data_from_firebase():
    """تحميل البيانات من Firebase إلى المتغيرات في الذاكرة للاستخدام السريع"""
    global marketplace_items, users_wallets, charge_keys, active_orders
    
    try:
        print("📥 بدء تحميل البيانات من Firebase...")
        
        # 1. تحميل المنتجات (غير المباعة فقط)
        print("🔄 جاري تحميل المنتجات من Firestore...")
        products_ref = query_where(db.collection('products'), 'sold', '==', False)
        marketplace_items = []
        for doc in products_ref.stream():
            data = doc.to_dict()
            data['id'] = doc.id
            marketplace_items.append(data)
            print(f"  📦 منتج: {data.get('item_name', 'بدون اسم')} - {data.get('price', 0)} ريال")
        print(f"✅ تم تحميل {len(marketplace_items)} منتج من Firestore")
        
        # 2. تحميل أرصدة المستخدمين
        print("🔄 جاري تحميل المستخدمين من Firestore...")
        users_ref = db.collection('users')
        users_wallets = {}
        for doc in users_ref.stream():
            data = doc.to_dict()
            users_wallets[doc.id] = data.get('balance', 0.0)
            print(f"  👤 مستخدم {doc.id}: {data.get('balance', 0)} ريال")
        print(f"✅ تم تحميل {len(users_wallets)} مستخدم من Firestore")
        
        # 3. تحميل مفاتيح الشحن (غير المستخدمة فقط)
        keys_ref = query_where(db.collection('charge_keys'), 'used', '==', False)
        charge_keys = {}
        for doc in keys_ref.stream():
            data = doc.to_dict()
            charge_keys[doc.id] = {
                'amount': data.get('amount', 0),
                'used': data.get('used', False),
                'used_by': data.get('used_by'),
                'created_at': data.get('created_at', time.time())
            }
        print(f"✅ تم تحميل {len(charge_keys)} مفتاح شحن")
        
        # 4. تحميل الطلبات النشطة (pending فقط)
        orders_ref = query_where(db.collection('orders'), 'status', '==', 'pending')
        active_orders = {}
        for doc in orders_ref.stream():
            data = doc.to_dict()
            active_orders[doc.id] = data
        print(f"✅ تم تحميل {len(active_orders)} طلب نشط")
        
        print("🎉 تم تحميل جميع البيانات من Firebase بنجاح!")
        return True
        
    except Exception as e:
        print(f"⚠️ تحذير: لم يتم تحميل البيانات من Firebase: {e}")
        print("سيتم البدء ببيانات فارغة")
        return False

# دالة لتوليد كود تحقق عشوائي
def generate_verification_code(user_id, user_name):
    # توليد كود من 6 أرقام
    code = str(random.randint(100000, 999999))
    
    # حفظ الكود (صالح لمدة 10 دقائق)
    verification_codes[str(user_id)] = {
        'code': code,
        'name': user_name,
        'created_at': time.time()
    }
    
    return code

# دالة للتحقق من صحة الكود
def verify_code(user_id, code):
    user_id = str(user_id)
    
    if user_id not in verification_codes:
        return None
    
    code_data = verification_codes[user_id]
    
    # التحقق من صلاحية الكود (10 دقائق)
    if time.time() - code_data['created_at'] > 600:  # 10 * 60 ثانية
        del verification_codes[user_id]
        return None
    
    # التحقق من تطابق الكود
    if code_data['code'] != code:
        return None
    
    return code_data

# --- كود صفحة الويب (HTML + JavaScript) ---

# --- أوامر البوت ---

# دالة مساعدة لتسجيل الرسائل
def log_message(message, handler_name):
    print("="*50)
    print(f"📨 {handler_name}")
    print(f"👤 المستخدم: {message.from_user.id} - {message.from_user.first_name}")
    print(f"💬 النص: {message.text}")
    print("="*50)

@bot.message_handler(commands=['start'])
def send_welcome(message):
    log_message(message, "معالج /start")
    try:
        user_id = str(message.from_user.id)
        user_name = message.from_user.first_name
        if message.from_user.last_name:
            user_name += ' ' + message.from_user.last_name
        username = message.from_user.username or ''
        
        # جلب صورة البروفايل من تيليجرام
        profile_photo = get_user_profile_photo(user_id)
        
        # حفظ معلومات المستخدم في Firebase
        if db:
            try:
                user_ref = db.collection('users').document(user_id)
                user_doc = user_ref.get()
                
                if not user_doc.exists:
                    user_data = {
                        'telegram_id': user_id,
                        'name': user_name,
                        'username': username,
                        'balance': 0.0,
                        'telegram_started': True,  # المستخدم بدأ محادثة مع البوت
                        'created_at': firestore.SERVER_TIMESTAMP,
                        'last_seen': firestore.SERVER_TIMESTAMP
                    }
                    if profile_photo:
                        user_data['profile_photo'] = profile_photo
                    user_ref.set(user_data)
                    users_wallets[user_id] = 0.0
                    print(f"✅ مستخدم جديد تم إنشاؤه")
                else:
                    update_data = {
                        'name': user_name,
                        'username': username,
                        'telegram_started': True,  # تحديث: المستخدم بدأ محادثة مع البوت
                        'last_seen': firestore.SERVER_TIMESTAMP
                    }
                    if profile_photo:
                        update_data['profile_photo'] = profile_photo
                    user_ref.update(update_data)
                    print(f"✅ مستخدم موجود تم تحديثه")
            except Exception as e:
                print(f"⚠️ خطأ في Firebase: {e}")
        
        # إنشاء أزرار Inline داخل الرسالة
        markup = types.InlineKeyboardMarkup(row_width=2)
        btn_shop = types.InlineKeyboardButton("🏪 افتح السوق", callback_data="open_shop")
        btn_code = types.InlineKeyboardButton("🔐 كود الدخول", callback_data="get_code")
        btn_myid = types.InlineKeyboardButton("🆔 معرفي", callback_data="my_id")
        markup.add(btn_shop)
        markup.add(btn_code, btn_myid)
        
        # إرسال الرسالة
        print(f"📤 إرسال رسالة الترحيب...")
        result = bot.send_message(
            message.chat.id,
            "🌟 *أهلاً بك في السوق الآمن!* 🛡️\n\n"
            "منصة آمنة للبيع والشراء مع نظام حماية الأموال ❄️\n\n"
            "📌 *اختر من الأزرار أدناه:*",
            reply_markup=markup,
            parse_mode="Markdown"
        )
        print(f"✅ تم الإرسال! message_id: {result.message_id}")
        
    except Exception as e:
        print(f"❌ خطأ في send_welcome: {e}")
        import traceback
        traceback.print_exc()

# معالج أزرار Inline
@bot.callback_query_handler(func=lambda call: call.data in ["open_shop", "get_code", "my_id"])
def handle_inline_buttons(call):
    try:
        if call.data == "open_shop":
            # إرسال زر برابط الموقع
            markup = types.InlineKeyboardMarkup()
            btn = types.InlineKeyboardButton("🛒 الدخول للسوق", url=SITE_URL)
            markup.add(btn)
            bot.send_message(
                call.message.chat.id,
                f"🏪 *اضغط الزر أدناه لفتح السوق:*\n\n"
                f"🔗 الرابط: {SITE_URL}",
                reply_markup=markup,
                parse_mode="Markdown"
            )
        elif call.data == "get_code":
            # إنشاء كود التحقق
            user_id = str(call.from_user.id)
            user_name = call.from_user.first_name
            if call.from_user.last_name:
                user_name += ' ' + call.from_user.last_name
            code = str(random.randint(100000, 999999))
            verification_codes[user_id] = {
                'code': code,
                'name': user_name,
                'created_at': time.time()
            }
            bot.send_message(
                call.message.chat.id,
                f"🔐 *كود الدخول الخاص بك:*\n\n"
                f"`{code}`\n\n"
                f"⏱ صالح لمدة 10 دقائق\n"
                f"📋 انسخ الكود وأدخله في الموقع",
                parse_mode="Markdown"
            )
        elif call.data == "my_id":
            bot.send_message(
                call.message.chat.id,
                f"🆔 *الآيدي الخاص بك:*\n\n`{call.from_user.id}`\n\nأرسل هذا الرقم للمالك ليضيفك كمشرف!",
                parse_mode="Markdown"
            )
        # إزالة علامة التحميل من الزر
        bot.answer_callback_query(call.id)
    except Exception as e:
        print(f"❌ خطأ في inline button: {e}")
        bot.answer_callback_query(call.id, "حدث خطأ!")

@bot.message_handler(commands=['my_id'])
def my_id(message):
    log_message(message, "معالج /my_id")
    try:
        bot.reply_to(message, f"🆔 الآيدي الخاص بك: `{message.from_user.id}`\n\nأرسل هذا الرقم للمالك ليضيفك كمشرف!", parse_mode="Markdown")
        print(f"✅ تم إرسال الآيدي")
    except Exception as e:
        print(f"❌ خطأ: {e}")

# تخزين بيانات المنتج المؤقتة
temp_product_data = {}

# أمر إضافة منتج (فقط للمالك)
@bot.message_handler(commands=['add_product'])
def add_product_command(message):
    # التحقق من أن المستخدم هو المالك
    if message.from_user.id != ADMIN_ID:
        return bot.reply_to(message, "⛔ هذا الأمر للمالك فقط!")
    
    # بدء عملية إضافة منتج جديد
    user_id = message.from_user.id
    temp_product_data[user_id] = {}
    
    msg = bot.reply_to(message, "📦 **إضافة منتج جديد**\n\n📝 أرسل اسم المنتج:", parse_mode="Markdown")
    bot.register_next_step_handler(msg, process_product_name)

def process_product_name(message):
    user_id = message.from_user.id
    
    if message.text == '/cancel':
        temp_product_data.pop(user_id, None)
        return bot.reply_to(message, "❌ تم إلغاء إضافة المنتج")
    
    temp_product_data[user_id]['item_name'] = message.text.strip()
    bot.reply_to(message, f"✅ تم إضافة الاسم: {message.text.strip()}")
    
    msg = bot.send_message(message.chat.id, "💰 أرسل سعر المنتج (بالريال):")
    bot.register_next_step_handler(msg, process_product_price)

def process_product_price(message):
    user_id = message.from_user.id
    
    if message.text == '/cancel':
        temp_product_data.pop(user_id, None)
        return bot.reply_to(message, "❌ تم إلغاء إضافة المنتج")
    
    # التحقق من السعر
    try:
        price = float(message.text.strip())
        temp_product_data[user_id]['price'] = str(price)
        bot.reply_to(message, f"✅ تم إضافة السعر: {price} ريال")
        
        # إرسال أزرار الفئات
        markup = types.ReplyKeyboardMarkup(row_width=2, one_time_keyboard=True, resize_keyboard=True)
        markup.add(
            types.KeyboardButton("نتفلكس"),
            types.KeyboardButton("شاهد"),
            types.KeyboardButton("ديزني بلس"),
            types.KeyboardButton("اوسن بلس"),
            types.KeyboardButton("فديو بريميم"),
            types.KeyboardButton("اشتراكات أخرى")
        )
        
        msg = bot.send_message(message.chat.id, "🏷️ اختر فئة المنتج:", reply_markup=markup)
        bot.register_next_step_handler(msg, process_product_category)
        
    except ValueError:
        msg = bot.reply_to(message, "❌ السعر يجب أن يكون رقماً! أرسل السعر مرة أخرى:")
        bot.register_next_step_handler(msg, process_product_price)

def process_product_category(message):
    user_id = message.from_user.id
    
    if message.text == '/cancel':
        temp_product_data.pop(user_id, None)
        return bot.reply_to(message, "❌ تم إلغاء إضافة المنتج", reply_markup=types.ReplyKeyboardRemove())
    
    valid_categories = ["نتفلكس", "شاهد", "ديزني بلس", "اوسن بلس", "فديو بريميم", "اشتراكات أخرى"]
    
    if message.text.strip() not in valid_categories:
        markup = types.ReplyKeyboardMarkup(row_width=2, one_time_keyboard=True, resize_keyboard=True)
        markup.add(
            types.KeyboardButton("نتفلكس"),
            types.KeyboardButton("شاهد"),
            types.KeyboardButton("ديزني بلس"),
            types.KeyboardButton("اوسن بلس"),
            types.KeyboardButton("فديو بريميم"),
            types.KeyboardButton("اشتراكات أخرى")
        )
        msg = bot.reply_to(message, "❌ فئة غير صحيحة! اختر من الأزرار:", reply_markup=markup)
        return bot.register_next_step_handler(msg, process_product_category)
    
    temp_product_data[user_id]['category'] = message.text.strip()
    bot.reply_to(message, f"✅ تم اختيار الفئة: {message.text.strip()}", reply_markup=types.ReplyKeyboardRemove())
    
    msg = bot.send_message(message.chat.id, "📝 أرسل تفاصيل المنتج (مثل: مدة الاشتراك، المميزات، إلخ):")
    bot.register_next_step_handler(msg, process_product_details)

def process_product_details(message):
    user_id = message.from_user.id
    
    if message.text == '/cancel':
        temp_product_data.pop(user_id, None)
        return bot.reply_to(message, "❌ تم إلغاء إضافة المنتج")
    
    temp_product_data[user_id]['details'] = message.text.strip()
    bot.reply_to(message, "✅ تم إضافة التفاصيل")
    
    markup = types.ReplyKeyboardMarkup(row_width=1, one_time_keyboard=True, resize_keyboard=True)
    markup.add(types.KeyboardButton("تخطي"))
    
    msg = bot.send_message(message.chat.id, "🖼️ أرسل رابط صورة المنتج (أو اضغط تخطي):", reply_markup=markup)
    bot.register_next_step_handler(msg, process_product_image)

def process_product_image(message):
    user_id = message.from_user.id
    
    if message.text == '/cancel':
        temp_product_data.pop(user_id, None)
        return bot.reply_to(message, "❌ تم إلغاء إضافة المنتج", reply_markup=types.ReplyKeyboardRemove())
    
    if message.text.strip() == "تخطي":
        temp_product_data[user_id]['image_url'] = "https://via.placeholder.com/300x200?text=No+Image"
        bot.reply_to(message, "⏭️ تم تخطي الصورة", reply_markup=types.ReplyKeyboardRemove())
    else:
        temp_product_data[user_id]['image_url'] = message.text.strip()
        bot.reply_to(message, "✅ تم إضافة رابط الصورة", reply_markup=types.ReplyKeyboardRemove())
    
    msg = bot.send_message(message.chat.id, "🔐 أرسل البيانات المخفية (الايميل والباسورد مثلاً):")
    bot.register_next_step_handler(msg, process_product_hidden_data)

def process_product_hidden_data(message):
    user_id = message.from_user.id
    
    if message.text == '/cancel':
        temp_product_data.pop(user_id, None)
        return bot.reply_to(message, "❌ تم إلغاء إضافة المنتج")
    
    temp_product_data[user_id]['hidden_data'] = message.text.strip()
    bot.reply_to(message, "✅ تم إضافة البيانات المخفية")
    
    # سؤال عن نوع التسليم
    markup = types.ReplyKeyboardMarkup(row_width=2, one_time_keyboard=True, resize_keyboard=True)
    markup.add(
        types.KeyboardButton("⚡ تسليم فوري"),
        types.KeyboardButton("👨‍💼 تسليم يدوي")
    )
    
    msg = bot.send_message(
        message.chat.id, 
        "📦 اختر نوع التسليم:\n\n"
        "⚡ **تسليم فوري**: يتم إرسال البيانات تلقائياً للمشتري\n"
        "👨‍💼 **تسليم يدوي**: يتم إشعار الأدمن لتنفيذ الطلب",
        parse_mode="Markdown",
        reply_markup=markup
    )
    bot.register_next_step_handler(msg, process_product_delivery_type)

def process_product_delivery_type(message):
    user_id = message.from_user.id
    
    if message.text == '/cancel':
        temp_product_data.pop(user_id, None)
        return bot.reply_to(message, "❌ تم إلغاء إضافة المنتج", reply_markup=types.ReplyKeyboardRemove())
    
    if message.text == "⚡ تسليم فوري":
        temp_product_data[user_id]['delivery_type'] = 'instant'
        delivery_display = "⚡ تسليم فوري"
    elif message.text == "👨‍💼 تسليم يدوي":
        temp_product_data[user_id]['delivery_type'] = 'manual'
        delivery_display = "👨‍💼 تسليم يدوي"
    else:
        markup = types.ReplyKeyboardMarkup(row_width=2, one_time_keyboard=True, resize_keyboard=True)
        markup.add(
            types.KeyboardButton("⚡ تسليم فوري"),
            types.KeyboardButton("👨‍💼 تسليم يدوي")
        )
        msg = bot.reply_to(message, "❌ اختيار غير صحيح! اختر من الأزرار:", reply_markup=markup)
        return bot.register_next_step_handler(msg, process_product_delivery_type)
    
    bot.reply_to(message, f"✅ نوع التسليم: {delivery_display}", reply_markup=types.ReplyKeyboardRemove())
    
    # عرض ملخص المنتج
    product = temp_product_data[user_id]
    summary = (
        "📦 **ملخص المنتج:**\n\n"
        f"📝 الاسم: {product['item_name']}\n"
        f"💰 السعر: {product['price']} ريال\n"
        f"🏷️ الفئة: {product['category']}\n"
        f"📋 التفاصيل: {product['details']}\n"
        f"🖼️ الصورة: {product['image_url']}\n"
        f"🔐 البيانات: {product['hidden_data']}\n"
        f"📦 التسليم: {delivery_display}\n\n"
        "هل تريد إضافة هذا المنتج؟"
    )
    
    markup = types.ReplyKeyboardMarkup(row_width=2, one_time_keyboard=True, resize_keyboard=True)
    markup.add(
        types.KeyboardButton("✅ موافق"),
        types.KeyboardButton("❌ إلغاء")
    )
    
    msg = bot.send_message(message.chat.id, summary, parse_mode="Markdown", reply_markup=markup)
    bot.register_next_step_handler(msg, confirm_add_product)

def confirm_add_product(message):
    user_id = message.from_user.id
    
    if message.text == "✅ موافق":
        product = temp_product_data.get(user_id)
        
        if product:
            # إضافة المنتج
            product_id = str(uuid.uuid4())  # رقم فريد لا يتكرر
            delivery_type = product.get('delivery_type', 'instant')
            item = {
                'id': product_id,
                'item_name': product['item_name'],
                'price': str(product['price']),
                'seller_id': str(ADMIN_ID),
                'seller_name': 'المالك',
                'hidden_data': product['hidden_data'],
                'category': product['category'],
                'details': product['details'],
                'image_url': product['image_url'],
                'delivery_type': delivery_type,
                'sold': False
            }
            
            # حفظ في Firebase أولاً
            try:
                db.collection('products').document(product_id).set({
                    'item_name': item['item_name'],
                    'price': float(product['price']),
                    'seller_id': str(ADMIN_ID),
                    'seller_name': 'المالك',
                    'hidden_data': item['hidden_data'],
                    'category': item['category'],
                    'details': item['details'],
                    'image_url': item['image_url'],
                    'delivery_type': delivery_type,
                    'sold': False,
                    'created_at': firestore.SERVER_TIMESTAMP
                })
                print(f"✅ تم حفظ المنتج {product_id} في Firebase")
            except Exception as e:
                print(f"❌ خطأ في حفظ المنتج في Firebase: {e}")
            
            # حفظ في الذاكرة
            marketplace_items.append(item)
            
            delivery_display = "⚡ فوري" if delivery_type == 'instant' else "👨‍💼 يدوي"
            bot.reply_to(message,
                         f"✅ **تم إضافة المنتج بنجاح!**\n\n"
                         f"📦 المنتج: {product['item_name']}\n"
                         f"💰 السعر: {product['price']} ريال\n"
                         f"🏷️ الفئة: {product['category']}\n"
                         f"📦 التسليم: {delivery_display}\n"
                         f"📊 إجمالي المنتجات: {len(marketplace_items)}",
                         parse_mode="Markdown",
                         reply_markup=types.ReplyKeyboardRemove())
        
        # حذف البيانات المؤقتة
        temp_product_data.pop(user_id, None)
    else:
        bot.reply_to(message, "❌ تم إلغاء إضافة المنتج", reply_markup=types.ReplyKeyboardRemove())
        temp_product_data.pop(user_id, None)

@bot.message_handler(commands=['code'])
def get_verification_code(message):
    user_id = message.from_user.id
    user_name = message.from_user.first_name
    if message.from_user.last_name:
        user_name += ' ' + message.from_user.last_name
    
    # توليد كود تحقق
    code = generate_verification_code(user_id, user_name)
    
    bot.send_message(message.chat.id,
                     f"🔐 **كود التحقق الخاص بك:**\n\n"
                     f"`{code}`\n\n"
                     f"⏱️ **صالح لمدة 10 دقائق**\n\n"
                     f"💡 **خطوات الدخول:**\n"
                     f"1️⃣ افتح الموقع في المتصفح\n"
                     f"2️⃣ اضغط على زر 'حسابي'\n"
                     f"3️⃣ أدخل الآيدي الخاص بك: `{user_id}`\n"
                     f"4️⃣ أدخل الكود أعلاه\n\n"
                     f"⚠️ لا تشارك هذا الكود مع أحد!",
                     parse_mode="Markdown")

# أمر خاص بالآدمن لشحن رصيد المستخدمين
# طريقة الاستخدام: /add ID AMOUNT
# مثال: /add 123456789 50
@bot.message_handler(commands=['add'])
def add_funds(message):
    if message.from_user.id != ADMIN_ID:
        return bot.reply_to(message, "⛔ هذا الأمر للمشرف فقط.")
    
    try:
        parts = message.text.split()
        target_id = parts[1]
        amount = float(parts[2])
        add_balance(target_id, amount)
        bot.reply_to(message, f"✅ تم إضافة {amount} ريال للمستخدم {target_id}")
        bot.send_message(target_id, f"🎉 تم شحن رصيدك بمبلغ {amount} ريال!")
    except:
        bot.reply_to(message, "خطأ! الاستخدام: /add ID AMOUNT")

# أمر تسجيل/التحقق من Callback URL في EdfaPay
# الاستخدام: /edfapay (للتحقق) أو /edfapay register (للتسجيل)
@bot.message_handler(commands=['edfapay'])
def edfapay_settings(message):
    """إدارة إعدادات EdfaPay"""
    if message.from_user.id != ADMIN_ID:
        return bot.reply_to(message, "⛔ هذا الأمر للمالك فقط!")
    
    try:
        parts = message.text.split()
        action = parts[1] if len(parts) > 1 else "check"
        
        if action == "register":
            # تسجيل الـ callback URL
            bot.reply_to(message, "⏳ جاري تسجيل Callback URL في EdfaPay...")
            
            callback_url = f"{SITE_URL}/payment/edfapay_webhook"
            
            response = requests.post(
                "https://api.edfapay.com/payment/merchants/callback-url",
                json={
                    "action": "post",
                    "id": EDFAPAY_MERCHANT_ID,
                    "url": callback_url
                },
                timeout=30
            )
            
            if response.status_code == 200:
                bot.send_message(
                    message.chat.id,
                    f"✅ *تم تسجيل Callback URL بنجاح!*\n\n"
                    f"🔗 URL: `{callback_url}`\n\n"
                    f"📡 Response: `{response.text[:200]}`",
                    parse_mode="Markdown"
                )
            else:
                bot.send_message(
                    message.chat.id,
                    f"❌ *فشل تسجيل Callback URL*\n\n"
                    f"📡 Status: {response.status_code}\n"
                    f"📡 Response: `{response.text[:200]}`",
                    parse_mode="Markdown"
                )
        else:
            # التحقق من الـ callback URL المسجل
            bot.reply_to(message, "⏳ جاري التحقق من Callback URL...")
            
            response = requests.post(
                "https://api.edfapay.com/payment/merchants/callback-url",
                json={
                    "action": "get",
                    "id": EDFAPAY_MERCHANT_ID
                },
                timeout=30
            )
            
            # تنظيف النص من الرموز الخاصة
            response_text = response.text[:300].replace('`', "'").replace('_', '-').replace('*', '')
            
            bot.send_message(
                message.chat.id,
                f"📡 حالة EdfaPay Callback\n\n"
                f"🔑 Merchant ID: {EDFAPAY_MERCHANT_ID}\n"
                f"🌐 SITE_URL: {SITE_URL}\n\n"
                f"📡 Response ({response.status_code}):\n{response_text}\n\n"
                f"💡 للتسجيل أرسل: /edfapay register"
            )
            
    except Exception as e:
        bot.reply_to(message, f"❌ خطأ: {e}")

# أمر توليد مفاتيح الشحن
# الاستخدام: /توليد AMOUNT [COUNT]
# مثال: /توليد 50 10  (توليد 10 مفاتيح بقيمة 50 ريال لكل منها)
@bot.message_handler(commands=['توليد'])
def generate_keys(message):
    if message.from_user.id != ADMIN_ID:
        return bot.reply_to(message, "⛔ هذا الأمر للمالك فقط!")
    
    try:
        parts = message.text.split()
        amount = float(parts[1])
        count = int(parts[2]) if len(parts) > 2 else 1
        
        # التحقق من الحدود
        if count > 100:
            return bot.reply_to(message, "❌ الحد الأقصى 100 مفتاح في المرة الواحدة!")
        
        if amount <= 0:
            return bot.reply_to(message, "❌ المبلغ يجب أن يكون أكبر من صفر!")
        
        # توليد المفاتيح
        generated_keys = []
        for i in range(count):
            # توليد مفتاح عشوائي
            key_code = f"KEY-{random.randint(10000, 99999)}-{random.randint(1000, 9999)}"
            
            # حفظ المفتاح في الذاكرة
            charge_keys[key_code] = {
                'amount': amount,
                'used': False,
                'used_by': None,
                'created_at': time.time()
            }
            
            # حفظ في Firebase
            try:
                db.collection('charge_keys').document(key_code).set({
                    'amount': float(amount),
                    'used': False,
                    'used_by': '',
                    'created_at': time.time()
                })
            except Exception as e:
                print(f"⚠️ خطأ في حفظ المفتاح في Firebase: {e}")
            
            generated_keys.append(key_code)
        
        # إرسال المفاتيح
        if count == 1:
            response = (
                f"🎁 **تم توليد المفتاح بنجاح!**\n\n"
                f"💰 القيمة: {amount} ريال\n"
                f"🔑 المفتاح:\n"
                f"`{generated_keys[0]}`\n\n"
                f"📝 يمكن للمستخدم شحنه بإرسال: /شحن {generated_keys[0]}"
            )
        else:
            keys_text = "\n".join([f"`{key}`" for key in generated_keys])
            response = (
                f"🎁 **تم توليد {count} مفتاح بنجاح!**\n\n"
                f"💰 قيمة كل مفتاح: {amount} ريال\n"
                f"💵 المجموع الكلي: {amount * count} ريال\n\n"
                f"🔑 المفاتيح:\n{keys_text}\n\n"
                f"📝 الاستخدام: /شحن [المفتاح]"
            )
        
        bot.reply_to(message, response, parse_mode="Markdown")
        
    except IndexError:
        bot.reply_to(message, 
                     "❌ **خطأ في الاستخدام!**\n\n"
                     "📝 الصيغة الصحيحة:\n"
                     "`/توليد [المبلغ] [العدد]`\n\n"
                     "**أمثلة:**\n"
                     "• `/توليد 50` - مفتاح واحد بقيمة 50 ريال\n"
                     "• `/توليد 100 5` - 5 مفاتيح بقيمة 100 ريال لكل منها\n"
                     "• `/توليد 25 10` - 10 مفاتيح بقيمة 25 ريال لكل منها",
                     parse_mode="Markdown")
    except ValueError:
        bot.reply_to(message, "❌ الرجاء إدخال أرقام صحيحة!")

# أمر شحن الرصيد (يفتح خيارات الشحن)
@bot.message_handler(commands=['شحن'])
def recharge_balance(message):
    """أمر شحن الرصيد - يعرض خيارات الشحن"""
    try:
        user_id = str(message.from_user.id)
        
        # إنشاء أزرار خيارات الشحن
        markup = types.InlineKeyboardMarkup(row_width=2)
        btn_payment = types.InlineKeyboardButton("💳 شحن إلكتروني", callback_data="recharge_payment")
        btn_code = types.InlineKeyboardButton("🔑 شحن بكود", callback_data="recharge_code")
        markup.add(btn_payment)
        markup.add(btn_code)
        
        bot.send_message(
            message.chat.id,
            "💰 *شحن الرصيد*\n\n"
            "اختر طريقة الشحن:\n\n"
            "💳 *شحن إلكتروني* - الدفع عبر بوابة الدفع\n"
            "🔑 *شحن بكود* - إذا لديك كود شحن",
            reply_markup=markup,
            parse_mode="Markdown"
        )
    except Exception as e:
        bot.reply_to(message, f"❌ حدث خطأ: {str(e)}")

# معالج زر شحن إلكتروني
@bot.callback_query_handler(func=lambda call: call.data == "recharge_payment")
def handle_recharge_payment(call):
    """طلب إدخال مبلغ الشحن"""
    try:
        user_id = str(call.from_user.id)
        
        # التحقق من إعدادات بوابة الدفع EdfaPay
        if not EDFAPAY_MERCHANT_ID or not EDFAPAY_PASSWORD:
            bot.answer_callback_query(call.id, "❌ بوابة الدفع غير مفعلة حالياً")
            return bot.send_message(
                call.message.chat.id,
                "❌ *عذراً، بوابة الدفع غير مفعلة حالياً*\n\n"
                "يمكنك استخدام أكواد الشحن بدلاً من ذلك.",
                parse_mode="Markdown"
            )
        
        # تعيين حالة المستخدم لانتظار المبلغ
        user_states[user_id] = {
            'state': 'waiting_recharge_amount',
            'created_at': time.time()
        }
        
        bot.answer_callback_query(call.id)
        
        # إنشاء زر إلغاء
        markup = types.InlineKeyboardMarkup()
        btn_cancel = types.InlineKeyboardButton("❌ إلغاء", callback_data="cancel_recharge")
        markup.add(btn_cancel)
        
        bot.send_message(
            call.message.chat.id,
            "💳 *شحن رصيد إلكتروني*\n\n"
            "💵 أدخل المبلغ الذي تريد شحنه بالريال:\n\n"
            "📌 *مثال:* `50` أو `100`\n\n"
            "⚠️ الحد الأدنى: 10 ريال\n"
            "⚠️ الحد الأقصى: 1000 ريال",
            reply_markup=markup,
            parse_mode="Markdown"
        )
    except Exception as e:
        bot.answer_callback_query(call.id, "حدث خطأ!")
        print(f"❌ خطأ في handle_recharge_payment: {e}")

# معالج زر شحن بكود
@bot.callback_query_handler(func=lambda call: call.data == "recharge_code")
def handle_recharge_code(call):
    """طلب إدخال كود الشحن"""
    try:
        user_id = str(call.from_user.id)
        
        # تعيين حالة المستخدم لانتظار الكود
        user_states[user_id] = {
            'state': 'waiting_recharge_code',
            'created_at': time.time()
        }
        
        bot.answer_callback_query(call.id)
        
        # إنشاء زر إلغاء
        markup = types.InlineKeyboardMarkup()
        btn_cancel = types.InlineKeyboardButton("❌ إلغاء", callback_data="cancel_recharge")
        markup.add(btn_cancel)
        
        bot.send_message(
            call.message.chat.id,
            "🔑 *شحن بكود*\n\n"
            "📝 أرسل كود الشحن الخاص بك:\n\n"
            "📌 *مثال:* `KEY-XXXXX-XXXXX`",
            reply_markup=markup,
            parse_mode="Markdown"
        )
    except Exception as e:
        bot.answer_callback_query(call.id, "حدث خطأ!")
        print(f"❌ خطأ في handle_recharge_code: {e}")

# معالج زر إلغاء الشحن
@bot.callback_query_handler(func=lambda call: call.data == "cancel_recharge")
def handle_cancel_recharge(call):
    """إلغاء عملية الشحن"""
    try:
        user_id = str(call.from_user.id)
        
        # إزالة حالة المستخدم
        if user_id in user_states:
            del user_states[user_id]
        
        bot.answer_callback_query(call.id, "تم الإلغاء")
        bot.send_message(
            call.message.chat.id,
            "❌ تم إلغاء عملية الشحن.\n\n"
            "يمكنك البدء من جديد بإرسال /شحن",
            parse_mode="Markdown"
        )
    except Exception as e:
        bot.answer_callback_query(call.id, "حدث خطأ!")

# دالة إنشاء فاتورة دفع من EdfaPay
def create_edfapay_invoice(user_id, amount, user_name):
    """إنشاء فاتورة دفع في EdfaPay"""
    try:
        # توليد معرف فريد للطلب
        order_id = f"TR{user_id}{int(time.time())}"
        order_description = f"Recharge {amount} SAR"
        
        # إنشاء الـ Hash
        # Formula: hash = SHA1(MD5(UPPERCASE(order_id + order_amount + order_currency + order_description + merchant_password)))
        to_hash = f"{order_id}{amount}SAR{order_description}{EDFAPAY_PASSWORD}".upper()
        md5_hash = hashlib.md5(to_hash.encode()).hexdigest()
        final_hash = hashlib.sha1(md5_hash.encode()).hexdigest()
        
        # جلب IP العميل (نستخدم قيمة افتراضية)
        payer_ip = "176.44.76.222"
        
        # بيانات الطلب لـ EdfaPay API (multipart/form-data)
        payload = {
            'action': 'SALE',
            'edfa_merchant_id': EDFAPAY_MERCHANT_ID,
            'order_id': order_id,
            'order_amount': str(amount),
            'order_currency': 'SAR',
            'order_description': order_description,
            'req_token': 'N',
            'payer_first_name': user_name or 'Customer',
            'payer_last_name': 'User',
            'payer_address': 'Riyadh',
            'payer_country': 'SA',
            'payer_city': 'Riyadh',
            'payer_zip': '12221',
            'payer_email': f'user{user_id}@telegram.com',
            'payer_phone': '966500000000',
            'payer_ip': payer_ip,
            'term_url_3ds': f"{SITE_URL}/payment/success?order_id={order_id}",
            'auth': 'N',
            'recurring_init': 'N',
            'hash': final_hash
        }
        
        print(f"📤 EdfaPay Request: {payload}")
        
        # إرسال الطلب (multipart/form-data)
        # استخدام API الإنتاج
        api_url = "https://api.edfapay.com/payment/initiate"
        
        response = requests.post(api_url, data=payload, timeout=30)
        print(f"📤 EdfaPay Response Status: {response.status_code}")
        print(f"📤 EdfaPay Response: {response.text[:500]}")
        
        result = response.json()
        
        # التحقق من النجاح
        if response.status_code == 200 and result.get('redirect_url'):
            payment_url = result.get('redirect_url')
            
            # حفظ الطلب المعلق
            pending_payments[order_id] = {
                'user_id': user_id,
                'amount': amount,
                'order_id': order_id,
                'status': 'pending',
                'created_at': time.time()
            }
            
            # حفظ في Firebase
            try:
                db.collection('pending_payments').document(order_id).set({
                    'user_id': user_id,
                    'amount': amount,
                    'order_id': order_id,
                    'status': 'pending',
                    'created_at': firestore.SERVER_TIMESTAMP
                })
            except Exception as e:
                print(f"⚠️ خطأ في حفظ الطلب في Firebase: {e}")
            
            return {
                'success': True,
                'payment_url': payment_url,
                'invoice_id': order_id
            }
        else:
            error_msg = result.get('message') or result.get('error') or result.get('errors') or result
            print(f"❌ EdfaPay Error: {error_msg}")
            return {
                'success': False,
                'error': str(error_msg)
            }
            
    except requests.exceptions.Timeout:
        return {'success': False, 'error': 'انتهت مهلة الاتصال'}
    except requests.exceptions.RequestException as e:
        return {'success': False, 'error': f'خطأ في الاتصال: {str(e)}'}
    except Exception as e:
        print(f"❌ Exception in create_edfapay_invoice: {e}")
        import traceback
        traceback.print_exc()
        return {'success': False, 'error': str(e)}

# معالج الرسائل النصية (للمبالغ والأكواد)
@bot.message_handler(func=lambda message: str(message.from_user.id) in user_states)
def handle_user_state_message(message):
    """معالج رسائل المستخدمين حسب حالتهم"""
    try:
        user_id = str(message.from_user.id)
        state_data = user_states.get(user_id)
        
        if not state_data:
            return
        
        # التحقق من صلاحية الحالة (10 دقائق)
        if time.time() - state_data.get('created_at', 0) > 600:
            del user_states[user_id]
            return bot.reply_to(message, "⏱ انتهت صلاحية العملية. أرسل /شحن للبدء من جديد")
        
        state = state_data.get('state')
        
        # === حالة انتظار مبلغ الشحن ===
        if state == 'waiting_recharge_amount':
            text = message.text.strip()
            
            # التحقق من أن المدخل رقم
            try:
                amount = float(text)
            except ValueError:
                return bot.reply_to(message, "❌ الرجاء إدخال رقم صحيح فقط (مثال: 50)")
            
            # التحقق من الحدود
            if amount < 10:
                return bot.reply_to(message, "❌ الحد الأدنى للشحن هو 10 ريال")
            if amount > 1000:
                return bot.reply_to(message, "❌ الحد الأقصى للشحن هو 1000 ريال")
            
            # إزالة حالة المستخدم
            del user_states[user_id]
            
            # إرسال رسالة انتظار
            wait_msg = bot.reply_to(message, "⏳ جاري إنشاء رابط الدفع...")
            
            # إنشاء الفاتورة
            user_name = message.from_user.first_name
            result = create_edfapay_invoice(user_id, amount, user_name)
            
            if result['success']:
                # إنشاء زر للدفع
                markup = types.InlineKeyboardMarkup()
                btn_pay = types.InlineKeyboardButton("💳 ادفع الآن", url=result['payment_url'])
                markup.add(btn_pay)
                
                bot.edit_message_text(
                    f"✅ *تم إنشاء طلب الشحن!*\n\n"
                    f"💰 المبلغ: {amount} ريال\n"
                    f"📋 رقم الطلب: `{result['invoice_id']}`\n\n"
                    f"👇 اضغط الزر أدناه للدفع:\n\n"
                    f"⚠️ بعد الدفع سيتم إضافة الرصيد تلقائياً",
                    chat_id=wait_msg.chat.id,
                    message_id=wait_msg.message_id,
                    reply_markup=markup,
                    parse_mode="Markdown"
                )
                
                # إشعار المالك
                try:
                    bot.send_message(ADMIN_ID,
                        f"🔔 *طلب شحن جديد*\n\n"
                        f"👤 المستخدم: {user_name}\n"
                        f"🆔 الآيدي: {user_id}\n"
                        f"💰 المبلغ: {amount} ريال\n"
                        f"📋 رقم الطلب: `{result['invoice_id']}`",
                        parse_mode="Markdown"
                    )
                except:
                    pass
            else:
                bot.edit_message_text(
                    f"❌ *فشل إنشاء طلب الدفع*\n\n"
                    f"السبب: {result['error']}\n\n"
                    f"حاول مرة أخرى لاحقاً أو تواصل مع الدعم",
                    chat_id=wait_msg.chat.id,
                    message_id=wait_msg.message_id,
                    parse_mode="Markdown"
                )
        
        # === حالة انتظار كود الشحن ===
        elif state == 'waiting_recharge_code':
            key_code = message.text.strip()
            user_name = message.from_user.first_name
            
            # إزالة حالة المستخدم
            del user_states[user_id]
            
            # التحقق من وجود المفتاح
            if key_code not in charge_keys:
                return bot.reply_to(message, "❌ المفتاح غير صحيح أو منتهي الصلاحية!")
            
            key_data = charge_keys[key_code]
            
            # التحقق من استخدام المفتاح
            if key_data['used']:
                return bot.reply_to(message, 
                    f"❌ هذا المفتاح تم استخدامه بالفعل!\n\n"
                    f"👤 استخدمه: {key_data.get('used_by', 'مستخدم')}")
            
            # شحن الرصيد
            amount = key_data['amount']
            add_balance(user_id, amount)
            
            # تحديث حالة المفتاح
            charge_keys[key_code]['used'] = True
            charge_keys[key_code]['used_by'] = user_name
            charge_keys[key_code]['used_at'] = time.time()
            
            # تحديث في Firebase
            try:
                db.collection('charge_keys').document(key_code).update({
                    'used': True,
                    'used_by': user_name,
                    'used_at': time.time()
                })
            except Exception as e:
                print(f"⚠️ خطأ في تحديث المفتاح في Firebase: {e}")
            
            # إرسال رسالة نجاح
            bot.reply_to(message,
                f"✅ *تم شحن رصيدك بنجاح!*\n\n"
                f"💰 المبلغ المضاف: {amount} ريال\n"
                f"💵 رصيدك الحالي: {get_balance(user_id)} ريال\n\n"
                f"🎉 استمتع بالتسوق!",
                parse_mode="Markdown"
            )
            
            # إشعار المالك
            try:
                bot.send_message(ADMIN_ID,
                    f"🔔 *تم استخدام مفتاح شحن*\n\n"
                    f"👤 المستخدم: {user_name}\n"
                    f"🆔 الآيدي: {user_id}\n"
                    f"💰 المبلغ: {amount} ريال\n"
                    f"🔑 المفتاح: `{key_code}`",
                    parse_mode="Markdown"
                )
            except:
                pass
        
        # === حالة انتظار مبلغ الفاتورة ===
        elif state == 'waiting_invoice_amount':
            text = message.text.strip()
            merchant_name = state_data.get('merchant_name', message.from_user.first_name)
            
            # التحقق من أن المدخل رقم
            try:
                amount = float(text)
            except ValueError:
                return bot.reply_to(message, "❌ الرجاء إدخال رقم صحيح فقط (مثال: 100)")
            
            # التحقق من الحدود
            if amount < 1:
                return bot.reply_to(message, "❌ الحد الأدنى للفاتورة هو 1 ريال")
            if amount > 10000:
                return bot.reply_to(message, "❌ الحد الأقصى للفاتورة هو 10,000 ريال")
            
            # إزالة حالة المستخدم
            del user_states[user_id]
            
            # إنشاء معرف فريد للفاتورة
            invoice_id = generate_invoice_id()
            invoice_url = f"{SITE_URL}/invoice/{invoice_id}"
            
            # حفظ الفاتورة المعلقة (بدون رقم هاتف بعد)
            merchant_invoices[invoice_id] = {
                'invoice_id': invoice_id,
                'merchant_id': user_id,
                'merchant_name': merchant_name,
                'amount': amount,
                'customer_phone': None,
                'status': 'waiting_payment',
                'created_at': time.time()
            }
            
            # حفظ في Firebase
            try:
                db.collection('merchant_invoices').document(invoice_id).set({
                    'invoice_id': invoice_id,
                    'merchant_id': user_id,
                    'merchant_name': merchant_name,
                    'amount': amount,
                    'customer_phone': None,
                    'status': 'waiting_payment',
                    'created_at': firestore.SERVER_TIMESTAMP
                })
            except Exception as e:
                print(f"⚠️ خطأ في حفظ الفاتورة: {e}")
            
            # إرسال رابط الفاتورة للتاجر
            bot.send_message(
                message.chat.id,
                f"✅ *تم إنشاء الفاتورة بنجاح!*\n\n"
                f"💰 المبلغ: {amount} ريال\n"
                f"🆔 رقم الفاتورة: `{invoice_id}`\n\n"
                f"🔗 *رابط الفاتورة:*\n`{invoice_url}`\n\n"
                f"📤 أرسل هذا الرابط للعميل للدفع",
                parse_mode="Markdown"
            )
                
    except Exception as e:
        print(f"❌ خطأ في handle_user_state_message: {e}")

# أمر عرض المفاتيح النشطة (للمالك فقط)
@bot.message_handler(commands=['المفاتيح'])
def list_keys(message):
    if message.from_user.id != ADMIN_ID:
        return bot.reply_to(message, "⛔ هذا الأمر للمالك فقط!")
    
    active_keys = [k for k, v in charge_keys.items() if not v['used']]
    used_keys = [k for k, v in charge_keys.items() if v['used']]
    
    if not charge_keys:
        return bot.reply_to(message, "📭 لا توجد مفاتيح محفوظة!")
    
    response = f"📊 **إحصائيات المفاتيح**\n\n"
    response += f"✅ مفاتيح نشطة: {len(active_keys)}\n"
    response += f"🚫 مفاتيح مستخدمة: {len(used_keys)}\n"
    response += f"📈 الإجمالي: {len(charge_keys)}\n\n"
    
    if active_keys:
        total_value = sum([charge_keys[k]['amount'] for k in active_keys])
        response += f"💰 القيمة الإجمالية للمفاتيح النشطة: {total_value} ريال"
    
    bot.reply_to(message, response, parse_mode="Markdown")

@bot.message_handler(commands=['web'])
def open_web_app(message):
    bot.send_message(message.chat.id, 
                     f"🏪 **مرحباً بك في السوق!**\n\n"
                     f"افتح الرابط التالي في متصفحك لتصفح المنتجات:\n\n"
                     f"🔗 {SITE_URL}\n\n"
                     f"💡 **نصيحة:** انسخ الرابط وافتحه في متصفح خارجي (Chrome/Safari) "
                     f"للحصول على أفضل تجربة!",
                     parse_mode="Markdown")

# ============ نظام الفواتير للتجار ============

@bot.message_handler(commands=['فاتورة'])
def create_invoice_command(message):
    """أمر إنشاء فاتورة للعميل"""
    user_id = str(message.from_user.id)
    user_name = message.from_user.first_name
    
    # تعيين حالة انتظار إدخال مبلغ الفاتورة
    user_states[user_id] = {
        'state': 'waiting_invoice_amount',
        'created_at': time.time(),
        'merchant_name': user_name
    }
    
    # إنشاء زر إلغاء
    markup = types.InlineKeyboardMarkup()
    btn_cancel = types.InlineKeyboardButton("❌ إلغاء", callback_data="cancel_invoice")
    markup.add(btn_cancel)
    
    bot.send_message(
        message.chat.id,
        "🧾 *إنشاء فاتورة جديدة*\n\n"
        "💰 أدخل مبلغ الفاتورة بالريال:\n\n"
        "_مثال: 100_",
        parse_mode="Markdown",
        reply_markup=markup
    )

@bot.callback_query_handler(func=lambda call: call.data == "cancel_invoice")
def handle_cancel_invoice(call):
    """إلغاء إنشاء الفاتورة"""
    user_id = str(call.from_user.id)
    
    if user_id in user_states:
        del user_states[user_id]
    
    bot.answer_callback_query(call.id, "تم الإلغاء")
    bot.edit_message_text(
        "❌ تم إلغاء إنشاء الفاتورة.",
        chat_id=call.message.chat.id,
        message_id=call.message.message_id
    )

def generate_invoice_id():
    """توليد معرف قصير وفريد للفاتورة"""
    chars = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'
    return ''.join(random.choice(chars) for _ in range(6))

def create_customer_invoice(merchant_id, merchant_name, amount, customer_phone, original_invoice_id=None):
    """إنشاء فاتورة دفع للعميل وإرسالها لـ EdfaPay"""
    try:
        # استخدام معرف الفاتورة الأصلي أو توليد جديد
        invoice_id = original_invoice_id or f"INV{generate_invoice_id()}"
        order_id = f"{invoice_id}{int(time.time())}"
        order_description = f"Invoice {invoice_id} - {amount} SAR"
        
        # إنشاء الـ Hash
        to_hash = f"{order_id}{amount}SAR{order_description}{EDFAPAY_PASSWORD}".upper()
        md5_hash = hashlib.md5(to_hash.encode()).hexdigest()
        final_hash = hashlib.sha1(md5_hash.encode()).hexdigest()
        
        # تنظيف رقم الهاتف (الرقم يأتي كاملاً مع رمز الدولة من الصفحة)
        phone = customer_phone.strip()
        # إزالة + إن وجدت
        phone = phone.replace('+', '')
        # إزالة المسافات
        phone = phone.replace(' ', '')
        # إذا بدأ بصفر، أضف 966 (للتوافق مع الأرقام القديمة)
        if phone.startswith('0'):
            phone = '966' + phone[1:]
        
        # بيانات الطلب
        payload = {
            'action': 'SALE',
            'edfa_merchant_id': EDFAPAY_MERCHANT_ID,
            'order_id': order_id,
            'order_amount': str(amount),
            'order_currency': 'SAR',
            'order_description': order_description,
            'req_token': 'N',
            'payer_first_name': 'Customer',
            'payer_last_name': 'User',
            'payer_address': 'Saudi Arabia',
            'payer_country': 'SA',
            'payer_city': 'Riyadh',
            'payer_zip': '12221',
            'payer_email': f'customer{int(time.time())}@invoice.com',
            'payer_phone': phone,
            'payer_ip': '176.44.76.222',
            'term_url_3ds': f"{SITE_URL}/payment/success?order_id={order_id}&invoice={invoice_id}",
            'auth': 'N',
            'recurring_init': 'N',
            'hash': final_hash
        }
        
        print(f"📤 EdfaPay Invoice Request: {payload}")
        
        response = requests.post(EDFAPAY_API_URL, data=payload, timeout=30)
        print(f"📤 EdfaPay Response: {response.status_code} - {response.text[:500]}")
        
        result = response.json()
        
        if response.status_code == 200 and result.get('redirect_url'):
            payment_url = result.get('redirect_url')
            
            # حفظ الفاتورة في الذاكرة (صلاحية ساعة واحدة)
            expires_at = time.time() + 3600  # ساعة واحدة
            merchant_invoices[invoice_id] = {
                'invoice_id': invoice_id,
                'order_id': order_id,
                'merchant_id': merchant_id,
                'merchant_name': merchant_name,
                'amount': amount,
                'customer_phone': phone,
                'status': 'pending',
                'created_at': time.time(),
                'expires_at': expires_at
            }
            
            # حفظ الطلب المعلق (لربطه بالـ webhook)
            pending_payments[order_id] = {
                'user_id': merchant_id,  # سيتم إضافة الرصيد للتاجر
                'amount': amount,
                'order_id': order_id,
                'invoice_id': invoice_id,
                'is_merchant_invoice': True,  # علامة أنها فاتورة تاجر
                'status': 'pending',
                'created_at': time.time()
            }
            
            # حفظ في Firebase
            try:
                db.collection('merchant_invoices').document(invoice_id).set({
                    'invoice_id': invoice_id,
                    'order_id': order_id,
                    'merchant_id': merchant_id,
                    'merchant_name': merchant_name,
                    'amount': amount,
                    'customer_phone': phone,
                    'status': 'pending',
                    'created_at': firestore.SERVER_TIMESTAMP,
                    'expires_at': expires_at
                })
                
                db.collection('pending_payments').document(order_id).set({
                    'user_id': merchant_id,
                    'amount': amount,
                    'order_id': order_id,
                    'invoice_id': invoice_id,
                    'is_merchant_invoice': True,
                    'status': 'pending',
                    'created_at': firestore.SERVER_TIMESTAMP
                })
            except Exception as e:
                print(f"⚠️ خطأ في حفظ الفاتورة في Firebase: {e}")
            
            return {
                'success': True,
                'payment_url': payment_url,
                'invoice_id': invoice_id,
                'order_id': order_id
            }
        else:
            error_msg = result.get('message') or result.get('error') or str(result)
            return {'success': False, 'error': error_msg}
            
    except Exception as e:
        print(f"❌ Exception in create_customer_invoice: {e}")
        import traceback
        traceback.print_exc()
        return {'success': False, 'error': str(e)}

# زر استلام الطلب من قبل المشرف (النظام القديم - للطلبات في الذاكرة)
@bot.callback_query_handler(func=lambda call: call.data.startswith('claim_') and not call.data.startswith('claim_order_'))
def claim_order(call):
    order_id = call.data.replace('claim_', '')
    admin_id = call.from_user.id
    admin_name = call.from_user.first_name
    
    # التحقق من أن المستخدم هو المالك
    if admin_id != ADMIN_ID:
        return bot.answer_callback_query(call.id, "⛔ غير مصرح لك!", show_alert=True)
    
    # التحقق من وجود الطلب
    if order_id not in active_orders:
        return bot.answer_callback_query(call.id, "❌ الطلب غير موجود أو تم حذفه!", show_alert=True)
    
    order = active_orders[order_id]
    
    # التحقق من أن الطلب لم يتم استلامه مسبقاً
    if order['status'] == 'claimed':
        return bot.answer_callback_query(call.id, "⚠️ تم استلام هذا الطلب مسبقاً!", show_alert=True)
    
    # تحديث حالة الطلب في الذاكرة
    order['status'] = 'claimed'
    order['admin_id'] = admin_id
    
    # تحديث في Firebase
    try:
        db.collection('orders').document(order_id).update({
            'status': 'claimed',
            'admin_id': str(admin_id),
            'claimed_at': firestore.SERVER_TIMESTAMP
        })
    except Exception as e:
        print(f"⚠️ خطأ في تحديث الطلب في Firebase: {e}")
    
    # تحديث رسالة المشرف الذي استلم
    try:
        bot.edit_message_text(
            f"✅ تم استلام الطلب #{order_id}\n\n"
            f"📦 المنتج: {order['item_name']}\n"
            f"💰 السعر: {order['price']} ريال\n\n"
            f"👨‍💼 أنت المسؤول عن هذا الطلب\n"
            f"⏰ الحالة: قيد التنفيذ...\n\n"
            f"🔒 سيتم إرسال البيانات السرية لك الآن...",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id
        )
    except:
        pass
    
    # حذف الرسالة من المشرفين الآخرين
    if 'admin_messages' in order:
        for other_admin_id, msg_id in order['admin_messages'].items():
            if other_admin_id != admin_id:
                try:
                    bot.delete_message(other_admin_id, msg_id)
                except:
                    pass
    
    # إرسال البيانات المخفية للمشرف على الخاص
    hidden_info = order['hidden_data'] if order['hidden_data'] else "لا توجد بيانات مخفية لهذا المنتج."
    
    # إنشاء زر لتأكيد إتمام الطلب
    markup = types.InlineKeyboardMarkup()
    complete_btn = types.InlineKeyboardButton("✅ تم التسليم للعميل", callback_data=f"complete_{order_id}")
    markup.add(complete_btn)
    
    bot.send_message(
        admin_id,
        f"🔐 بيانات الطلب السرية #{order_id}\n\n"
        f"📦 المنتج: {order['item_name']}\n\n"
        f"👤 معلومات العميل:\n"
        f"• الاسم: {order['buyer_name']}\n"
        f"• آيدي تيليجرام: {order['buyer_id']}\n"
        f"• آيدي اللعبة: {order['game_id']}\n"
        f"• الاسم في اللعبة: {order['game_name']}\n\n"
        f"🔒 البيانات المحمية:\n"
        f"{hidden_info}\n\n"
        f"⚡ قم بتنفيذ الطلب ثم اضغط الزر أدناه!",
        reply_markup=markup
    )
    
    bot.answer_callback_query(call.id, "✅ تم استلام الطلب! تحقق من رسائلك الخاصة.")

# زر إتمام الطلب من قبل المشرف (النظام القديم - للطلبات في الذاكرة)
@bot.callback_query_handler(func=lambda call: call.data.startswith('complete_') and not call.data.startswith('complete_order_'))
def complete_order(call):
    order_id = call.data.replace('complete_', '')
    admin_id = call.from_user.id
    
    if order_id not in active_orders:
        return bot.answer_callback_query(call.id, "❌ الطلب غير موجود!", show_alert=True)
    
    order = active_orders[order_id]
    
    # التحقق من أن المشرف هو نفسه من استلم الطلب
    if order['admin_id'] != admin_id:
        return bot.answer_callback_query(call.id, "⛔ لم تستلم هذا الطلب!", show_alert=True)
    
    # تحويل المال للبائع
    add_balance(order['seller_id'], order['price'])
    
    # إشعار البائع
    bot.send_message(
        order['seller_id'],
        f"💰 تم بيع منتجك!\n\n"
        f"📦 المنتج: {order['item_name']}\n"
        f"💵 المبلغ: {order['price']} ريال\n\n"
        f"✅ تم إضافة المبلغ لرصيدك!"
    )
    
    # إشعار العميل
    markup = types.InlineKeyboardMarkup()
    confirm_btn = types.InlineKeyboardButton("✅ أكد الاستلام", callback_data=f"buyer_confirm_{order_id}")
    markup.add(confirm_btn)
    
    bot.send_message(
        order['buyer_id'],
        f"🎉 تم تنفيذ طلبك!\n\n"
        f"📦 المنتج: {order['item_name']}\n\n"
        f"✅ يرجى التحقق من حسابك والتأكد من استلام الخدمة\n\n"
        f"⚠️ إذا استلمت الخدمة بنجاح، اضغط الزر أدناه لتأكيد الاستلام.",
        reply_markup=markup
    )
    
    # تحديث حالة الطلب
    order['status'] = 'completed'
    
    # حذف رسالة البيانات السرية من خاص المشرف
    try:
        bot.edit_message_text(
            f"✅ تم إتمام الطلب #{order_id}\n\nتم حذف البيانات السرية للأمان.",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id
        )
    except:
        pass
    
    bot.answer_callback_query(call.id, "✅ تم إتمام الطلب بنجاح!")

# زر تأكيد الاستلام من العميل
@bot.callback_query_handler(func=lambda call: call.data.startswith('buyer_confirm_'))
def buyer_confirm(call):
    order_id = call.data.replace('buyer_confirm_', '')
    
    if order_id not in active_orders:
        return bot.answer_callback_query(call.id, "✅ تم تأكيد هذا الطلب مسبقاً!")
    
    order = active_orders[order_id]
    
    # التحقق من أن المستخدم هو المشتري
    if str(call.from_user.id) != order['buyer_id']:
        return bot.answer_callback_query(call.id, "⛔ هذا ليس طلبك!", show_alert=True)
    
    # حذف الطلب من القائمة النشطة
    del active_orders[order_id]
    
    # تحديث في Firebase
    try:
        db.collection('orders').document(order_id).update({
            'status': 'confirmed',
            'confirmed_at': firestore.SERVER_TIMESTAMP
        })
    except Exception as e:
        print(f"⚠️ خطأ في تحديث الطلب في Firebase: {e}")
    
    bot.edit_message_text(
        f"✅ شكراً لتأكيدك!\n\n"
        f"تم إتمام الطلب بنجاح ✨\n"
        f"نتمنى لك تجربة ممتعة! 🎮",
        chat_id=call.message.chat.id,
        message_id=call.message.message_id
    )
    
    bot.answer_callback_query(call.id, "✅ شكراً لك!")

# زر تأكيد الاستلام (يحرر المال للبائع) - الكود القديم للتوافق
@bot.callback_query_handler(func=lambda call: call.data.startswith('confirm_'))
def confirm_transaction(call):
    trans_id = call.data.split('_')[1]
    
    if trans_id not in transactions:
        return bot.answer_callback_query(call.id, "هذه العملية غير موجودة")
    
    trans = transactions[trans_id]
    
    # التأكد أن الذي يضغط هو المشتري فقط
    if str(call.from_user.id) != str(trans['buyer_id']):
        return bot.answer_callback_query(call.id, "فقط المشتري يمكنه تأكيد الاستلام!", show_alert=True)

    # تحرير المال للبائع
    seller_id = trans['seller_id']
    amount = trans['amount']
    
    # إضافة الرصيد للبائع
    add_balance(seller_id, amount)
    
    # حذف العملية من الانتظار
    del transactions[trans_id]
    
    bot.edit_message_text(f"✅ تم تأكيد استلام الخدمة: {trans['item_name']}\nتم تحويل {amount} ريال للبائع.", call.message.chat.id, call.message.message_id)
    bot.send_message(seller_id, f"🤑 مبروك! قام العميل بتأكيد الاستلام.\n💰 تم إضافة {amount} ريال لرصيدك.\n📦 الطلب: {trans['item_name']}\n🎮 آيدي: {trans.get('game_id', 'غير محدد')}")

# معالج تنفيذ الطلبات اليدوية
@bot.callback_query_handler(func=lambda call: call.data.startswith('claim_order_'))
def claim_manual_order(call):
    """معالج تنفيذ الطلب اليدوي من قبل الأدمن"""
    order_id = call.data.replace('claim_order_', '')
    admin_id = call.from_user.id
    admin_name = call.from_user.first_name
    
    print(f"📋 محاولة استلام الطلب: {order_id} بواسطة: {admin_name} ({admin_id})")
    
    # التحقق من أن المستخدم هو المالك
    if admin_id != ADMIN_ID:
        return bot.answer_callback_query(call.id, "⛔ غير مصرح لك!", show_alert=True)
    
    try:
        # جلب الطلب من Firebase
        order_ref = db.collection('orders').document(order_id)
        order_doc = order_ref.get()
        
        print(f"🔍 البحث عن الطلب: {order_id} - موجود: {order_doc.exists}")
        
        if not order_doc.exists:
            print(f"❌ الطلب غير موجود في Firebase: {order_id}")
            return bot.answer_callback_query(call.id, "❌ الطلب غير موجود!", show_alert=True)
        
        order = order_doc.to_dict()
        
        # التحقق من حالة الطلب
        if order.get('status') == 'completed':
            return bot.answer_callback_query(call.id, "✅ تم تنفيذ هذا الطلب مسبقاً!", show_alert=True)
        
        if order.get('status') == 'claimed':
            claimed_by = order.get('claimed_by_name', 'أدمن آخر')
            return bot.answer_callback_query(call.id, f"⚠️ هذا الطلب مستلم من قبل {claimed_by}!", show_alert=True)
        
        # تحديث حالة الطلب إلى مستلم
        order_ref.update({
            'status': 'claimed',
            'claimed_by': str(admin_id),
            'claimed_by_name': admin_name,
            'claimed_at': firestore.SERVER_TIMESTAMP
        })
        
        # تحديث رسالة الأدمن
        try:
            buyer_details = order.get('buyer_details', '')
            
            # 🔓 الآن نكشف بيانات المشتري للمشرف الذي استلم الطلب
            buyer_details_text = ""
            if buyer_details:
                buyer_details_text = f"\n\n📝 تفاصيل الطلب من المشتري:\n━━━━━━━━━━━━━━━━━━━━━━━━\n{buyer_details}\n━━━━━━━━━━━━━━━━━━━━━━━━"
            
            # إنشاء زر إكمال الطلب
            complete_markup = telebot.types.InlineKeyboardMarkup()
            complete_markup.add(telebot.types.InlineKeyboardButton(
                "✅ تم التسليم", 
                callback_data=f"complete_order_{order_id}"
            ))
            
            bot.edit_message_text(
                f"✅ تم استلام الطلب بواسطتك!\n\n"
                f"🆔 رقم الطلب: #{order_id}\n"
                f"📦 المنتج: {order.get('item_name')}\n"
                f"👤 المشتري: {order.get('buyer_name')}\n"
                f"🔢 معرف المشتري: {order.get('buyer_id')}\n"
                f"💰 السعر: {order.get('price')} ريال"
                f"{buyer_details_text}\n\n"
                f"👇 بعد تنفيذ الطلب اضغط الزر أدناه",
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=complete_markup
            )
        except Exception as e:
            print(f"⚠️ خطأ في تحديث رسالة الأدمن: {e}")
        
        # 📌 إشعار المالك بأن مشرف استلم الطلب
        if admin_id != ADMIN_ID:
            try:
                bot.send_message(
                    ADMIN_ID,
                    f"📌 تم استلام طلب\n\n"
                    f"🆔 رقم الطلب: #{order_id}\n"
                    f"📦 المنتج: {order.get('item_name')}\n"
                    f"👤 المشتري: {order.get('buyer_name')}\n"
                    f"👨‍💼 المشرف المنفذ: {admin_name}\n"
                    f"💰 السعر: {order.get('price')} ريال"
                )
            except:
                pass
        
        # إشعار المشتري
        try:
            bot.send_message(
                int(order.get('buyer_id')),
                f"👨‍💼 تم استلام طلبك!\n\n"
                f"🆔 رقم الطلب: #{order_id}\n"
                f"📦 المنتج: {order.get('item_name')}\n"
                f"✅ المسؤول: {admin_name}\n\n"
                f"⏳ جاري تنفيذ طلبك..."
            )
        except:
            pass
        
        bot.answer_callback_query(call.id, "✅ تم استلام الطلب بنجاح!")
        
    except Exception as e:
        print(f"❌ خطأ في استلام الطلب: {e}")
        bot.answer_callback_query(call.id, f"❌ حدث خطأ: {str(e)}", show_alert=True)

# معالج إكمال الطلب اليدوي
@bot.callback_query_handler(func=lambda call: call.data.startswith('complete_order_'))
def complete_manual_order(call):
    """معالج إكمال الطلب اليدوي بعد التنفيذ"""
    from datetime import datetime
    order_id = call.data.replace('complete_order_', '')
    admin_id = call.from_user.id
    admin_name = call.from_user.first_name
    
    try:
        # جلب الطلب من Firebase
        order_ref = db.collection('orders').document(order_id)
        order_doc = order_ref.get()
        
        if not order_doc.exists:
            return bot.answer_callback_query(call.id, "❌ الطلب غير موجود!", show_alert=True)
        
        order = order_doc.to_dict()
        
        # التحقق من أن الأدمن هو من استلم الطلب
        if order.get('claimed_by') != str(admin_id) and admin_id != ADMIN_ID:
            return bot.answer_callback_query(call.id, "⛔ هذا الطلب ليس مستلماً بواسطتك!", show_alert=True)
        
        if order.get('status') == 'completed':
            return bot.answer_callback_query(call.id, "✅ تم تنفيذ هذا الطلب مسبقاً!", show_alert=True)
        
        # تحديث حالة الطلب إلى مكتمل
        order_ref.update({
            'status': 'completed',
            'completed_by': str(admin_id),
            'completed_by_name': admin_name,
            'completed_at': firestore.SERVER_TIMESTAMP
        })
        
        # تحديث رسالة الأدمن
        try:
            bot.edit_message_text(
                f"✅ تم إكمال الطلب بنجاح!\n\n"
                f"🆔 رقم الطلب: #{order_id}\n"
                f"📦 المنتج: {order.get('item_name')}\n"
                f"👤 المشتري: {order.get('buyer_name')}\n"
                f"💰 السعر: {order.get('price')} ريال\n\n"
                f"👨‍💼 تم التنفيذ بواسطة: {admin_name}\n"
                f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M')}",
                chat_id=call.message.chat.id,
                message_id=call.message.message_id
            )
        except:
            pass
        
        # إشعار المشتري بإكمال الطلب
        try:
            hidden_data = order.get('hidden_data', '')
            if hidden_data:
                bot.send_message(
                    int(order.get('buyer_id')),
                    f"🎉 تم تنفيذ طلبك بنجاح!\n\n"
                    f"🆔 رقم الطلب: #{order_id}\n"
                    f"📦 المنتج: {order.get('item_name')}\n"
                    f"👨‍💼 تم التنفيذ بواسطة: {admin_name}\n\n"
                    f"🔐 بيانات الاشتراك:\n{hidden_data}\n\n"
                    f"⚠️ احفظ هذه البيانات في مكان آمن!\n"
                    f"شكراً لتسوقك معنا! 💙"
                )
            else:
                bot.send_message(
                    int(order.get('buyer_id')),
                    f"🎉 تم تنفيذ طلبك بنجاح!\n\n"
                    f"🆔 رقم الطلب: #{order_id}\n"
                    f"📦 المنتج: {order.get('item_name')}\n"
                    f"👨‍💼 تم التنفيذ بواسطة: {admin_name}\n\n"
                    f"شكراً لتسوقك معنا! 💙"
                )
        except Exception as e:
            print(f"⚠️ فشل إشعار المشتري: {e}")
        
        # إشعار المالك الرئيسي
        try:
            if admin_id != ADMIN_ID:
                bot.send_message(
                    ADMIN_ID,
                    f"✅ تم تنفيذ طلب يدوي\n\n"
                    f"🆔 الطلب: #{order_id}\n"
                    f"📦 المنتج: {order.get('item_name')}\n"
                    f"👨‍💼 المنفذ: {admin_name}\n"
                    f"👤 المشتري: {order.get('buyer_name')}"
                )
        except:
            pass
        
        bot.answer_callback_query(call.id, "✅ تم إكمال الطلب وإشعار المشتري!")
        
    except Exception as e:
        print(f"❌ خطأ في إكمال الطلب: {e}")
        bot.answer_callback_query(call.id, f"❌ حدث خطأ: {str(e)}", show_alert=True)

# --- مسارات الموقع (Flask) ---

# مسار تسجيل الخروج
@app.route('/logout', methods=['POST'])
def logout():
    session.clear()
    return {'success': True}

# مسار جلب طلبات المستخدم
@app.route('/get_orders')
def get_user_orders():
    # استخدام الجلسة فقط للأمان - لا نقبل user_id من الرابط
    user_id = session.get('user_id')
    
    if not user_id:
        return {'orders': []}
    
    user_id = str(user_id)
    
    # جلب جميع الطلبات الخاصة بالمستخدم من Firebase
    user_orders = []
    
    try:
        orders_ref = query_where(db.collection('orders'), 'buyer_id', '==', user_id)
        for doc in orders_ref.stream():
            order = doc.to_dict()
            order_id = doc.id
            
            # إضافة اسم المشرف إذا تم استلام الطلب
            admin_name = None
            if order.get('admin_id'):
                try:
                    admin_info = bot.get_chat(order['admin_id'])
                    admin_name = admin_info.first_name
                except:
                    admin_name = "مشرف"
            
            user_orders.append({
                'order_id': order_id,
                'item_name': order.get('item_name', 'منتج'),
                'price': order.get('price', 0),
                'game_id': order.get('buyer_details', ''),  # تفاصيل المشتري
                'game_name': '',
                'status': order.get('status', 'completed'),
                'delivery_type': order.get('delivery_type', 'instant'),
                'admin_name': admin_name
            })
    except Exception as e:
        print(f"❌ خطأ في جلب الطلبات: {e}")
        # fallback للذاكرة
        for order_id, order in active_orders.items():
            if str(order.get('buyer_id')) == user_id:
                admin_name = None
                if order.get('admin_id'):
                    try:
                        admin_info = bot.get_chat(order['admin_id'])
                        admin_name = admin_info.first_name
                    except:
                        admin_name = "مشرف"
                
                user_orders.append({
                    'order_id': order_id,
                    'item_name': order.get('item_name', 'منتج'),
                    'price': order.get('price', 0),
                    'game_id': order.get('game_id', ''),
                    'game_name': order.get('game_name', ''),
                    'status': order.get('status', 'completed'),
                    'delivery_type': order.get('delivery_type', 'instant'),
                    'admin_name': admin_name
                })
    
    # ترتيب الطلبات من الأحدث للأقدم
    user_orders.reverse()
    
    return {'orders': user_orders}

# مسار التحقق من الكود وتسجيل الدخول
@app.route('/verify', methods=['POST'])
@limiter.limit("5 per minute")  # 🔒 Rate Limiting: 5 محاولات/دقيقة
def verify_login():
    data = request.get_json()
    user_id = data.get('user_id')
    code = data.get('code')
    
    if not user_id or not code:
        return {'success': False, 'message': 'الرجاء إدخال الآيدي والكود'}
    
    # التحقق من صحة الكود
    code_data = verify_code(user_id, code)
    
    if not code_data:
        return {'success': False, 'message': 'الكود غير صحيح أو منتهي الصلاحية'}
    
    # تجديد الجلسة لمنع Session Fixation
    regenerate_session()
    
    # تسجيل دخول المستخدم
    session.permanent = True  # تفعيل انتهاء الصلاحية التلقائي
    session['user_id'] = user_id
    session['user_name'] = code_data['name']
    session['login_time'] = time.time()  # وقت تسجيل الدخول

    # حذف الكود بعد الاستخدام
    del verification_codes[str(user_id)]

    # جلب الرصيد
    balance = get_balance(user_id)

    # جلب صورة الحساب من تيليجرام أو Firebase
    profile_photo_url = None
    try:
        # أولاً: محاولة جلب من Firebase
        user_doc = db.collection('users').document(str(user_id)).get()
        if user_doc.exists:
            profile_photo_url = user_doc.to_dict().get('profile_photo')
        
        # ثانياً: إذا لم توجد، جلب من تيليجرام مباشرة
        if not profile_photo_url:
            photos = bot.get_user_profile_photos(int(user_id), limit=1)
            if photos.total_count > 0:
                file_id = photos.photos[0][0].file_id
                file_info = bot.get_file(file_id)
                token = bot.token
                profile_photo_url = f"https://api.telegram.org/file/bot{token}/{file_info.file_path}"
                # حفظ في Firebase للاستخدام لاحقاً
                db.collection('users').document(str(user_id)).update({'profile_photo': profile_photo_url})
    except Exception as e:
        print(f"⚠️ خطأ في جلب صورة الحساب: {e}")
    
    # حفظ في الجلسة
    if profile_photo_url:
        session['profile_photo'] = profile_photo_url

    return {
        'success': True,
        'message': 'تم تسجيل الدخول بنجاح',
        'user_name': code_data['name'],
        'balance': balance,
        'profile_photo_url': profile_photo_url
    }

# --- حماية إضافية: رؤوس أمنية ---
@app.after_request
def add_security_headers(response):
    """إضافة رؤوس أمنية لكل استجابة"""
    # منع تضمين الموقع في iframe
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    # حماية من XSS
    response.headers['X-XSS-Protection'] = '1; mode=block'
    # منع تخمين نوع المحتوى
    response.headers['X-Content-Type-Options'] = 'nosniff'
    # سياسة الإحالة
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    # منع الكشف عن معلومات السيرفر
    response.headers['Server'] = 'Protected'
    return response

# --- حماية من محاولات الاختراق ---
BLOCKED_PATHS = [
    '/wp-admin', '/wp-login', '/wp-content', '/wp-includes',
    '/wordpress', '/.env', '/.git', '/phpmyadmin', '/pma',
    '/admin.php', '/xmlrpc.php', '/wp-config', '/config.php',
    '/shell', '/c99', '/r57', '/webshell', '/backdoor',
    '/.htaccess', '/.htpasswd', '/cgi-bin', '/admin/config',
    '/phpinfo', '/info.php', '/test.php', '/debug',
    '/backup', '/.bak', '/.sql', '/.zip', '/.tar',
    '/vendor/', '/node_modules/', '/.DS_Store'
]

@app.before_request
def block_suspicious_requests():
    """حظر الطلبات المشبوهة"""
    path = request.path.lower()
    
    # التحقق من الروابط المحظورة
    for blocked in BLOCKED_PATHS:
        if blocked in path:
            # سجل المحاولة
            print(f"🚫 محاولة اختراق محظورة: {request.path} من {request.remote_addr}")
            return "Forbidden", 403
    
    return None

# --- التحقق من صلاحية الجلسة ---
@app.before_request
def check_session_validity():
    """التحقق من صلاحية الجلسة قبل كل طلب"""
    if 'user_id' in session:
        login_time = session.get('login_time', 0)
        # التحقق من انتهاء الصلاحية (30 دقيقة)
        if time.time() - login_time > 1800:  # 30 * 60 = 1800 ثانية
            session.clear()
            print("⏰ انتهت صلاحية الجلسة")

@app.route('/robots.txt')
def robots_txt():
    """ملف robots.txt للمحركات البحث"""
    return """User-agent: *
Allow: /
Disallow: /admin
Disallow: /webhook
Disallow: /payment/
Disallow: /api/
""", 200, {'Content-Type': 'text/plain'}

@app.route('/favicon.ico')
def favicon():
    """أيقونة الموقع"""
    return '', 204

@app.route('/')
def index():
    # التحقق من جلسة المستخدم - استخدام الجلسة فقط للأمان
    user_id = session.get('user_id')
    user_name = session.get('user_name', 'ضيف')
    profile_photo = session.get('profile_photo', '')
    
    # 1. جلب الرصيد وصورة البروفايل (محدث من Firebase)
    balance = 0.0
    if user_id:
        try:
            user_doc = db.collection('users').document(str(user_id)).get()
            if user_doc.exists:
                user_data = user_doc.to_dict()
                balance = user_data.get('balance', 0.0)
                if not profile_photo:
                    profile_photo = user_data.get('profile_photo', '')
        except:
            balance = get_balance(user_id)
    
    # 2. جلب المنتجات (مباشرة من Firebase لضمان ظهورها)
    items = []
    try:
        # جلب المنتجات التي لم تُبع (sold == False)
        docs = query_where(db.collection('products'), 'sold', '==', False).stream()
        
        for doc in docs:
            p = doc.to_dict()
            p['id'] = doc.id  # مهم جداً لعملية الشراء
            items.append(p)
        
        print(f"✅ تم جلب {len(items)} منتج من Firebase للمتجر")
            
    except Exception as e:
        print(f"❌ خطأ في جلب المنتجات للمتجر: {e}")
        # في حال الفشل، نعود لاستخدام الذاكرة كاحتياط
        items = [i for i in marketplace_items if not i.get('sold')]

    # 3. جلب المنتجات المباعة (لعرضها في قسم منفصل)
    sold_items = []
    try:
        sold_docs = query_where(db.collection('products'), 'sold', '==', True).stream()
        for doc in sold_docs:
            p = doc.to_dict()
            p['id'] = doc.id
            sold_items.append(p)
        print(f"✅ تم جلب {len(sold_items)} منتج مباع من Firebase")
    except Exception as e:
        print(f"❌ خطأ في جلب المنتجات المباعة: {e}")
        sold_items = [i for i in marketplace_items if i.get('sold')]

    # 4. جلب مشتريات المستخدم الحالي
    my_purchases = []
    if user_id:
        try:
            purchases_docs = query_where(db.collection('orders'), 'buyer_id', '==', str(user_id)).stream()
            for doc in purchases_docs:
                p = doc.to_dict()
                p['order_id'] = doc.id
                my_purchases.append(p)
            print(f"✅ تم جلب {len(my_purchases)} مشتريات للمستخدم {user_id}")
        except Exception as e:
            print(f"❌ خطأ في جلب مشتريات المستخدم: {e}")

    # جلب عدد منتجات السلة
    cart_count = 0
    if user_id:
        cart = user_carts.get(str(user_id), {})
        cart_count = len(cart.get('items', []))

    # عرض الصفحة
    return render_template('index.html', 
                                  items=items,
                                  sold_items=sold_items,
                                  my_purchases=my_purchases,
                                  balance=balance, 
                                  current_user_id=user_id or 0, 
                                  current_user=user_id,
                                  user_name=user_name,
                                  profile_photo=profile_photo,
                                  cart_count=cart_count)

# ============================================
# 🛒 نظام سلة التسوق
# ============================================


@app.route('/cart')
def cart_page():
    """صفحة سلة التسوق"""
    user_id = session.get('user_id')
    if not user_id:
        return redirect('/')
    
    balance = get_balance(user_id)
    return render_template('cart.html', user_id=user_id, balance=balance)

# --- API سلة التسوق ---

@app.route('/api/cart/add', methods=['POST'])
@limiter.limit("30 per minute")
def api_cart_add():
    """إضافة منتج للسلة"""
    try:
        data = request.json
        user_id = str(data.get('user_id'))
        product_id = data.get('product_id')
        buyer_details = data.get('buyer_details', '')  # معلومات المشتري للتسليم اليدوي
        
        if not user_id or not product_id:
            return jsonify({'status': 'error', 'message': 'بيانات ناقصة'})
        
        # التحقق من المنتج
        product_doc = db.collection('products').document(product_id).get()
        if not product_doc.exists:
            return jsonify({'status': 'error', 'message': 'المنتج غير موجود'})
        
        product = product_doc.to_dict()
        
        # منع إضافة منتج مباع
        if product.get('sold', False):
            return jsonify({'status': 'error', 'message': 'عذراً، هذا المنتج تم بيعه! 🚫'})
        
        # جلب أو إنشاء السلة
        from datetime import datetime, timedelta
        
        cart = user_carts.get(user_id, {})
        now = datetime.utcnow()
        
        # التحقق من انتهاء السلة
        if cart.get('expires_at'):
            expires = cart['expires_at']
            if isinstance(expires, str):
                expires = datetime.fromisoformat(expires.replace('Z', ''))
            if expires < now:
                cart = {}  # السلة انتهت
        
        # إنشاء سلة جديدة أو تحديث
        if not cart.get('items'):
            cart = {
                'items': [],
                'created_at': now.isoformat(),
                'expires_at': (now + timedelta(hours=3)).isoformat(),
                'status': 'active'
            }
        
        # التحقق من عدم وجود المنتج في السلة
        existing_ids = [item['product_id'] for item in cart.get('items', [])]
        if product_id in existing_ids:
            return jsonify({'status': 'error', 'message': 'المنتج موجود في السلة بالفعل!'})
        
        # إضافة المنتج
        cart_item = {
            'product_id': product_id,
            'name': product.get('item_name', 'منتج'),
            'price': float(product.get('price', 0)),
            'category': product.get('category', ''),
            'image_url': product.get('image_url', ''),
            'delivery_type': product.get('delivery_type', 'instant'),
            'buyer_instructions': product.get('buyer_instructions', ''),
            'buyer_details': buyer_details,  # معلومات المشتري المدخلة
            'added_at': now.isoformat()
        }
        cart['items'].append(cart_item)
        cart['updated_at'] = now.isoformat()
        
        # حفظ في الذاكرة و Firebase
        user_carts[user_id] = cart
        
        if db:
            db.collection('carts').document(user_id).set(cart)
        
        # تحديث إحصائيات المنتج
        try:
            stats_ref = db.collection('cart_stats').document(product_id)
            stats_doc = stats_ref.get()
            if stats_doc.exists:
                stats_ref.update({'add_to_cart_count': firestore.Increment(1)})
            else:
                stats_ref.set({'product_id': product_id, 'add_to_cart_count': 1, 'purchase_count': 0})
        except:
            pass
        
        return jsonify({
            'status': 'success',
            'message': 'تمت الإضافة للسلة! 🛒',
            'cart_count': len(cart['items'])
        })
        
    except Exception as e:
        print(f"❌ خطأ في إضافة للسلة: {e}")
        return jsonify({'status': 'error', 'message': 'حدث خطأ'})

@app.route('/api/cart/get')
def api_cart_get():
    """جلب محتويات السلة"""
    try:
        user_id = request.args.get('user_id')
        if not user_id:
            return jsonify({'status': 'error', 'message': 'معرف المستخدم مطلوب'})
        
        from datetime import datetime
        
        cart = user_carts.get(str(user_id), {})
        
        if not cart or not cart.get('items'):
            return jsonify({'status': 'empty', 'message': 'السلة فارغة'})
        
        # التحقق من انتهاء الصلاحية
        now = datetime.utcnow()
        expires_at = cart.get('expires_at')
        if expires_at:
            if isinstance(expires_at, str):
                expires = datetime.fromisoformat(expires_at.replace('Z', ''))
            else:
                expires = expires_at
            if expires < now:
                # حذف السلة المنتهية
                user_carts.pop(str(user_id), None)
                if db:
                    db.collection('carts').document(str(user_id)).delete()
                return jsonify({'status': 'expired', 'message': 'انتهت صلاحية السلة'})
        
        # تحديث حالة المنتجات
        updated_items = []
        for item in cart['items']:
            product_doc = db.collection('products').document(item['product_id']).get()
            if product_doc.exists:
                product = product_doc.to_dict()
                item['sold'] = product.get('sold', False)
                item['current_price'] = float(product.get('price', item['price']))
                item['price_changed'] = item['current_price'] != item['price']
                updated_items.append(item)
            else:
                item['sold'] = True  # المنتج محذوف
                updated_items.append(item)
        
        cart['items'] = updated_items
        
        return jsonify({
            'status': 'success',
            'cart': cart
        })
        
    except Exception as e:
        print(f"❌ خطأ في جلب السلة: {e}")
        return jsonify({'status': 'error', 'message': 'حدث خطأ'})

@app.route('/api/cart/remove', methods=['POST'])
def api_cart_remove():
    """حذف منتج من السلة"""
    try:
        data = request.json
        user_id = str(data.get('user_id'))
        product_id = data.get('product_id')
        
        if not user_id or not product_id:
            return jsonify({'status': 'error', 'message': 'بيانات ناقصة'})
        
        cart = user_carts.get(user_id, {})
        if not cart or not cart.get('items'):
            return jsonify({'status': 'error', 'message': 'السلة فارغة'})
        
        # حذف المنتج
        cart['items'] = [i for i in cart['items'] if i['product_id'] != product_id]
        
        from datetime import datetime
        cart['updated_at'] = datetime.utcnow().isoformat()
        
        # حفظ
        user_carts[user_id] = cart
        if db:
            db.collection('carts').document(user_id).set(cart)
        
        return jsonify({
            'status': 'success',
            'message': 'تم حذف المنتج',
            'cart_count': len(cart['items'])
        })
        
    except Exception as e:
        print(f"❌ خطأ في حذف من السلة: {e}")
        return jsonify({'status': 'error', 'message': 'حدث خطأ'})

@app.route('/api/cart/checkout', methods=['POST'])
@limiter.limit("5 per minute")
def api_cart_checkout():
    """إتمام شراء السلة"""
    try:
        data = request.json
        user_id = str(data.get('user_id'))
        
        if not user_id:
            return jsonify({'status': 'error', 'message': 'معرف المستخدم مطلوب'})
        
        # جلب السلة
        cart = user_carts.get(user_id, {})
        if not cart or not cart.get('items'):
            return jsonify({'status': 'error', 'message': 'السلة فارغة'})
        
        # تصفية المنتجات المتاحة
        available_items = []
        total = 0
        
        for item in cart['items']:
            product_doc = db.collection('products').document(item['product_id']).get()
            if product_doc.exists:
                product = product_doc.to_dict()
                if not product.get('sold', False):
                    item['product_data'] = product
                    item['current_price'] = float(product.get('price', item['price']))
                    total += item['current_price']
                    available_items.append(item)
        
        if not available_items:
            return jsonify({'status': 'error', 'message': 'لا توجد منتجات متاحة في السلة'})
        
        # التحقق من الرصيد
        user_doc = db.collection('users').document(user_id).get()
        if not user_doc.exists:
            return jsonify({'status': 'error', 'message': 'حدث خطأ في المستخدم'})
        
        user_data = user_doc.to_dict()
        balance = float(user_data.get('balance', 0))
        
        if balance < total:
            return jsonify({'status': 'error', 'message': f'رصيدك غير كافي! تحتاج {total - balance:.2f} ر.س إضافية'})
        
        # تنفيذ الشراء باستخدام batch
        batch = db.batch()
        new_balance = balance - total
        purchased_items = []
        order_ids = []
        
        # جلب اسم المشتري
        buyer_name = user_data.get('first_name', 'مستخدم')
        
        for item in available_items:
            product = item['product_data']
            product_id = item['product_id']
            delivery_type = item.get('delivery_type', product.get('delivery_type', 'instant'))
            order_status = 'completed' if delivery_type == 'instant' else 'pending'
            
            # تحديث المنتج كمباع
            product_ref = db.collection('products').document(product_id)
            batch.update(product_ref, {
                'sold': True,
                'buyer_id': user_id,
                'buyer_name': buyer_name,
                'sold_at': firestore.SERVER_TIMESTAMP
            })
            
            # إنشاء الطلب
            import random
            order_id = f"ORD_{random.randint(100000, 999999)}"
            order_ref = db.collection('orders').document(order_id)
            batch.set(order_ref, {
                'buyer_id': user_id,
                'buyer_name': buyer_name,
                'item_name': product.get('item_name'),
                'price': item['current_price'],
                'hidden_data': product.get('hidden_data'),
                'details': product.get('details', ''),
                'category': product.get('category', ''),
                'delivery_type': delivery_type,
                'buyer_details': item.get('buyer_details', ''),  # معلومات المشتري للتسليم اليدوي
                'buyer_instructions': item.get('buyer_instructions', ''),
                'status': order_status,
                'from_cart': True,
                'created_at': firestore.SERVER_TIMESTAMP
            })
            
            order_ids.append(order_id)
            purchased_items.append({
                'name': product.get('item_name'),
                'price': item['current_price'],
                'hidden_data': product.get('hidden_data'),
                'order_id': order_id,
                'delivery_type': delivery_type,
                'buyer_details': item.get('buyer_details', '')
            })
            
            # تحديث إحصائيات
            try:
                stats_ref = db.collection('cart_stats').document(product_id)
                batch.update(stats_ref, {'purchase_count': firestore.Increment(1)})
            except:
                pass
        
        # تحديث رصيد المستخدم
        user_ref = db.collection('users').document(user_id)
        batch.update(user_ref, {'balance': new_balance})
        
        # تنفيذ كل العمليات
        batch.commit()
        
        # تحديث الذاكرة
        users_wallets[user_id] = new_balance
        
        # حذف السلة
        user_carts.pop(user_id, None)
        db.collection('carts').document(user_id).delete()
        
        # فصل المنتجات الفورية عن اليدوية
        instant_items = [i for i in purchased_items if i.get('delivery_type') == 'instant']
        manual_items = [i for i in purchased_items if i.get('delivery_type') == 'manual']
        
        # إرسال البيانات للمشتري عبر البوت
        try:
            msg = "🎉 تم شراء سلتك بنجاح!\n\n"
            
            # المنتجات الفورية
            if instant_items:
                msg += "⚡ منتجات تسليم فوري:\n"
                for item in instant_items:
                    msg += f"📦 {item['name']}\n"
                    msg += f"💰 {item['price']} ر.س\n"
                    msg += f"🆔 #{item['order_id']}\n"
                    if item.get('hidden_data'):
                        msg += f"🔐 البيانات:\n{item['hidden_data']}\n"
                    msg += "─────────────\n"
            
            # المنتجات اليدوية
            if manual_items:
                msg += "\n👨‍💼 منتجات تسليم يدوي (بانتظار التنفيذ):\n"
                for item in manual_items:
                    msg += f"📦 {item['name']}\n"
                    msg += f"💰 {item['price']} ر.س\n"
                    msg += f"🆔 #{item['order_id']}\n"
                    msg += "⏳ سيتم تنفيذه قريباً\n"
                    msg += "─────────────\n"
            
            msg += f"\n💳 رصيدك المتبقي: {new_balance:.2f} ر.س"
            
            bot.send_message(int(user_id), msg)
        except Exception as e:
            print(f"⚠️ فشل إرسال رسالة للمشتري: {e}")
        
        # إشعار الأدمن للطلبات اليدوية
        if manual_items:
            try:
                for item in manual_items:
                    claim_markup = telebot.types.InlineKeyboardMarkup()
                    claim_markup.add(telebot.types.InlineKeyboardButton(
                        "📋 استلام الطلب", 
                        callback_data=f"claim_order_{item['order_id']}"
                    ))
                    
                    admin_msg = f"🆕 طلب يدوي جديد من السلة!\n\n"
                    admin_msg += f"🆔 رقم الطلب: #{item['order_id']}\n"
                    admin_msg += f"📦 المنتج: {item['name']}\n"
                    admin_msg += f"👤 المشتري: {buyer_name} ({user_id})\n"
                    admin_msg += f"💰 السعر: {item['price']} ر.س\n"
                    if item.get('buyer_details'):
                        admin_msg += f"\n📝 معلومات المشتري:\n{item['buyer_details']}\n"
                    admin_msg += f"\n👇 اضغط لاستلام الطلب"
                    
                    bot.send_message(ADMIN_ID, admin_msg, reply_markup=claim_markup)
            except Exception as e:
                print(f"⚠️ فشل إشعار الأدمن: {e}")
        
        # إشعار عام للأدمن
        try:
            admin_msg = f"🛒 شراء سلة جديد!\n\n"
            admin_msg += f"👤 المشتري: {buyer_name} ({user_id})\n"
            admin_msg += f"📦 عدد المنتجات: {len(purchased_items)}\n"
            admin_msg += f"⚡ فوري: {len(instant_items)} | 👨‍💼 يدوي: {len(manual_items)}\n"
            admin_msg += f"💰 الإجمالي: {total:.2f} ر.س"
            bot.send_message(ADMIN_ID, admin_msg)
        except:
            pass
        
        return jsonify({
            'status': 'success',
            'message': 'تم الشراء بنجاح!',
            'purchased_count': len(purchased_items),
            'total': total,
            'new_balance': new_balance,
            'order_ids': order_ids
        })
        
    except Exception as e:
        print(f"❌ خطأ في إتمام الشراء: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'status': 'error', 'message': 'حدث خطأ في إتمام الشراء'})

@app.route('/api/cart/count')
def api_cart_count():
    """جلب عدد منتجات السلة"""
    user_id = request.args.get('user_id')
    if not user_id:
        return jsonify({'count': 0})
    
    cart = user_carts.get(str(user_id), {})
    count = len(cart.get('items', []))
    return jsonify({'count': count})

# صفحة الشحن المنفصلة

@app.route('/wallet')
def wallet_page():
    """صفحة المحفظة والشحن"""
    # استخدام الجلسة فقط لمنع تسريب بيانات المستخدمين
    user_id = session.get('user_id')
    
    if not user_id:
        return redirect('/')
    
    # جلب الرصيد
    balance = get_balance(user_id)
    
    # جلب المعاملات من Firebase
    transactions = []
    total_charges = 0
    charges_count = 0
    purchases_count = 0
    
    try:
        # جلب الشحنات
        charges_ref = query_where(db.collection('charge_history'), 'user_id', '==', str(user_id))
        for doc in charges_ref.stream():
            data = doc.to_dict()
            amount = data.get('amount', 0)
            total_charges += amount
            charges_count += 1
            transactions.append({
                'type': 'income',
                'title': 'شحن رصيد',
                'amount': amount,
                'date': data.get('date', 'غير محدد'),
                'timestamp': data.get('timestamp', 0)
            })
        
        # جلب المشتريات (للسجل والإحصائيات)
        orders_ref = query_where(db.collection('orders'), 'buyer_id', '==', str(user_id))
        for doc in orders_ref.stream():
            data = doc.to_dict()
            purchases_count += 1
            
            # تحويل التاريخ
            date_str = 'غير محدد'
            timestamp_val = 0
            if data.get('created_at'):
                try:
                    created = data['created_at']
                    if hasattr(created, 'seconds'):
                        timestamp_val = created.seconds
                        from datetime import datetime, timedelta, timezone
                        utc_time = datetime.fromtimestamp(created.seconds, tz=timezone.utc)
                        saudi_time = utc_time + timedelta(hours=3)
                        date_str = saudi_time.strftime('%Y-%m-%d %H:%M')
                    elif isinstance(created, datetime):
                        timestamp_val = created.timestamp()
                        saudi_time = created + timedelta(hours=3)
                        date_str = saudi_time.strftime('%Y-%m-%d %H:%M')
                except:
                    pass
            
            # إضافة للسجل كخصم
            transactions.append({
                'type': 'expense',
                'title': f"شراء {data.get('item_name', 'منتج')}",
                'amount': data.get('price', 0),
                'date': date_str,
                'timestamp': timestamp_val
            })
        
        # ترتيب من الأحدث
        transactions.sort(key=lambda x: x.get('timestamp', 0), reverse=True)
        transactions = transactions[:15]  # آخر 15 معاملة
        
    except Exception as e:
        print(f"❌ خطأ في جلب المعاملات: {e}")
    
    return render_template('wallet.html', 
                                  user_id=user_id,
                                  balance=balance,
                                  transactions=transactions,
                                  total_charges=total_charges,
                                  charges_count=charges_count,
                                  purchases_count=purchases_count)

# ===== معالجة الدفع من المحفظة =====
@app.route('/wallet/pay', methods=['POST'])
@limiter.limit("5 per minute")
def wallet_pay():
    """معالجة طلب الشحن من صفحة المحفظة"""
    
    # التحقق من تسجيل الدخول
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({'success': False, 'message': 'يجب تسجيل الدخول أولاً'})
    
    try:
        data = request.json
        phone = data.get('phone', '').strip()
        amount = float(data.get('amount', 0))
        
        # التحقق من البيانات
        if not phone or len(phone) < 10:
            return jsonify({'success': False, 'message': 'رقم جوال غير صحيح'})
        
        if amount < 10 or amount > 5000:
            return jsonify({'success': False, 'message': 'المبلغ يجب أن يكون بين 10 و 5000 ريال'})
        
        # التحقق من إعدادات EdfaPay
        if not EDFAPAY_MERCHANT_ID or not EDFAPAY_PASSWORD:
            return jsonify({'success': False, 'message': 'بوابة الدفع غير مفعلة'})
        
        # استخدام نفس طريقة التيليجرام بالضبط
        amount_int = int(amount)
        order_id = f"TR{user_id}{int(time.time())}"
        order_description = f"Recharge {amount_int} SAR"
        
        # حساب الـ hash - نفس طريقة التيليجرام بالضبط
        to_hash = f"{order_id}{amount_int}SAR{order_description}{EDFAPAY_PASSWORD}".upper()
        md5_hash = hashlib.md5(to_hash.encode()).hexdigest()
        final_hash = hashlib.sha1(md5_hash.encode()).hexdigest()
        
        # إنشاء طلب EdfaPay - نفس التيليجرام
        payload = {
            'action': 'SALE',
            'edfa_merchant_id': EDFAPAY_MERCHANT_ID,
            'order_id': order_id,
            'order_amount': str(amount_int),
            'order_currency': 'SAR',
            'order_description': order_description,
            'req_token': 'N',
            'payer_first_name': 'Customer',
            'payer_last_name': 'User',
            'payer_address': 'Riyadh',
            'payer_country': 'SA',
            'payer_city': 'Riyadh',
            'payer_zip': '12221',
            'payer_email': f'user{user_id}@telegram.com',
            'payer_phone': '966500000000',
            'payer_ip': '176.44.76.222',
            'term_url_3ds': f"{SITE_URL}/payment/success?order_id={order_id}",
            'auth': 'N',
            'recurring_init': 'N',
            'hash': final_hash
        }
        
        print(f"📤 Wallet Pay Request: {payload}")
        
        response = requests.post(EDFAPAY_API_URL, data=payload, timeout=30)
        
        print(f"📥 EdfaPay Raw Response: {response.text}")
        
        try:
            result = response.json()
        except:
            print(f"❌ فشل في تحليل JSON: {response.text}")
            return jsonify({'success': False, 'message': 'خطأ في بوابة الدفع - حاول مرة أخرى'})
        
        print(f"📥 EdfaPay Response: {result}")
        
        if response.status_code == 200 and result.get('redirect_url'):
            payment_url = result.get('redirect_url')
            
            # حفظ الطلب المعلق
            pending_payments[order_id] = {
                'user_id': str(user_id),
                'amount': amount,
                'order_id': order_id,
                'phone': phone,
                'status': 'pending',
                'created_at': time.time()
            }
            
            # حفظ في Firebase
            try:
                db.collection('pending_payments').document(order_id).set({
                    'user_id': str(user_id),
                    'amount': amount,
                    'order_id': order_id,
                    'phone': phone,
                    'status': 'pending',
                    'created_at': firestore.SERVER_TIMESTAMP
                })
            except Exception as e:
                print(f"⚠️ خطأ في حفظ الطلب: {e}")
            
            return jsonify({
                'success': True,
                'payment_url': payment_url,
                'order_id': order_id
            })
        else:
            error_msg = result.get('message') or result.get('error') or result.get('error_message') or 'فشل في إنشاء طلب الدفع'
            print(f"❌ EdfaPay Error: {error_msg}")
            return jsonify({'success': False, 'message': error_msg})
            
    except requests.exceptions.Timeout:
        print(f"❌ Wallet Pay Timeout")
        return jsonify({'success': False, 'message': 'انتهى وقت الاتصال - حاول مرة أخرى'})
    except requests.exceptions.RequestException as e:
        print(f"❌ Wallet Pay Request Error: {e}")
        return jsonify({'success': False, 'message': 'خطأ في الاتصال ببوابة الدفع'})
    except Exception as e:
        print(f"❌ Wallet Pay Error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'message': f'حدث خطأ: {str(e)}'})

# صفحة مشترياتي المنفصلة

@app.route('/my_purchases')
def my_purchases_page():
    """صفحة مشترياتي المنفصلة"""
    # استخدام الجلسة فقط لمنع تسريب بيانات المستخدمين
    user_id = session.get('user_id')
    
    if not user_id:
        return redirect('/')
    
    # جلب مشتريات المستخدم من Firebase
    purchases = []
    try:
        from datetime import datetime, timedelta, timezone
        orders_ref = query_where(db.collection('orders'), 'buyer_id', '==', str(user_id))
        for doc in orders_ref.stream():
            data = doc.to_dict()
            data['id'] = doc.id
            # تحويل الوقت إلى توقيت السعودية (UTC+3)
            if data.get('created_at'):
                try:
                    created = data['created_at']
                    # إذا كان Firestore Timestamp
                    if hasattr(created, 'seconds'):
                        utc_time = datetime.fromtimestamp(created.seconds, tz=timezone.utc)
                    elif isinstance(created, datetime):
                        utc_time = created
                    else:
                        utc_time = datetime.now(tz=timezone.utc)
                    
                    # إضافة 3 ساعات لتوقيت السعودية
                    saudi_time = utc_time + timedelta(hours=3)
                    data['sold_at'] = saudi_time.strftime('%Y-%m-%d %H:%M')
                    data['sort_time'] = saudi_time.timestamp()
                except Exception as e:
                    print(f"خطأ في تحويل الوقت: {e}")
                    data['sold_at'] = 'غير محدد'
                    data['sort_time'] = 0
            else:
                data['sold_at'] = 'غير محدد'
                data['sort_time'] = 0
            purchases.append(data)
        # ترتيب من الأحدث للأقدم
        purchases.sort(key=lambda x: x.get('sort_time', 0), reverse=True)
    except Exception as e:
        print(f"❌ خطأ في جلب المشتريات: {e}")
    
    return render_template('purchases.html', purchases=purchases)

@app.route('/get_balance')
def get_balance_api():
    # استخدام الجلسة فقط لمنع كشف أرصدة المستخدمين
    user_id = session.get('user_id')
    
    if not user_id:
        return {'balance': 0}
    
    balance = get_balance(user_id)
    return {'balance': balance}

@app.route('/charge_balance', methods=['POST'])
@limiter.limit("5 per minute")  # 🔒 Rate Limiting: منع تخمين مفاتيح الشحن
def charge_balance_api():
    """شحن الرصيد باستخدام كود الشحن"""
    data = request.json
    key_code = data.get('charge_key', '').strip()
    
    # ===== التحقق الآمن من هوية المستخدم =====
    if not session.get('user_id'):
        return jsonify({'success': False, 'message': 'يجب تسجيل الدخول أولاً!'})
    
    user_id = str(session.get('user_id'))
    
    if not key_code:
        return jsonify({'success': False, 'message': 'الرجاء إدخال كود الشحن'})
    
    # البحث عن الكود في Firebase مباشرة
    key_data = None
    
    # أولاً: البحث في الذاكرة
    if key_code in charge_keys:
        key_data = charge_keys[key_code]
    else:
        # ثانياً: البحث في Firebase
        try:
            doc_ref = db.collection('charge_keys').document(key_code)
            doc = doc_ref.get()
            if doc.exists:
                key_data = doc.to_dict()
                # إضافته للذاكرة
                charge_keys[key_code] = key_data
        except Exception as e:
            print(f"خطأ في البحث عن الكود في Firebase: {e}")
    
    # التحقق من وجود الكود
    if not key_data:
        return jsonify({'success': False, 'message': 'كود الشحن غير صحيح أو غير موجود'})
    
    # التحقق من أن الكود لم يستخدم
    if key_data.get('used', False):
        return jsonify({'success': False, 'message': 'هذا الكود تم استخدامه مسبقاً'})
    
    # شحن الرصيد
    amount = key_data['amount']
    current_balance = get_balance(user_id)
    new_balance = current_balance + amount
    
    # تحديث الرصيد في الذاكرة
    users_wallets[user_id] = new_balance
    
    # تحديث الكود كمستخدم
    charge_keys[key_code]['used'] = True
    charge_keys[key_code]['used_by'] = user_id
    charge_keys[key_code]['used_at'] = time.time()
    
    # تحديث في Firebase
    if db:
        try:
            # تحديث رصيد المستخدم
            user_ref = db.collection('users').document(user_id)
            user_doc = user_ref.get()
            if user_doc.exists:
                user_ref.update({'balance': new_balance})
            else:
                user_ref.set({'user_id': user_id, 'balance': new_balance})
            
            # تحديث حالة الكود
            db.collection('charge_keys').document(key_code).update({
                'used': True,
                'used_by': user_id,
                'used_at': time.time()
            })
            
            # حفظ سجل الشحنة
            from datetime import datetime
            db.collection('charge_history').add({
                'user_id': user_id,
                'amount': amount,
                'key_code': key_code,
                'date': datetime.now().strftime('%Y-%m-%d %H:%M'),
                'timestamp': time.time(),
                'type': 'charge'
            })
        except Exception as e:
            print(f"خطأ في تحديث Firebase: {e}")
    
    return jsonify({
        'success': True, 
        'message': f'تم شحن {amount} ريال بنجاح!',
        'new_balance': new_balance
    })

@app.route('/sell', methods=['POST'])
def sell_item():
    data = request.json
    seller_id = str(data.get('seller_id'))
    
    # التحقق من أن البائع هو المالك فقط
    if int(seller_id) != ADMIN_ID:
        return {'status': 'error', 'message': 'غير مصرح لك بإضافة منتجات! فقط المالك يمكنه ذلك.'}
    
    # حفظ البيانات المخفية بشكل آمن
    item = {
        'id': str(uuid.uuid4()),  # رقم فريد لا يتكرر
        'item_name': data.get('item_name'),
        'price': data.get('price'),
        'seller_id': seller_id,
        'seller_name': data.get('seller_name'),
        'hidden_data': data.get('hidden_data', ''),  # البيانات المخفية
        'category': data.get('category', ''),  # الفئة
        'image_url': data.get('image_url', '')  # رابط الصورة
    }
    marketplace_items.append(item)
    return {'status': 'success'}

@app.route('/buy', methods=['POST'])
@limiter.limit("10 per minute")  # 🔒 Rate Limiting: منع الشراء الآلي
def buy_item():
    try:
        data = request.json
        item_id = str(data.get('item_id'))  # تأكد أنه نص
        buyer_details = sanitize(data.get('buyer_details', ''))  # ✅ تنظيف XSS

        # ===== التحقق الآمن من هوية المشتري =====
        # لا نثق بـ buyer_id القادم من الطلب!
        # نأخذه فقط من الـ session (بعد تسجيل الدخول)
        
        buyer_id = None
        buyer_name = None
        
        # 1️⃣ التحقق من الجلسة (المستخدم مسجل دخول)
        if session.get('user_id'):
            buyer_id = str(session.get('user_id'))
            buyer_name = session.get('user_name', 'مستخدم')
            print(f"✅ مشتري موثق من الجلسة: {buyer_id}")
        else:
            # 2️⃣ لم يسجل دخول - نرفض الطلب
            print(f"❌ محاولة شراء بدون تسجيل دخول!")
            return {'status': 'error', 'message': 'يجب تسجيل الدخول أولاً!'}
        
        print(f"🛒 محاولة شراء - item_id: {item_id}, buyer_id: {buyer_id}")

        # 1. البحث عن المنتج في Firebase مباشرة
        doc_ref = db.collection('products').document(item_id)
        doc = doc_ref.get()

        if not doc.exists:
            print(f"❌ المنتج {item_id} غير موجود في Firebase")
            # محاولة البحث في الذاكرة كاحتياط
            item = None
            for prod in marketplace_items:
                if prod.get('id') == item_id:
                    item = prod
                    print(f"✅ تم إيجاد المنتج في الذاكرة: {item.get('item_name')}")
                    break
            
            if not item:
                return {'status': 'error', 'message': 'المنتج غير موجود أو تم حذفه!'}
        else:
            item = doc.to_dict()
            item['id'] = doc.id
            print(f"✅ تم إيجاد المنتج في Firebase: {item.get('item_name')}")

        # 2. التحقق من أن المنتج لم يُباع
        if item.get('sold', False):
            return {'status': 'error', 'message': 'عذراً، هذا المنتج تم بيعه للتو! 🚫'}

        price = float(item.get('price', 0))

        # 3. التحقق الفعلي من إمكانية إرسال رسالة للمشتري (قبل إتمام الشراء)
        # نرسل رسالة حقيقية لأن chat_action لا تفشل حتى لو المستخدم حظر البوت
        try:
            test_msg = bot.send_message(
                int(buyer_id),
                "🛒",  # رسالة قصيرة جداً
                disable_notification=True  # بدون صوت إشعار
            )
            bot.delete_message(int(buyer_id), test_msg.message_id)
            print(f"✅ تم التحقق من إمكانية إرسال الرسائل للمشتري {buyer_id}")
        except Exception as e:
            print(f"❌ فشل التحقق من المشتري {buyer_id}: {e}")
            # إنشاء رسالة الخطأ مع رابط البوت
            bot_link = f"@{BOT_USERNAME}" if BOT_USERNAME else "البوت"
            error_msg = f'⚠️ لا يمكن إرسال البيانات لك!\n\nتأكد أنك:\n1. لم تحظر البوت {bot_link}\n2. لم تحذف المحادثة معه\n\nأو اذهب للبوت واضغط /start ثم حاول مرة أخرى'
            return {'status': 'error', 'message': error_msg}

        # 4. التحقق من رصيد المشتري (من Firebase مباشرة)
        user_ref = db.collection('users').document(buyer_id)
        user_doc = user_ref.get()
        
        if not user_doc.exists:
            return {'status': 'error', 'message': 'حدث خطأ! حاول مرة أخرى.'}
        
        user_data = user_doc.to_dict()
        current_balance = user_data.get('balance', 0.0)

        if current_balance < price:
            return {'status': 'error', 'message': 'رصيدك غير كافي للشراء!'}

        # 4. تنفيذ العملية (خصم + تحديث حالة المنتج)
        # نستخدم batch لضمان تنفيذ كل الخطوات معاً أو فشلها معاً
        batch = db.batch()

        # خصم الرصيد
        new_balance = current_balance - price
        batch.update(user_ref, {'balance': new_balance})

        # تحديث المنتج كمباع (تأكد من استخدام document reference الصحيح)
        product_doc_ref = db.collection('products').document(item_id)
        batch.set(product_doc_ref, {
            'sold': True,
            'buyer_id': buyer_id,
            'buyer_name': buyer_name,
            'sold_at': firestore.SERVER_TIMESTAMP
        }, merge=True)

        # حفظ الطلب
        order_id = f"ORD_{random.randint(100000, 999999)}"
        order_ref = db.collection('orders').document(order_id)
        
        # تحديد نوع التسليم
        delivery_type = item.get('delivery_type', 'instant')
        order_status = 'completed' if delivery_type == 'instant' else 'pending'
        
        batch.set(order_ref, {
            'buyer_id': buyer_id,
            'buyer_name': buyer_name,
            'item_name': item.get('item_name'),
            'price': price,
            'hidden_data': item.get('hidden_data'),
            'buyer_details': buyer_details,  # تفاصيل المشتري للتسليم اليدوي
            'buyer_instructions': item.get('buyer_instructions', ''),  # ما كان مطلوب من المشتري
            'details': item.get('details', ''),
            'category': item.get('category', ''),
            'image_url': item.get('image_url', ''),
            'seller_id': item.get('seller_id'),
            'delivery_type': delivery_type,
            'status': order_status,
            'created_at': firestore.SERVER_TIMESTAMP
        })

        # تنفيذ التغييرات
        try:
            batch.commit()
            print(f"✅ تم حفظ الطلب في Firebase: {order_id} (نوع: {delivery_type})")
        except Exception as batch_error:
            print(f"❌ فشل حفظ الطلب في Firebase: {batch_error}")
            return {'status': 'error', 'message': 'فشل حفظ الطلب! حاول مرة أخرى'}
        
        # التحقق من حفظ الطلب (للتسليم اليدوي فقط)
        if delivery_type == 'manual':
            try:
                verify_order = db.collection('orders').document(order_id).get()
                if verify_order.exists:
                    print(f"✅ تم التحقق من وجود الطلب: {order_id}")
                else:
                    print(f"⚠️ الطلب غير موجود بعد الحفظ: {order_id}")
            except Exception as verify_error:
                print(f"⚠️ فشل التحقق من الطلب: {verify_error}")

        # 5. تحديث الذاكرة المحلية (اختياري لكن جيد للسرعة)
        users_wallets[buyer_id] = new_balance
        # البحث عن المنتج في القائمة المحلية وتحديثه
        for prod in marketplace_items:
            if prod.get('id') == item_id:
                prod['sold'] = True
                break

        # 6. إرسال المنتج للمشتري أو إشعار الأدمن
        hidden_info = item.get('hidden_data', 'لا توجد بيانات')
        message_sent = False
        
        if delivery_type == 'instant':
            # تسليم فوري - إرسال البيانات مباشرة للمشتري
            try:
                bot.send_message(
                    int(buyer_id),
                    f"✅ تم الشراء بنجاح!\n\n"
                    f"📦 المنتج: {item.get('item_name')}\n"
                    f"💰 السعر: {price} ريال\n"
                    f"🆔 رقم الطلب: #{order_id}\n\n"
                    f"🔐 بيانات الاشتراك:\n{hidden_info}\n\n"
                    f"⚠️ احفظ هذه البيانات في مكان آمن!"
                )
                message_sent = True
                print(f"✅ تم إرسال بيانات المنتج للمشتري {buyer_id}")
                
                # إشعار للمالك
                bot.send_message(
                    ADMIN_ID,
                    f"🔔 عملية بيع جديدة!\n"
                    f"📦 المنتج: {item.get('item_name')}\n"
                    f"👤 المشتري: {buyer_name} ({buyer_id})\n"
                    f"💰 السعر: {price} ريال\n"
                    f"✅ تم إرسال البيانات للمشتري"
                )
            except Exception as e:
                print(f"⚠️ فشل إرسال الرسالة للمشتري {buyer_id}: {e}")
                # إشعار المالك بالفشل
                try:
                    bot.send_message(
                        ADMIN_ID,
                        f"⚠️ تنبيه: فشل إرسال بيانات المنتج!\n"
                        f"📦 المنتج: {item.get('item_name')}\n"
                        f"👤 المشتري: {buyer_name} ({buyer_id})\n"
                        f"🔐 البيانات: {hidden_info}\n"
                        f"❌ السبب: {str(e)}"
                    )
                except:
                    pass
        else:
            # تسليم يدوي - إشعار المشتري بانتظار التنفيذ وإرسال للأدمنز
            try:
                bot.send_message(
                    int(buyer_id),
                    f"⏳ تم استلام طلبك!\n\n"
                    f"📦 المنتج: {item.get('item_name')}\n"
                    f"💰 السعر: {price} ريال\n"
                    f"🆔 رقم الطلب: #{order_id}\n\n"
                    f"👨‍💼 طلبك بانتظار التنفيذ من قبل الإدارة\n"
                    f"📲 سيتم إرسال البيانات لك فور تنفيذ الطلب"
                )
                message_sent = True
                print(f"✅ تم إشعار المشتري {buyer_id} بانتظار التنفيذ")
            except Exception as e:
                print(f"⚠️ فشل إرسال رسالة الانتظار للمشتري {buyer_id}: {e}")
            
            # إرسال إشعار لجميع الأدمنز مع زر التنفيذ
            claim_markup = telebot.types.InlineKeyboardMarkup()
            claim_markup.add(telebot.types.InlineKeyboardButton(
                "📋 استلام الطلب", 
                callback_data=f"claim_order_{order_id}"
            ))
            
            # 🔒 إخفاء بيانات المشتري في الإشعار الأولي للحماية
            # البيانات تظهر فقط للمشرف الذي يستلم الطلب
            hidden_buyer_details = ""
            if buyer_details:
                hidden_buyer_details = f"\n\n📝 بيانات المشتري: 🔒 ******** (تظهر عند الاستلام)"
            
            admin_message = (
                f"🆕 طلب جديد بانتظار التنفيذ!\n\n"
                f"🆔 رقم الطلب: #{order_id}\n"
                f"📦 المنتج: {item.get('item_name')}\n"
                f"👤 المشتري: {buyer_name}\n"
                f"💰 السعر: {price} ريال"
                f"{hidden_buyer_details}\n\n"
                f"👇 اضغط لاستلام وعرض التفاصيل"
            )
            
            # إرسال للمالك الرئيسي
            try:
                bot.send_message(ADMIN_ID, admin_message, reply_markup=claim_markup)
            except:
                pass
            


        # إرجاع البيانات للموقع
        return {
            'status': 'success',
            'hidden_data': hidden_info if delivery_type == 'instant' else None,
            'order_id': order_id,
            'message_sent': message_sent,
            'new_balance': new_balance,
            'delivery_type': delivery_type
        }

    except Exception as e:
        print(f"❌ Error in buy_item: {e}")
        return {'status': 'error', 'message': 'حدث خطأ أثناء الشراء، حاول مرة أخرى.'}

# ============================================
# === نقاط استقبال بوابة الدفع EdfaPay ===
# ============================================

# Webhook الديناميكي لـ EdfaPay (يستخدم merchant_id في الرابط)
@app.route('/merchant_webhook/<merchant_id>', methods=['GET', 'POST'])
def merchant_webhook(merchant_id):
    """استقبال إشعارات الدفع من EdfaPay على الرابط الديناميكي"""
    # تجاهل رسائل Telegram (تحتوي على update_id)
    if request.method == 'POST':
        data = request.json or request.form.to_dict()
        if data.get('update_id') or data.get('message'):
            # هذه رسالة من Telegram وليست من EdfaPay
            print(f"⚠️ تم تجاهل رسالة Telegram على merchant_webhook")
            return jsonify({'status': 'ok', 'message': 'Telegram message ignored'}), 200
    return process_edfapay_callback(request, f"merchant_webhook/{merchant_id}")

# دعم كلا الصيغتين: edfapay_webhook و edfapay-webhook
@app.route('/payment/edfapay_webhook', methods=['GET', 'POST'])
@app.route('/payment/edfapay-webhook', methods=['GET', 'POST'])
@limiter.limit("30 per minute")  # 🔒 Rate Limiting: منع هجمات الـ webhook
def edfapay_webhook():
    """استقبال إشعارات الدفع من EdfaPay"""
    return process_edfapay_callback(request, "edfapay_webhook")

def process_edfapay_callback(req, source):
    """معالجة callback من EdfaPay"""
    
    # إذا كان الطلب GET (فتح من المتصفح) - عرض رسالة
    if req.method == 'GET':
        return jsonify({
            'status': 'ok',
            'message': 'EdfaPay Webhook Endpoint',
            'description': 'This endpoint receives payment notifications from EdfaPay',
            'source': source,
            'method': 'POST only'
        })
    
    try:
        # جلب البيانات (تدعم JSON و form-data)
        data = {}
        if req.is_json:
            data = req.json or {}
        else:
            data = req.form.to_dict() or {}
        
        # إذا كانت البيانات فارغة، جرب query parameters
        if not data:
            data = req.args.to_dict() or {}
        
        print(f"📩 EdfaPay Webhook ({source}): {data}")
        
        # ===== 🔐 التحقق من صحة الطلب (Signature Verification) =====
        order_id = data.get('order_id', '')
        trans_id = data.get('trans_id', '')
        status = data.get('status', '') or data.get('result', '')
        amount = data.get('order_amount', '') or data.get('amount', '') or data.get('trans_amount', '')
        received_hash = data.get('hash', '')
        
        # التحقق من أن الطلب من EdfaPay وليس مزيف
        if order_id and EDFAPAY_PASSWORD:
            # 1️⃣ التحقق من وجود الطلب في النظام أولاً
            payment_exists = order_id in pending_payments
            if not payment_exists:
                try:
                    doc = db.collection('pending_payments').document(order_id).get()
                    payment_exists = doc.exists
                except:
                    pass
            
            if not payment_exists:
                print(f"🚫 محاولة webhook مزيفة! order_id غير موجود: {order_id}")
                # إرسال تنبيه أمني للمالك
                try:
                    if BOT_ACTIVE:
                        client_ip = req.headers.get('X-Forwarded-For', req.remote_addr)
                        alert_msg = f"""
⚠️ *تنبيه أمني - Webhook مشبوه!*

🔴 محاولة إرسال webhook لطلب غير موجود!

📋 Order ID: `{order_id}`
💰 المبلغ المزعوم: {amount}
🌐 IP: `{client_ip}`
⏰ الوقت: {time.strftime('%Y-%m-%d %H:%M:%S')}

_قد تكون محاولة اختراق!_
                        """
                        bot.send_message(ADMIN_ID, alert_msg, parse_mode='Markdown')
                except:
                    pass
                return jsonify({'status': 'error', 'message': 'Invalid order'}), 403
            
            # 2️⃣ التحقق من أن المبلغ المرسل يطابق المبلغ الأصلي
            original_payment = pending_payments.get(order_id)
            if not original_payment:
                try:
                    doc = db.collection('pending_payments').document(order_id).get()
                    if doc.exists:
                        original_payment = doc.to_dict()
                except:
                    pass
            
            if original_payment and amount:
                original_amount = float(original_payment.get('amount', 0))
                received_amount = float(amount) if amount else 0
                
                if original_amount != received_amount:
                    print(f"🚫 محاولة تزوير المبلغ! الأصلي: {original_amount}, المستلم: {received_amount}")
                    try:
                        if BOT_ACTIVE:
                            client_ip = req.headers.get('X-Forwarded-For', req.remote_addr)
                            alert_msg = f"""
⚠️ *تنبيه أمني - تزوير مبلغ!*

🔴 المبلغ المرسل لا يطابق المبلغ الأصلي!

📋 Order ID: `{order_id}`
💰 المبلغ الأصلي: {original_amount} ريال
💰 المبلغ المزيف: {received_amount} ريال
🌐 IP: `{client_ip}`

_محاولة اختراق واضحة!_
                            """
                            bot.send_message(ADMIN_ID, alert_msg, parse_mode='Markdown')
                    except:
                        pass
                    return jsonify({'status': 'error', 'message': 'Amount mismatch'}), 403
        
        print(f"📋 Parsed: order_id={order_id}, trans_id={trans_id}, status={status}, amount={amount}")
        
        # التحقق من وجود order_id
        if not order_id:
            print("⚠️ EdfaPay Webhook: لا يوجد order_id - قد يكون إشعار أولي")
            return jsonify({'status': 'ok', 'message': 'No order_id provided'}), 200
        
        # ===== تحديد حالة الدفع =====
        status_upper = str(status).upper().strip()
        
        # الحالات الناجحة
        SUCCESS_STATUSES = ['SUCCESS', 'SETTLED', 'CAPTURED', 'APPROVED', '3DS_SUCCESS']
        
        # الحالات المرفوضة/الفاشلة
        FAILED_STATUSES = ['DECLINED', 'FAILURE', 'FAILED', 'TXN_FAILURE', 'REJECTED', 'CANCELLED', 'ERROR', '3DS_FAILURE']
        
        # الحالات المعلقة (تحتاج انتظار)
        PENDING_STATUSES = ['PENDING', 'PROCESSING', 'REDIRECT', '3DS_REQUIRED']
        
        # ===== معالجة الحالات =====
        
        # 1️⃣ حالة النجاح
        if status_upper in SUCCESS_STATUSES:
            print(f"✅ EdfaPay: عملية ناجحة - {status}")
            
            # البحث عن الطلب في الذاكرة
            payment_data = pending_payments.get(order_id)
            
            # البحث في Firebase إذا لم يوجد في الذاكرة
            if not payment_data:
                try:
                    doc = db.collection('pending_payments').document(order_id).get()
                    if doc.exists:
                        payment_data = doc.to_dict()
                        print(f"📥 تم جلب الطلب من Firebase")
                except Exception as e:
                    print(f"⚠️ خطأ في البحث في Firebase: {e}")
            
            # التحقق من أن الطلب لم يُعالج مسبقاً (حماية من Replay Attack)
            if payment_data and payment_data.get('status') == 'completed':
                print(f"⚠️ محاولة إعادة استخدام webhook! الطلب {order_id} تم معالجته مسبقاً")
                return jsonify({'status': 'ok', 'message': 'Already processed'}), 200
            
            if payment_data and payment_data.get('status') != 'completed':
                user_id = str(payment_data.get('user_id', ''))
                pay_amount = float(payment_data.get('amount', amount or 0))
                is_merchant_invoice = payment_data.get('is_merchant_invoice', False)
                invoice_id = payment_data.get('invoice_id', '')
                
                if not user_id:
                    print(f"❌ لا يوجد user_id في الطلب")
                    return jsonify({'status': 'error', 'message': 'Missing user_id'}), 400
                
                # ✅ إضافة الرصيد
                add_balance(user_id, pay_amount)
                print(f"✅ تم إضافة {pay_amount} ريال للمستخدم {user_id}")
                
                # تحديث في الذاكرة
                if order_id in pending_payments:
                    pending_payments[order_id]['status'] = 'completed'
                
                # تحديث في Firebase
                try:
                    db.collection('pending_payments').document(order_id).update({
                        'status': 'completed',
                        'completed_at': firestore.SERVER_TIMESTAMP,
                        'trans_id': trans_id,
                        'edfapay_status': status,
                        'payment_data': data
                    })
                except Exception as e:
                    print(f"⚠️ خطأ في تحديث Firebase: {e}")
                
                # ===== إشعارات مختلفة حسب نوع الدفع =====
                
                if is_merchant_invoice and invoice_id:
                    # 🔹 فاتورة تاجر - إشعار التاجر
                    try:
                        # تحديث حالة الفاتورة
                        if invoice_id in merchant_invoices:
                            merchant_invoices[invoice_id]['status'] = 'completed'
                        
                        db.collection('merchant_invoices').document(invoice_id).update({
                            'status': 'completed',
                            'completed_at': firestore.SERVER_TIMESTAMP
                        })
                    except:
                        pass
                    
                    # إشعار التاجر
                    try:
                        new_balance = get_balance(user_id)
                        # جلب رقم العميل للمالك فقط
                        customer_phone = ''
                        if invoice_id:
                            if invoice_id in merchant_invoices:
                                customer_phone = merchant_invoices[invoice_id].get('customer_phone', '')
                            if not customer_phone:
                                try:
                                    inv_doc = db.collection('merchant_invoices').document(invoice_id).get()
                                    if inv_doc.exists:
                                        customer_phone = inv_doc.to_dict().get('customer_phone', '')
                                except:
                                    pass
                        if not customer_phone:
                            customer_phone = 'غير محدد'
                        
                        # رسالة للتاجر (بدون رقم العميل)
                        bot.send_message(
                            int(user_id),
                            f"💰 *تم استلام دفعة جديدة!*\n\n"
                            f"🧾 رقم الفاتورة: `{invoice_id}`\n"
                            f"💵 المبلغ: {pay_amount} ريال\n\n"
                            f"💳 رصيدك الحالي: {new_balance} ريال\n\n"
                            f"✅ تم إضافة المبلغ لرصيدك",
                            parse_mode="Markdown"
                        )
                    except Exception as e:
                        print(f"⚠️ خطأ في إرسال إشعار للتاجر: {e}")
                    
                    # إشعار المالك (مفصّل للحماية والتوثيق)
                    try:
                        merchant_name = merchant_invoices.get(invoice_id, {}).get('merchant_name', 'غير معروف')
                        bot.send_message(
                            ADMIN_ID,
                            f"🧾 *دفع فاتورة تاجر!*\n\n"
                            f"👤 التاجر: {merchant_name}\n"
                            f"🆔 آيدي: `{user_id}`\n"
                            f"💰 المبلغ: {pay_amount} ريال\n"
                            f"📋 الفاتورة: `{invoice_id}`\n"
                            f"📱 رقم العميل: `{customer_phone}`\n"
                            f"🔗 EdfaPay: `{trans_id}`",
                            parse_mode="Markdown"
                        )
                    except:
                        pass
                else:
                    # 🔹 شحن عادي - إشعار المستخدم
                    try:
                        new_balance = get_balance(user_id)
                        bot.send_message(
                            int(user_id),
                            f"✅ *تم شحن رصيدك بنجاح!*\n\n"
                            f"💰 المبلغ المضاف: {pay_amount} ريال\n"
                            f"💵 رصيدك الحالي: {new_balance} ريال\n\n"
                            f"📋 رقم العملية: `{order_id}`\n\n"
                            f"🎉 استمتع بالتسوق!",
                            parse_mode="Markdown"
                        )
                    except Exception as e:
                        print(f"⚠️ خطأ في إرسال إشعار: {e}")
                    
                    # إشعار المالك
                    try:
                        bot.send_message(
                            ADMIN_ID,
                            f"💳 *دفعة جديدة ناجحة!*\n\n"
                            f"👤 المستخدم: `{user_id}`\n"
                            f"💰 المبلغ: {pay_amount} ريال\n"
                            f"📋 الطلب: `{order_id}`\n"
                            f"🔗 EdfaPay: `{trans_id}`",
                            parse_mode="Markdown"
                        )
                    except:
                        pass
                
                return jsonify({'status': 'success', 'message': 'Payment processed'})
            
            elif payment_data and payment_data.get('status') == 'completed':
                print(f"⚠️ الطلب {order_id} تم معالجته مسبقاً")
                return jsonify({'status': 'success', 'message': 'Already processed'})
            
            else:
                print(f"❌ الطلب {order_id} غير موجود")
                return jsonify({'status': 'error', 'message': 'Order not found'}), 404
        
        # 2️⃣ حالة الفشل/الرفض
        elif status_upper in FAILED_STATUSES:
            print(f"❌ EdfaPay: عملية مرفوضة - {status}")
            
            # البحث عن بيانات الطلب لإرسال إشعار للعميل
            payment_data = pending_payments.get(order_id)
            if not payment_data:
                try:
                    doc = db.collection('pending_payments').document(order_id).get()
                    if doc.exists:
                        payment_data = doc.to_dict()
                except:
                    pass
            
            # تحديث حالة الطلب
            try:
                db.collection('pending_payments').document(order_id).update({
                    'status': 'failed',
                    'failed_at': firestore.SERVER_TIMESTAMP,
                    'failure_reason': data.get('decline_reason', status),
                    'payment_data': data
                })
            except:
                pass
            
            # ✅ إشعار العميل بالفشل
            if payment_data:
                try:
                    user_id = payment_data.get('user_id')
                    pay_amount = payment_data.get('amount', 0)
                    is_merchant_invoice = payment_data.get('is_merchant_invoice', False)
                    
                    # تنظيف سبب الرفض من الأحرف الخاصة
                    decline_reason = data.get('decline_reason', 'فشلت العملية')
                    # إزالة الأحرف التي تسبب مشاكل في Markdown
                    decline_reason = decline_reason.replace('_', ' ').replace('*', '').replace('`', '').replace('[', '').replace(']', '')
                    # اختصار الرسالة إذا كانت طويلة
                    if len(decline_reason) > 50:
                        decline_reason = 'تم رفض البطاقة'
                    
                    # رسالة مختلفة حسب نوع الدفع
                    if is_merchant_invoice:
                        msg_text = f"❌ فشلت عملية الدفع\n\n💰 المبلغ: {pay_amount} ريال\n❗ السبب: {decline_reason}\n\n💡 أخبر العميل بالمحاولة مرة أخرى"
                    else:
                        msg_text = f"❌ فشلت عملية الشحن\n\n💰 المبلغ: {pay_amount} ريال\n❗ السبب: {decline_reason}\n\n💡 تأكد من رصيد البطاقة أو جرب بطاقة أخرى"
                    
                    bot.send_message(int(user_id), msg_text)
                except Exception as e:
                    print(f"⚠️ خطأ في إرسال إشعار للعميل: {e}")
            
            # إشعار المالك بالفشل
            try:
                raw_reason = data.get('decline_reason', status)
                clean_reason = str(raw_reason).replace('_', ' ').replace('*', '').replace('`', '')[:100]
                
                # جلب بيانات إضافية للمالك
                merchant_id = payment_data.get('user_id', 'غير محدد') if payment_data else 'غير محدد'
                invoice_id = payment_data.get('invoice_id', '') if payment_data else ''
                is_merchant_inv = payment_data.get('is_merchant_invoice', False) if payment_data else False
                
                # جلب رقم العميل إن وجد
                customer_phone = 'غير محدد'
                if invoice_id and invoice_id in merchant_invoices:
                    customer_phone = merchant_invoices[invoice_id].get('customer_phone', 'غير محدد')
                
                if is_merchant_inv:
                    bot.send_message(
                        ADMIN_ID,
                        f"❌ فشل دفع فاتورة تاجر\n\n"
                        f"👤 التاجر: {merchant_id}\n"
                        f"🧾 الفاتورة: {invoice_id}\n"
                        f"📱 رقم العميل: {customer_phone}\n"
                        f"❗ السبب: {clean_reason}"
                    )
                else:
                    bot.send_message(
                        ADMIN_ID,
                        f"❌ عملية شحن مرفوضة\n\n"
                        f"👤 المستخدم: {merchant_id}\n"
                        f"📋 الطلب: {order_id}\n"
                        f"❗ السبب: {clean_reason}"
                    )
            except:
                pass
            
            return jsonify({'status': 'success', 'message': f'Payment failed: {status}'})
        
        # 3️⃣ حالة معلقة
        elif status_upper in PENDING_STATUSES:
            print(f"⏳ EdfaPay: عملية معلقة - {status}")
            return jsonify({'status': 'success', 'message': f'Payment pending: {status}'})
        
        # 4️⃣ حالة غير معروفة
        else:
            print(f"❓ EdfaPay: حالة غير معروفة - {status}")
            # لا نضيف رصيد لحالات غير معروفة
            return jsonify({'status': 'success', 'message': f'Unknown status: {status}'})
            
    except Exception as e:
        print(f"❌ خطأ في معالجة webhook: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'status': 'error', 'message': str(e)}), 500

# ============================================
# === نقاط استقبال بوابة الدفع (Legacy) ===
# ============================================

@app.route('/payment/adfaly_webhook', methods=['GET', 'POST'])
def adfaly_webhook():
    """استقبال إشعارات الدفع من Adfaly Pay"""
    
    # إذا كان الطلب GET (فتح من المتصفح) - عرض رسالة
    if request.method == 'GET':
        return jsonify({
            'status': 'ok',
            'message': 'Adfaly Pay Webhook Endpoint',
            'description': 'This endpoint receives payment notifications from Adfaly Pay',
            'method': 'POST only'
        })
    
    try:
        # جلب البيانات
        data = request.json or request.form.to_dict()
        print(f"📩 Adfaly Webhook: {data}")
        
        # استخراج البيانات المهمة
        invoice_id = data.get('invoice_id') or data.get('order_id') or data.get('id')
        status = data.get('status') or data.get('payment_status')
        amount = data.get('amount') or data.get('paid_amount')
        
        if not invoice_id:
            print("❌ Adfaly Webhook: لا يوجد invoice_id")
            return jsonify({'status': 'error', 'message': 'Missing invoice_id'}), 400
        
        # التحقق من حالة الدفع
        if status and status.lower() in ['paid', 'success', 'completed', 'successful']:
            # البحث عن الطلب
            payment_data = pending_payments.get(invoice_id)
            
            if not payment_data:
                # البحث في Firebase
                try:
                    doc = db.collection('pending_payments').document(invoice_id).get()
                    if doc.exists:
                        payment_data = doc.to_dict()
                except:
                    pass
            
            if payment_data and payment_data.get('status') != 'completed':
                user_id = payment_data['user_id']
                pay_amount = float(payment_data.get('amount', amount or 0))
                
                # إضافة الرصيد
                add_balance(user_id, pay_amount)
                
                # تحديث حالة الطلب
                if invoice_id in pending_payments:
                    pending_payments[invoice_id]['status'] = 'completed'
                
                # تحديث في Firebase
                try:
                    db.collection('pending_payments').document(invoice_id).update({
                        'status': 'completed',
                        'completed_at': firestore.SERVER_TIMESTAMP
                    })
                except Exception as e:
                    print(f"⚠️ خطأ في تحديث Firebase: {e}")
                
                # إرسال إشعار للمستخدم عبر البوت
                try:
                    new_balance = get_balance(user_id)
                    bot.send_message(
                        int(user_id),
                        f"✅ *تم شحن رصيدك بنجاح!*\n\n"
                        f"💰 المبلغ المضاف: {pay_amount} ريال\n"
                        f"💵 رصيدك الحالي: {new_balance} ريال\n\n"
                        f"📋 رقم العملية: `{invoice_id}`\n\n"
                        f"🎉 استمتع بالتسوق!",
                        parse_mode="Markdown"
                    )
                except Exception as e:
                    print(f"⚠️ خطأ في إرسال إشعار للمستخدم: {e}")
                
                # إشعار المالك
                try:
                    bot.send_message(
                        ADMIN_ID,
                        f"💳 *تم استلام دفعة جديدة!*\n\n"
                        f"👤 المستخدم: {user_id}\n"
                        f"💰 المبلغ: {pay_amount} ريال\n"
                        f"📋 رقم العملية: `{invoice_id}`\n"
                        f"✅ تم إضافة الرصيد تلقائياً",
                        parse_mode="Markdown"
                    )
                except:
                    pass
                
                print(f"✅ تم شحن {pay_amount} ريال للمستخدم {user_id}")
                return jsonify({'status': 'success', 'message': 'Payment processed'})
            
            else:
                print(f"⚠️ الطلب {invoice_id} غير موجود أو تم معالجته مسبقاً")
                return jsonify({'status': 'success', 'message': 'Already processed or not found'})
        
        else:
            print(f"ℹ️ Adfaly Webhook: حالة الدفع: {status}")
            return jsonify({'status': 'success', 'message': f'Status: {status}'})
            
    except Exception as e:
        print(f"❌ خطأ في adfaly_webhook: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/payment/success', methods=['GET', 'POST'])
def payment_success():
    """صفحة نتيجة الدفع - تتحقق من الحالة الفعلية"""
    
    # جلب بيانات النتيجة من EdfaPay
    data = {}
    if request.method == 'POST':
        data = request.form.to_dict() or request.json or {}
    else:
        data = request.args.to_dict() or {}
    
    print(f"📄 Payment Result Page: {data}")
    
    # استخراج الحالة
    status = data.get('status', '') or data.get('result', '')
    order_id = data.get('order_id', '')
    decline_reason = data.get('decline_reason', '')
    
    status_upper = str(status).upper().strip()
    
    # تحديد إذا كانت العملية ناجحة أم لا
    SUCCESS_STATUSES = ['SUCCESS', 'SETTLED', 'CAPTURED', 'APPROVED', '3DS_SUCCESS']
    FAILED_STATUSES = ['DECLINED', 'FAILURE', 'FAILED', 'TXN_FAILURE', 'REJECTED', 'CANCELLED', 'ERROR', '3DS_FAILURE']
    
    is_success = status_upper in SUCCESS_STATUSES
    is_failed = status_upper in FAILED_STATUSES
    
    # إذا كان هناك result=DECLINED مع status مختلف
    result = data.get('result', '').upper()
    if result == 'DECLINED' or result == 'FAILURE':
        is_success = False
        is_failed = True
    
    # إذا لم توجد بيانات، تحقق من Firebase باستخدام order_id
    if not status and order_id:
        try:
            doc = db.collection('pending_payments').document(order_id).get()
            if doc.exists:
                payment_data = doc.to_dict()
                payment_status = payment_data.get('status', '')
                if payment_status == 'completed':
                    is_success = True
                    is_failed = False
                elif payment_status == 'failed':
                    is_success = False
                    is_failed = True
                    decline_reason = payment_data.get('failure_reason', 'فشلت العملية')
        except Exception as e:
            print(f"⚠️ خطأ في التحقق من Firebase: {e}")
    
    # إذا لم توجد بيانات ولا order_id، ابحث عن آخر طلب للمستخدم
    if not status and not order_id:
        # نعرض صفحة عامة مع زر العودة
        pass
    
    if is_success:
        # ✅ صفحة النجاح
        return render_template_string('''
        <!DOCTYPE html>
        <html dir="rtl" lang="ar">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>تم الدفع بنجاح</title>
            <style>
                * { box-sizing: border-box; margin: 0; padding: 0; }
                body { 
                    font-family: 'Tajawal', sans-serif; 
                    background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%); 
                    min-height: 100vh;
                    display: flex;
                    justify-content: center;
                    align-items: center;
                    padding: 20px;
                }
                .container {
                    background: rgba(255,255,255,0.1);
                    backdrop-filter: blur(10px);
                    border-radius: 20px;
                    padding: 40px;
                    text-align: center;
                    max-width: 400px;
                    border: 1px solid rgba(255,255,255,0.2);
                }
                .icon { font-size: 80px; margin-bottom: 20px; animation: bounce 1s ease infinite; }
                @keyframes bounce {
                    0%, 100% { transform: translateY(0); }
                    50% { transform: translateY(-10px); }
                }
                h1 { color: #55efc4; margin-bottom: 15px; font-size: 24px; }
                p { color: #dfe6e9; margin-bottom: 25px; line-height: 1.6; }
                .btn {
                    display: inline-block;
                    background: linear-gradient(135deg, #00b894, #55efc4);
                    color: white;
                    padding: 15px 40px;
                    border-radius: 30px;
                    text-decoration: none;
                    font-weight: bold;
                    transition: transform 0.3s;
                }
                .btn:hover { transform: scale(1.05); }
            </style>
            <link href="https://fonts.googleapis.com/css2?family=Tajawal:wght@400;700&display=swap" rel="stylesheet">
        </head>
        <body>
            <div class="container">
                <div class="icon">✅</div>
                <h1>تم الدفع بنجاح!</h1>
                <p>تم شحن رصيدك بنجاح.<br>يمكنك الآن العودة للبوت والتسوق.</p>
                <a href="https://t.me/{{ bot_username }}" class="btn">العودة للبوت</a>
            </div>
        </body>
        </html>
        ''', bot_username=BOT_USERNAME)
    
    elif is_failed:
        # ❌ صفحة الفشل
        error_msg = decline_reason or status or "فشلت عملية الدفع"
        return render_template_string('''
        <!DOCTYPE html>
        <html dir="rtl" lang="ar">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>فشل الدفع</title>
            <style>
                * { box-sizing: border-box; margin: 0; padding: 0; }
                body { 
                    font-family: 'Tajawal', sans-serif; 
                    background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%); 
                    min-height: 100vh;
                    display: flex;
                    justify-content: center;
                    align-items: center;
                    padding: 20px;
                }
                .container {
                    background: rgba(255,255,255,0.1);
                    backdrop-filter: blur(10px);
                    border-radius: 20px;
                    padding: 40px;
                    text-align: center;
                    max-width: 400px;
                    border: 1px solid rgba(255,255,255,0.2);
                }
                .icon { font-size: 80px; margin-bottom: 20px; }
                h1 { color: #ff7675; margin-bottom: 15px; font-size: 24px; }
                p { color: #dfe6e9; margin-bottom: 15px; line-height: 1.6; }
                .error-box {
                    background: rgba(255, 118, 117, 0.2);
                    border: 1px solid rgba(255, 118, 117, 0.5);
                    border-radius: 10px;
                    padding: 15px;
                    margin-bottom: 25px;
                }
                .error-text { color: #ff7675; font-size: 14px; }
                .btn {
                    display: inline-block;
                    background: linear-gradient(135deg, #6c5ce7, #a29bfe);
                    color: white;
                    padding: 15px 40px;
                    border-radius: 30px;
                    text-decoration: none;
                    font-weight: bold;
                    transition: transform 0.3s;
                }
                .btn:hover { transform: scale(1.05); }
            </style>
            <link href="https://fonts.googleapis.com/css2?family=Tajawal:wght@400;700&display=swap" rel="stylesheet">
        </head>
        <body>
            <div class="container">
                <div class="icon">❌</div>
                <h1>فشل الدفع!</h1>
                <p>لم تتم عملية الدفع.</p>
                <div class="error-box">
                    <p class="error-text">{{ error_msg }}</p>
                </div>
                <p>يمكنك المحاولة مرة أخرى.</p>
                <a href="https://t.me/{{ bot_username }}" class="btn">العودة للبوت</a>
            </div>
        </body>
        </html>
        ''', bot_username=BOT_USERNAME, error_msg=error_msg)
    
    else:
        # ⏳ إذا لم توجد بيانات - نعرض صفحة تنتظر وتتحقق من Firebase
        # ثم تحول تلقائياً بعد ثانيتين
        return render_template_string('''
        <!DOCTYPE html>
        <html dir="rtl" lang="ar">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>جاري التحقق...</title>
            <style>
                * { box-sizing: border-box; margin: 0; padding: 0; }
                body { 
                    font-family: 'Tajawal', sans-serif; 
                    background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%); 
                    min-height: 100vh;
                    display: flex;
                    justify-content: center;
                    align-items: center;
                    padding: 20px;
                }
                .container {
                    background: rgba(255,255,255,0.1);
                    backdrop-filter: blur(10px);
                    border-radius: 20px;
                    padding: 40px;
                    text-align: center;
                    max-width: 400px;
                    border: 1px solid rgba(255,255,255,0.2);
                }
                .icon { font-size: 80px; margin-bottom: 20px; }
                .spinner {
                    width: 60px;
                    height: 60px;
                    border: 4px solid rgba(255,255,255,0.1);
                    border-top-color: #6c5ce7;
                    border-radius: 50%;
                    animation: spin 1s linear infinite;
                    margin: 0 auto 20px;
                }
                @keyframes spin {
                    to { transform: rotate(360deg); }
                }
                h1 { color: #a29bfe; margin-bottom: 15px; font-size: 24px; }
                p { color: #dfe6e9; margin-bottom: 25px; line-height: 1.6; }
                .btn {
                    display: inline-block;
                    background: linear-gradient(135deg, #6c5ce7, #a29bfe);
                    color: white;
                    padding: 15px 40px;
                    border-radius: 30px;
                    text-decoration: none;
                    font-weight: bold;
                    transition: transform 0.3s;
                }
                .btn:hover { transform: scale(1.05); }
                #status-msg { 
                    background: rgba(255,255,255,0.1);
                    padding: 15px;
                    border-radius: 10px;
                    margin-bottom: 20px;
                }
            </style>
            <link href="https://fonts.googleapis.com/css2?family=Tajawal:wght@400;700&display=swap" rel="stylesheet">
        </head>
        <body>
            <div class="container">
                <div class="spinner"></div>
                <h1>جاري التحقق من الدفع...</h1>
                <div id="status-msg">
                    <p>⏳ يتم التحقق من حالة العملية</p>
                </div>
                <p>سيصلك إشعار في البوت بالنتيجة</p>
                <a href="https://t.me/{{ bot_username }}" class="btn">العودة للبوت</a>
            </div>
            <script>
                // التحقق من الحالة بعد 3 ثواني
                setTimeout(function() {
                    var orderId = '{{ order_id }}';
                    if (orderId) {
                        // إعادة تحميل الصفحة للتحقق من Firebase
                        window.location.reload();
                    }
                }, 3000);
                
                // بعد 10 ثواني، توجيه للبوت
                setTimeout(function() {
                    document.getElementById('status-msg').innerHTML = '<p>✅ تم إرسال طلبك - تحقق من البوت</p>';
                }, 10000);
            </script>
        </body>
        </html>
        ''', bot_username=BOT_USERNAME, order_id=order_id)

# ============ صفحة الفاتورة للعميل ============
@app.route('/invoice/<invoice_id>')
def show_invoice(invoice_id):
    """عرض صفحة الفاتورة للعميل"""
    
    # البحث عن الفاتورة في الذاكرة
    invoice_data = merchant_invoices.get(invoice_id)
    
    # البحث في Firebase إذا لم توجد
    if not invoice_data:
        try:
            doc = db.collection('merchant_invoices').document(invoice_id).get()
            if doc.exists:
                invoice_data = doc.to_dict()
                merchant_invoices[invoice_id] = invoice_data
        except Exception as e:
            print(f"⚠️ خطأ في جلب الفاتورة: {e}")
    
    # إذا لم توجد الفاتورة
    if not invoice_data:
        return render_template_string('''
        <!DOCTYPE html>
        <html dir="rtl" lang="ar">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>فاتورة غير موجودة</title>
            <link href="https://fonts.googleapis.com/css2?family=Tajawal:wght@400;500;700&display=swap" rel="stylesheet">
            <style>
                * { box-sizing: border-box; margin: 0; padding: 0; }
                body { 
                    font-family: 'Tajawal', sans-serif; 
                    background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%); 
                    min-height: 100vh;
                    display: flex;
                    justify-content: center;
                    align-items: center;
                    padding: 20px;
                }
                .container {
                    background: rgba(255,255,255,0.1);
                    backdrop-filter: blur(10px);
                    border-radius: 20px;
                    padding: 40px;
                    text-align: center;
                    max-width: 400px;
                    border: 1px solid rgba(255,255,255,0.2);
                }
                .icon { font-size: 80px; margin-bottom: 20px; }
                h1 { color: #ff7675; margin-bottom: 15px; font-size: 24px; }
                p { color: #dfe6e9; line-height: 1.6; }
            </style>
        </head>
        <body>
            <div class="container">
                <div class="icon">❌</div>
                <h1>فاتورة غير موجودة</h1>
                <p>عذراً، لم يتم العثور على هذه الفاتورة أو أنها منتهية الصلاحية.</p>
            </div>
        </body>
        </html>
        '''), 404
    
    # التحقق من انتهاء صلاحية الفاتورة (ساعة واحدة)
    expires_at = invoice_data.get('expires_at', 0)
    current_time = time.time()
    
    # إذا انتهت صلاحية الفاتورة
    if expires_at > 0 and current_time > expires_at and invoice_data.get('status') != 'completed':
        # تحديث الحالة إلى منتهية
        try:
            invoice_data['status'] = 'expired'
            merchant_invoices[invoice_id] = invoice_data
            db.collection('merchant_invoices').document(invoice_id).update({'status': 'expired'})
        except:
            pass
        
        return render_template_string('''
        <!DOCTYPE html>
        <html dir="rtl" lang="ar">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>انتهت صلاحية الفاتورة</title>
            <link href="https://fonts.googleapis.com/css2?family=Tajawal:wght@400;500;700&display=swap" rel="stylesheet">
            <style>
                * { box-sizing: border-box; margin: 0; padding: 0; }
                body { 
                    font-family: 'Tajawal', sans-serif; 
                    background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%); 
                    min-height: 100vh;
                    display: flex;
                    justify-content: center;
                    align-items: center;
                    padding: 20px;
                }
                .container {
                    background: rgba(255,255,255,0.1);
                    backdrop-filter: blur(10px);
                    border-radius: 20px;
                    padding: 40px;
                    text-align: center;
                    max-width: 400px;
                    border: 1px solid rgba(255,255,255,0.2);
                }
                .icon { font-size: 80px; margin-bottom: 20px; }
                h1 { color: #fdcb6e; margin-bottom: 15px; font-size: 24px; }
                p { color: #dfe6e9; line-height: 1.8; }
                .invoice-info {
                    background: rgba(253,203,110,0.1);
                    border-radius: 10px;
                    padding: 15px;
                    margin-top: 20px;
                }
                .invoice-info div {
                    color: #b2bec3;
                    font-size: 14px;
                    margin: 5px 0;
                }
            </style>
        </head>
        <body>
            <div class="container">
                <div class="icon">⏰</div>
                <h1>انتهت صلاحية الفاتورة</h1>
                <p>عذراً، لم يتم الدفع خلال المدة المحددة (ساعة واحدة).<br>الرجاء التواصل مع التاجر للحصول على فاتورة جديدة.</p>
                <div class="invoice-info">
                    <div>رقم الفاتورة: <strong>{{ invoice_id }}</strong></div>
                    <div>المبلغ: <strong>{{ amount }} ريال</strong></div>
                </div>
            </div>
        </body>
        </html>
        ''', invoice_id=invoice_id, amount=invoice_data.get('amount', 0)), 410
    
    # إذا كانت الفاتورة مرفوضة أو فاشلة
    if invoice_data.get('status') in ['failed', 'declined']:
        return render_template_string('''
        <!DOCTYPE html>
        <html dir="rtl" lang="ar">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>الفاتورة مرفوضة</title>
            <link href="https://fonts.googleapis.com/css2?family=Tajawal:wght@400;500;700&display=swap" rel="stylesheet">
            <style>
                * { box-sizing: border-box; margin: 0; padding: 0; }
                body { 
                    font-family: 'Tajawal', sans-serif; 
                    background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%); 
                    min-height: 100vh;
                    display: flex;
                    justify-content: center;
                    align-items: center;
                    padding: 20px;
                }
                .container {
                    background: rgba(255,255,255,0.1);
                    backdrop-filter: blur(10px);
                    border-radius: 20px;
                    padding: 40px;
                    text-align: center;
                    max-width: 400px;
                    border: 1px solid rgba(255,255,255,0.2);
                }
                .icon { font-size: 80px; margin-bottom: 20px; }
                h1 { color: #ff7675; margin-bottom: 15px; font-size: 24px; }
                p { color: #dfe6e9; line-height: 1.8; }
                .invoice-info {
                    background: rgba(255,118,117,0.1);
                    border-radius: 10px;
                    padding: 15px;
                    margin-top: 20px;
                }
                .invoice-info div {
                    color: #b2bec3;
                    font-size: 14px;
                    margin: 5px 0;
                }
            </style>
        </head>
        <body>
            <div class="container">
                <div class="icon">❌</div>
                <h1>تم رفض الدفع</h1>
                <p>عذراً، فشلت عملية الدفع لهذه الفاتورة.<br>الرجاء التواصل مع التاجر للحصول على فاتورة جديدة.</p>
                <div class="invoice-info">
                    <div>رقم الفاتورة: <strong>{{ invoice_id }}</strong></div>
                    <div>المبلغ: <strong>{{ amount }} ريال</strong></div>
                </div>
            </div>
        </body>
        </html>
        ''', invoice_id=invoice_id, amount=invoice_data.get('amount', 0)), 410
    
    # إذا كانت الفاتورة مدفوعة مسبقاً
    if invoice_data.get('status') == 'completed':
        return render_template_string('''
        <!DOCTYPE html>
        <html dir="rtl" lang="ar">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>الفاتورة مدفوعة</title>
            <link href="https://fonts.googleapis.com/css2?family=Tajawal:wght@400;500;700&display=swap" rel="stylesheet">
            <style>
                * { box-sizing: border-box; margin: 0; padding: 0; }
                body { 
                    font-family: 'Tajawal', sans-serif; 
                    background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%); 
                    min-height: 100vh;
                    display: flex;
                    justify-content: center;
                    align-items: center;
                    padding: 20px;
                }
                .container {
                    background: rgba(255,255,255,0.1);
                    backdrop-filter: blur(10px);
                    border-radius: 20px;
                    padding: 40px;
                    text-align: center;
                    max-width: 400px;
                    border: 1px solid rgba(255,255,255,0.2);
                }
                .icon { font-size: 80px; margin-bottom: 20px; }
                h1 { color: #00cec9; margin-bottom: 15px; font-size: 24px; }
                p { color: #dfe6e9; line-height: 1.6; }
            </style>
        </head>
        <body>
            <div class="container">
                <div class="icon">✅</div>
                <h1>تم دفع الفاتورة</h1>
                <p>هذه الفاتورة تم دفعها مسبقاً.</p>
            </div>
        </body>
        </html>
        ''')
    
    # عرض صفحة الفاتورة
    merchant_name = invoice_data.get('merchant_name', 'التاجر')
    amount = invoice_data.get('amount', 0)
    
    # جلب وقت الانتهاء المحفوظ (إذا لم يوجد = الفاتورة قديمة، نحسب من created_at)
    expires_at_ts = invoice_data.get('expires_at')
    if not expires_at_ts:
        # فاتورة قديمة بدون expires_at - نحسب من وقت الإنشاء + ساعة
        created_at = invoice_data.get('created_at')
        if created_at:
            # إذا كان timestamp من Firebase
            if hasattr(created_at, 'timestamp'):
                expires_at_ts = created_at.timestamp() + 3600
            elif isinstance(created_at, (int, float)):
                expires_at_ts = created_at + 3600
            else:
                expires_at_ts = time.time()  # افتراضي = منتهية
        else:
            expires_at_ts = time.time()  # افتراضي = منتهية
    
    remaining_seconds = int(expires_at_ts - time.time())
    if remaining_seconds < 0:
        remaining_seconds = 0
    
    return render_template_string('''
    <!DOCTYPE html>
    <html dir="rtl" lang="ar">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>فاتورة - {{ merchant_name }}</title>
        <link href="https://fonts.googleapis.com/css2?family=Tajawal:wght@400;500;700&display=swap" rel="stylesheet">
        <style>
            * { box-sizing: border-box; margin: 0; padding: 0; }
            body { 
                font-family: 'Tajawal', sans-serif; 
                background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%); 
                min-height: 100vh;
                display: flex;
                justify-content: center;
                align-items: center;
                padding: 20px;
            }
            .invoice-card {
                background: rgba(255,255,255,0.1);
                backdrop-filter: blur(10px);
                border-radius: 20px;
                padding: 30px;
                width: 100%;
                max-width: 400px;
                border: 1px solid rgba(255,255,255,0.2);
            }
            .header {
                text-align: center;
                margin-bottom: 25px;
            }
            .merchant-icon {
                width: 70px;
                height: 70px;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                border-radius: 50%;
                display: flex;
                align-items: center;
                justify-content: center;
                margin: 0 auto 15px;
                font-size: 30px;
            }
            .merchant-name {
                color: #fff;
                font-size: 22px;
                font-weight: 700;
            }
            .invoice-id {
                color: #a29bfe;
                font-size: 12px;
                margin-top: 5px;
            }
            .amount-section {
                background: rgba(255,255,255,0.05);
                border-radius: 15px;
                padding: 20px;
                text-align: center;
                margin-bottom: 20px;
            }
            .timer-section {
                background: rgba(253,203,110,0.1);
                border: 1px solid rgba(253,203,110,0.3);
                border-radius: 12px;
                padding: 12px;
                text-align: center;
                margin-bottom: 20px;
            }
            .timer-label {
                color: #fdcb6e;
                font-size: 12px;
                margin-bottom: 5px;
            }
            .timer-value {
                color: #fdcb6e;
                font-size: 24px;
                font-weight: 700;
                font-family: monospace;
            }
            .timer-expired {
                color: #ff7675 !important;
            }
            .amount-label {
                color: #b2bec3;
                font-size: 14px;
                margin-bottom: 8px;
            }
            .amount-value {
                color: #00cec9;
                font-size: 36px;
                font-weight: 700;
            }
            .amount-currency {
                color: #81ecec;
                font-size: 18px;
                margin-right: 5px;
            }
            .form-group {
                margin-bottom: 20px;
            }
            .form-label {
                display: block;
                color: #dfe6e9;
                margin-bottom: 8px;
                font-size: 14px;
            }
            .form-input {
                width: 100%;
                padding: 15px;
                border: 2px solid rgba(255,255,255,0.1);
                border-radius: 12px;
                background: rgba(255,255,255,0.05);
                color: #fff;
                font-size: 18px;
                font-family: 'Tajawal', sans-serif;
                text-align: center;
                direction: ltr;
                transition: border-color 0.3s;
            }
            .form-input:focus {
                outline: none;
                border-color: #667eea;
            }
            .form-input::placeholder {
                color: #636e72;
            }
            .phone-wrapper {
                display: flex;
                gap: 10px;
                direction: ltr;
            }
            .country-select {
                width: 120px;
                padding: 15px 10px;
                border: 2px solid rgba(255,255,255,0.1);
                border-radius: 12px;
                background: rgba(255,255,255,0.05);
                color: #fff;
                font-size: 14px;
                font-family: 'Tajawal', sans-serif;
                cursor: pointer;
                transition: border-color 0.3s;
            }
            .country-select:focus {
                outline: none;
                border-color: #667eea;
            }
            .country-select option {
                background: #1a1a2e;
                color: #fff;
            }
            .phone-input-wrapper {
                flex: 1;
            }
            .phone-input-wrapper .form-input {
                width: 100%;
            }
            .pay-btn {
                width: 100%;
                padding: 16px;
                border: none;
                border-radius: 12px;
                background: linear-gradient(135deg, #00b894 0%, #00cec9 100%);
                color: #fff;
                font-size: 18px;
                font-weight: 700;
                font-family: 'Tajawal', sans-serif;
                cursor: pointer;
                transition: transform 0.2s, box-shadow 0.2s;
            }
            .pay-btn:hover {
                transform: translateY(-2px);
                box-shadow: 0 10px 30px rgba(0,206,201,0.3);
            }
            .pay-btn:disabled {
                background: #636e72;
                cursor: not-allowed;
                transform: none;
            }
            .secure-note {
                text-align: center;
                color: #636e72;
                font-size: 12px;
                margin-top: 20px;
            }
            .secure-note span {
                color: #00b894;
            }
            .error-msg {
                color: #ff7675;
                font-size: 13px;
                margin-top: 8px;
                display: none;
            }
            .loading {
                display: none;
            }
            .loading.show {
                display: inline-block;
            }
        </style>
    </head>
    <body>
        <div class="invoice-card">
            <div class="header">
                <div class="merchant-icon">🏪</div>
                <div class="merchant-name">{{ merchant_name }}</div>
                <div class="invoice-id">رقم الفاتورة: {{ invoice_id }}</div>
            </div>
            
            <div class="amount-section">
                <div class="amount-label">المبلغ المطلوب</div>
                <div class="amount-value">
                    {{ amount }}
                    <span class="amount-currency">ريال</span>
                </div>
            </div>
            
            <div class="timer-section">
                <div class="timer-label">⏰ الوقت المتبقي للدفع</div>
                <div class="timer-value" id="countdown">00:00:00</div>
            </div>
            
            <form id="paymentForm" action="/invoice/{{ invoice_id }}/pay" method="POST">
                <div class="form-group">
                    <label class="form-label">📱 رقم الجوال</label>
                    <div class="phone-wrapper">
                        <select name="country_code" id="countrySelect" class="country-select">
                            <option value="966" data-length="9">🇸🇦 +966</option>
                            <option value="971" data-length="9">🇦🇪 +971</option>
                            <option value="965" data-length="8">🇰🇼 +965</option>
                            <option value="973" data-length="8">🇧🇭 +973</option>
                            <option value="974" data-length="8">🇶🇦 +974</option>
                            <option value="968" data-length="8">🇴🇲 +968</option>
                            <option value="962" data-length="9">🇯🇴 +962</option>
                            <option value="20" data-length="10">🇪🇬 +20</option>
                            <option value="212" data-length="9">🇲🇦 +212</option>
                            <option value="216" data-length="8">🇹🇳 +216</option>
                            <option value="213" data-length="9">🇩🇿 +213</option>
                            <option value="218" data-length="9">🇱🇾 +218</option>
                            <option value="249" data-length="9">🇸🇩 +249</option>
                            <option value="964" data-length="10">🇮🇶 +964</option>
                            <option value="963" data-length="9">🇸🇾 +963</option>
                            <option value="961" data-length="8">🇱🇧 +961</option>
                            <option value="970" data-length="9">🇵🇸 +970</option>
                            <option value="967" data-length="9">🇾🇪 +967</option>
                            <option value="90" data-length="10">🇹🇷 +90</option>
                            <option value="44" data-length="10">🇬🇧 +44</option>
                            <option value="1" data-length="10">🇺🇸 +1</option>
                            <option value="33" data-length="9">🇫🇷 +33</option>
                            <option value="49" data-length="11">🇩🇪 +49</option>
                        </select>
                        <div class="phone-input-wrapper">
                            <input type="tel" name="phone" class="form-input" 
                                   placeholder="5xxxxxxxx" 
                                   maxlength="10"
                                   required
                                   id="phoneInput">
                        </div>
                    </div>
                    <input type="hidden" name="full_phone" id="fullPhone">
                    <div class="error-msg" id="phoneError">الرجاء إدخال رقم جوال صحيح</div>
                </div>
                
                <button type="submit" class="pay-btn" id="payBtn">
                    <span class="loading" id="loading">⏳ </span>
                    💳 ادفع الآن
                </button>
            </form>
            
            <div class="secure-note">
                🔒 <span>دفع آمن</span> عبر بوابة EdfaPay
            </div>
        </div>
        
        <script>
            const form = document.getElementById('paymentForm');
            const phoneInput = document.getElementById('phoneInput');
            const countrySelect = document.getElementById('countrySelect');
            const fullPhoneInput = document.getElementById('fullPhone');
            const phoneError = document.getElementById('phoneError');
            const payBtn = document.getElementById('payBtn');
            const loading = document.getElementById('loading');
            
            phoneInput.addEventListener('input', function() {
                phoneError.style.display = 'none';
                // إزالة الصفر من البداية تلقائياً
                if (this.value.startsWith('0')) {
                    this.value = this.value.substring(1);
                }
            });
            
            form.addEventListener('submit', function(e) {
                let phone = phoneInput.value.trim();
                const countryCode = countrySelect.value;
                
                // إزالة الصفر من البداية
                if (phone.startsWith('0')) {
                    phone = phone.substring(1);
                }
                
                // التحقق من أن الرقم أرقام فقط
                if (!/^[0-9]+$/.test(phone)) {
                    e.preventDefault();
                    phoneError.textContent = 'الرجاء إدخال أرقام فقط';
                    phoneError.style.display = 'block';
                    return;
                }
                
                // التحقق من طول الرقم
                if (phone.length < 7 || phone.length > 11) {
                    e.preventDefault();
                    phoneError.textContent = 'الرجاء إدخال رقم جوال صحيح';
                    phoneError.style.display = 'block';
                    return;
                }
                
                // دمج رمز الدولة مع الرقم
                fullPhoneInput.value = countryCode + phone;
                
                payBtn.disabled = true;
                loading.classList.add('show');
            });
            
            // العداد التنازلي
            let remainingSeconds = {{ remaining_seconds }};
            const countdownEl = document.getElementById('countdown');
            
            function updateCountdown() {
                if (remainingSeconds <= 0) {
                    countdownEl.textContent = 'انتهت الصلاحية';
                    countdownEl.classList.add('timer-expired');
                    payBtn.disabled = true;
                    payBtn.textContent = '⏰ انتهت صلاحية الفاتورة';
                    return;
                }
                
                const hours = Math.floor(remainingSeconds / 3600);
                const minutes = Math.floor((remainingSeconds % 3600) / 60);
                const seconds = remainingSeconds % 60;
                
                countdownEl.textContent = 
                    String(hours).padStart(2, '0') + ':' +
                    String(minutes).padStart(2, '0') + ':' +
                    String(seconds).padStart(2, '0');
                
                // تغيير اللون للأحمر إذا أقل من 5 دقائق
                if (remainingSeconds < 300) {
                    countdownEl.classList.add('timer-expired');
                }
                
                remainingSeconds--;
            }
            
            updateCountdown();
            setInterval(updateCountdown, 1000);
        </script>
    </body>
    </html>
    ''', merchant_name=merchant_name, amount=amount, invoice_id=invoice_id, remaining_seconds=remaining_seconds)

@app.route('/invoice/<invoice_id>/pay', methods=['POST'])
def process_invoice_payment(invoice_id):
    """معالجة دفع الفاتورة"""
    
    # جلب رقم الهاتف الكامل (مع رمز الدولة)
    phone = request.form.get('full_phone', '').strip()
    # إذا لم يوجد، استخدم الرقم العادي
    if not phone:
        phone = request.form.get('phone', '').strip()
    
    # البحث عن الفاتورة
    invoice_data = merchant_invoices.get(invoice_id)
    
    if not invoice_data:
        try:
            doc = db.collection('merchant_invoices').document(invoice_id).get()
            if doc.exists:
                invoice_data = doc.to_dict()
        except:
            pass
    
    if not invoice_data:
        return redirect(f'/invoice/{invoice_id}')
    
    # التحقق من انتهاء صلاحية الفاتورة
    expires_at = invoice_data.get('expires_at', 0)
    if expires_at > 0 and time.time() > expires_at:
        return redirect(f'/invoice/{invoice_id}')
    
    # التحقق من أن الفاتورة لم تدفع
    if invoice_data.get('status') == 'completed':
        return redirect(f'/invoice/{invoice_id}')
    
    # إنشاء طلب الدفع
    merchant_id = invoice_data.get('merchant_id')
    merchant_name = invoice_data.get('merchant_name')
    amount = invoice_data.get('amount')
    
    result = create_customer_invoice(merchant_id, merchant_name, amount, phone, invoice_id)
    
    if result['success']:
        # تحديث الفاتورة الأصلية
        try:
            merchant_invoices[invoice_id]['customer_phone'] = phone
            merchant_invoices[invoice_id]['order_id'] = result['order_id']
            
            db.collection('merchant_invoices').document(invoice_id).update({
                'customer_phone': phone,
                'order_id': result['order_id']
            })
        except:
            pass
        
        # إعادة توجيه لصفحة الدفع
        return redirect(result['payment_url'])
    else:
        # عرض رسالة خطأ
        return render_template_string('''
        <!DOCTYPE html>
        <html dir="rtl" lang="ar">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>خطأ</title>
            <link href="https://fonts.googleapis.com/css2?family=Tajawal:wght@400;500;700&display=swap" rel="stylesheet">
            <style>
                * { box-sizing: border-box; margin: 0; padding: 0; }
                body { 
                    font-family: 'Tajawal', sans-serif; 
                    background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%); 
                    min-height: 100vh;
                    display: flex;
                    justify-content: center;
                    align-items: center;
                    padding: 20px;
                }
                .container {
                    background: rgba(255,255,255,0.1);
                    backdrop-filter: blur(10px);
                    border-radius: 20px;
                    padding: 40px;
                    text-align: center;
                    max-width: 400px;
                    border: 1px solid rgba(255,255,255,0.2);
                }
                .icon { font-size: 80px; margin-bottom: 20px; }
                h1 { color: #ff7675; margin-bottom: 15px; font-size: 24px; }
                p { color: #dfe6e9; line-height: 1.6; margin-bottom: 20px; }
                .btn {
                    display: inline-block;
                    padding: 12px 30px;
                    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                    color: #fff;
                    text-decoration: none;
                    border-radius: 10px;
                    font-weight: 600;
                }
            </style>
        </head>
        <body>
            <div class="container">
                <div class="icon">⚠️</div>
                <h1>حدث خطأ</h1>
                <p>{{ error }}</p>
                <a href="/invoice/{{ invoice_id }}" class="btn">حاول مرة أخرى</a>
            </div>
        </body>
        </html>
        ''', error=result.get('error', 'خطأ غير معروف'), invoice_id=invoice_id)

@app.route('/payment/cancel')
def payment_cancel():
    """صفحة إلغاء الدفع"""
    return render_template_string('''
    <!DOCTYPE html>
    <html dir="rtl" lang="ar">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>تم إلغاء الدفع</title>
        <style>
            * { box-sizing: border-box; margin: 0; padding: 0; }
            body { 
                font-family: 'Tajawal', sans-serif; 
                background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%); 
                min-height: 100vh;
                display: flex;
                justify-content: center;
                align-items: center;
                padding: 20px;
            }
            .container {
                background: rgba(255,255,255,0.1);
                backdrop-filter: blur(10px);
                border-radius: 20px;
                padding: 40px;
                text-align: center;
                max-width: 400px;
                border: 1px solid rgba(255,255,255,0.2);
            }
            .icon { font-size: 80px; margin-bottom: 20px; }
            h1 { color: #ff7675; margin-bottom: 15px; font-size: 24px; }
            p { color: #dfe6e9; margin-bottom: 25px; line-height: 1.6; }
            .btn {
                display: inline-block;
                background: linear-gradient(135deg, #6c5ce7, #a29bfe);
                color: white;
                padding: 15px 40px;
                border-radius: 30px;
                text-decoration: none;
                font-weight: bold;
                transition: transform 0.3s;
            }
            .btn:hover { transform: scale(1.05); }
        </style>
        <link href="https://fonts.googleapis.com/css2?family=Tajawal:wght@400;700&display=swap" rel="stylesheet">
    </head>
    <body>
        <div class="container">
            <div class="icon">❌</div>
            <h1>تم إلغاء الدفع</h1>
            <p>تم إلغاء عملية الدفع.<br>يمكنك المحاولة مرة أخرى.</p>
            <a href="https://t.me/{{ bot_username }}" class="btn">العودة للبوت</a>
        </div>
    </body>
    </html>
    ''', bot_username=BOT_USERNAME)

# لاستقبال تحديثات تيليجرام (Webhook)
@app.route('/webhook', methods=['POST'])
def getMessage():
    try:
        json_string = request.get_data().decode('utf-8')
        print(f"📩 Webhook received: {json_string[:200]}...")
        print(f"🤖 BOT_ACTIVE: {BOT_ACTIVE}")
        
        update = telebot.types.Update.de_json(json_string)
        
        # طباعة تفاصيل التحديث
        if update.message:
            print(f"📝 رسالة نصية من: {update.message.from_user.id}")
            print(f"📝 النص: {update.message.text}")
        
        # ✅ معالجة ضغطات الأزرار (callback_query)
        if update.callback_query:
            print(f"🔘 ضغط زر من: {update.callback_query.from_user.id}")
            print(f"🔘 البيانات: {update.callback_query.data}")
        
        if BOT_ACTIVE:
            print(f"🔢 معالجات الرسائل: {len(bot.message_handlers)}")
            print(f"🔢 معالجات الأزرار: {len(bot.callback_query_handlers)}")
            
            bot.threaded = False
            
            try:
                bot.process_new_updates([update])
                print("✅ تم معالجة التحديث بنجاح")
            except Exception as proc_error:
                print(f"❌ خطأ في المعالجة: {proc_error}")
                import traceback
                traceback.print_exc()
        else:
            print("⚠️ البوت غير نشط!")
    except Exception as e:
        print(f"❌ خطأ في Webhook: {e}")
        import traceback
        traceback.print_exc()
    return "!", 200

@app.route("/set_webhook")
def set_webhook():
    webhook_url = SITE_URL + "/webhook"
    bot.remove_webhook()
    bot.set_webhook(url=webhook_url)
    return f"Webhook set to {webhook_url}", 200

# Health check endpoint for Render
@app.route('/health')
def health():
    return {'status': 'ok'}, 200

# مسار لرفع البيانات إلى Firebase (للمالك فقط)
@app.route('/migrate_to_firebase')
def migrate_to_firebase_route():
    # التحقق من أن المستخدم هو المالك (يمكنك إضافة password parameter)
    password = request.args.get('password', '')
    admin_password = os.environ.get('ADMIN_PASS', 'admin123')
    
    if password != admin_password:
        return {'status': 'error', 'message': 'غير مصرح'}, 403
    
    # تنفيذ الرفع
    success = migrate_data_to_firebase()
    
    if success:
        return {
            'status': 'success',
            'message': 'تم رفع البيانات بنجاح إلى Firebase',
            'data': {
                'products': len(marketplace_items),
                'users': len(users_wallets),
                'orders': len(active_orders),
                'keys': len(charge_keys)
            }
        }, 200
    else:
        return {'status': 'error', 'message': 'فشل رفع البيانات'}, 500

# صفحة تسجيل الدخول للوحة التحكم (HTML منفصل) - نظام الكود المؤقت

# لوحة التحكم للمالك (محدثة بنظام الكود المؤقت)
@app.route('/dashboard', methods=['GET'])
def dashboard():
    # إذا لم يكن مسجل دخول -> عرض صفحة الدخول بنظام الكود
    if not session.get('is_admin'):
        return render_template('login.html')
    
    # المستخدم مسجل دخول -> عرض لوحة التحكم

    # --- جلب الإحصائيات الحقيقية من Firebase ---
    try:
        # عدد المستخدمين
        users_ref = db.collection('users')
        total_users = len(list(users_ref.stream()))
        
        # مجموع الأرصدة (يحتاج لعمل Loop)
        total_balance = 0
        users_list = []
        for user in users_ref.stream():
            user_data = user.to_dict()
            balance = user_data.get('balance', 0)
            total_balance += balance
            users_list.append({
                'id': user.id,
                'name': user_data.get('name', user_data.get('telegram_name', 'مستخدم')),
                'balance': balance,
                'username': user_data.get('username', '')
            })

        # المنتجات
        products_ref = db.collection('products')
        all_products = list(products_ref.stream())
        total_products = len(all_products)
        
        # حساب المباع والمتاح
        sold_products = 0
        available_products = 0
        for p in all_products:
            p_data = p.to_dict()
            if p_data.get('sold'):
                sold_products += 1
            else:
                available_products += 1
                
        # الطلبات (Orders)
        orders_ref = db.collection('orders')
        recent_orders_docs = orders_ref.order_by('created_at', direction=firestore.Query.DESCENDING).limit(20).stream()
        recent_orders = []
        total_revenue = 0
        for doc in recent_orders_docs:
            data = doc.to_dict()
            price = data.get('price', 0)
            total_revenue += price
            recent_orders.append({
                'id': doc.id[:8],
                'item_name': data.get('item_name', 'منتج'),
                'price': price,
                'buyer_name': data.get('buyer_name', 'مشتري'),
                'buyer_id': data.get('buyer_id', ''),
                'created_at': data.get('created_at', '')
            })
        
        # إجمالي الطلبات
        total_orders = len(list(orders_ref.stream()))

        # المفاتيح
        keys_ref = db.collection('charge_keys')
        all_keys_docs = list(keys_ref.stream())
        charge_keys_display = []
        active_keys = 0
        used_keys = 0
        
        for k in all_keys_docs:
            data = k.to_dict()
            is_used = data.get('used', False)
            if is_used:
                used_keys += 1
            else:
                active_keys += 1
            charge_keys_display.append({
                'code': k.id,
                'amount': data.get('amount', 0),
                'used': is_used,
                'used_by': data.get('used_by', '')
            })
        
        # ===== الفواتير (الجديد) =====
        invoices_ref = db.collection('merchant_invoices')
        all_invoices = list(invoices_ref.order_by('created_at', direction=firestore.Query.DESCENDING).limit(50).stream())
        invoices_list = []
        total_invoice_revenue = 0
        pending_invoices = 0
        completed_invoices = 0
        
        for inv in all_invoices:
            inv_data = inv.to_dict()
            status = inv_data.get('status', 'pending')
            amount = inv_data.get('amount', 0)
            expires_at = inv_data.get('expires_at', 0)
            
            # التحقق من انتهاء الصلاحية (أكثر من ساعة)
            if status == 'pending' and expires_at > 0 and time.time() > expires_at:
                status = 'expired'  # اعتبرها مرفوضة
            
            if status == 'completed':
                completed_invoices += 1
                total_invoice_revenue += amount
            else:
                pending_invoices += 1
            
            invoices_list.append({
                'id': inv.id,
                'merchant_id': inv_data.get('merchant_id', ''),
                'merchant_name': inv_data.get('merchant_name', 'تاجر'),
                'amount': amount,
                'customer_phone': inv_data.get('customer_phone', 'غير محدد'),
                'status': status,
                'created_at': inv_data.get('created_at', '')
            })
        
        # ===== المدفوعات (pending_payments) =====
        payments_ref = db.collection('pending_payments')
        all_payments = list(payments_ref.order_by('created_at', direction=firestore.Query.DESCENDING).limit(50).stream())
        payments_list = []
        
        for pay in all_payments:
            pay_data = pay.to_dict()
            payments_list.append({
                'order_id': pay.id,
                'user_id': pay_data.get('user_id', ''),
                'amount': pay_data.get('amount', 0),
                'status': pay_data.get('status', 'pending'),
                'is_invoice': pay_data.get('is_merchant_invoice', False),
                'invoice_id': pay_data.get('invoice_id', ''),
                'created_at': pay_data.get('created_at', '')
            })
        
        # ===== إحصائيات السلة =====
        active_carts = len(user_carts)
        cart_stats_ref = db.collection('cart_stats')
        cart_stats = list(cart_stats_ref.order_by('add_to_cart_count', direction=firestore.Query.DESCENDING).limit(10).stream())
        top_cart_products = []
        total_add_to_cart = 0
        total_cart_purchases = 0
        
        for stat in cart_stats:
            stat_data = stat.to_dict()
            add_count = stat_data.get('add_to_cart_count', 0)
            purchase_count = stat_data.get('purchase_count', 0)
            total_add_to_cart += add_count
            total_cart_purchases += purchase_count
            
            # جلب اسم المنتج
            try:
                prod_doc = db.collection('products').document(stat.id).get()
                prod_name = prod_doc.to_dict().get('item_name', 'منتج') if prod_doc.exists else 'محذوف'
            except:
                prod_name = 'غير معروف'
            
            top_cart_products.append({
                'product_id': stat.id,
                'name': prod_name,
                'add_count': add_count,
                'purchase_count': purchase_count
            })
        
        # معدل إتمام الشراء
        conversion_rate = (total_cart_purchases / total_add_to_cart * 100) if total_add_to_cart > 0 else 0

    except Exception as e:
        print(f"Error loading stats from Firebase: {e}")
        import traceback
        traceback.print_exc()
        # قيم افتراضية عند الخطأ
        total_users = 0
        total_balance = 0
        total_products = 0
        available_products = 0
        sold_products = 0
        total_orders = 0
        total_revenue = 0
        recent_orders = []
        users_list = []
        active_keys = 0
        used_keys = 0
        charge_keys_display = []
        invoices_list = []
        payments_list = []
        total_invoice_revenue = 0
        pending_invoices = 0
        completed_invoices = 0
        active_carts = 0
        top_cart_products = []
        conversion_rate = 0
    
    return f"""
    <!DOCTYPE html>
    <html dir="rtl">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>لوحة التحكم - المالك</title>
        <link href="https://fonts.googleapis.com/css2?family=Tajawal:wght@400;500;700&display=swap" rel="stylesheet">
        <style>
            * {{ margin: 0; padding: 0; box-sizing: border-box; }}
            body {{
                font-family: 'Tajawal', 'Segoe UI', sans-serif;
                background: linear-gradient(135deg, #0f0c29 0%, #302b63 50%, #24243e 100%);
                min-height: 100vh;
                padding: 20px;
                color: #fff;
            }}
            .container {{
                max-width: 1600px;
                margin: 0 auto;
            }}
            .header {{
                background: rgba(255,255,255,0.1);
                backdrop-filter: blur(10px);
                padding: 20px 30px;
                border-radius: 15px;
                margin-bottom: 20px;
                display: flex;
                justify-content: space-between;
                align-items: center;
                flex-wrap: wrap;
                gap: 15px;
                border: 1px solid rgba(255,255,255,0.1);
            }}
            .header h1 {{ color: #fff; font-size: 26px; }}
            .header-btns {{ display: flex; gap: 10px; flex-wrap: wrap; }}
            .btn {{
                padding: 10px 20px;
                border: none;
                border-radius: 8px;
                cursor: pointer;
                font-weight: bold;
                font-family: inherit;
                text-decoration: none;
                display: inline-block;
                font-size: 14px;
            }}
            .btn-success {{ background: linear-gradient(135deg, #00b894, #55efc4); color: #000; }}
            .btn-danger {{ background: linear-gradient(135deg, #e74c3c, #c0392b); color: #fff; }}
            .btn-primary {{ background: linear-gradient(135deg, #667eea, #764ba2); color: #fff; }}
            .btn-info {{ background: linear-gradient(135deg, #00cec9, #81ecec); color: #000; }}
            
            .stats-grid {{
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
                gap: 15px;
                margin-bottom: 25px;
            }}
            .stat-card {{
                background: rgba(255,255,255,0.1);
                backdrop-filter: blur(10px);
                padding: 20px;
                border-radius: 15px;
                text-align: center;
                border: 1px solid rgba(255,255,255,0.1);
                transition: transform 0.3s;
            }}
            .stat-card:hover {{ transform: translateY(-5px); }}
            .stat-card .icon {{ font-size: 36px; margin-bottom: 10px; }}
            .stat-card .value {{ font-size: 28px; font-weight: bold; color: #00cec9; }}
            .stat-card .label {{ color: #b2bec3; margin-top: 5px; font-size: 14px; }}
            .stat-card .label {{ color: #888; margin-top: 5px; }}
            .section {{
                background: rgba(255,255,255,0.1);
                backdrop-filter: blur(10px);
                padding: 25px;
                border-radius: 15px;
                margin-bottom: 20px;
                border: 1px solid rgba(255,255,255,0.1);
            }}
            .section h2 {{ 
                color: #fff; 
                margin-bottom: 20px; 
                border-bottom: 2px solid rgba(255,255,255,0.2); 
                padding-bottom: 10px;
                display: flex;
                justify-content: space-between;
                align-items: center;
            }}
            .section h2 .count {{
                background: #667eea;
                padding: 5px 15px;
                border-radius: 20px;
                font-size: 14px;
            }}
            .tabs {{
                display: flex;
                gap: 10px;
                margin-bottom: 20px;
                flex-wrap: wrap;
            }}
            .tab {{
                padding: 10px 20px;
                background: rgba(255,255,255,0.1);
                border: none;
                border-radius: 8px;
                color: #fff;
                cursor: pointer;
                font-family: inherit;
                transition: all 0.3s;
            }}
            .tab:hover, .tab.active {{
                background: linear-gradient(135deg, #667eea, #764ba2);
            }}
            table {{
                width: 100%;
                border-collapse: collapse;
            }}
            th, td {{
                padding: 12px;
                text-align: right;
                border-bottom: 1px solid rgba(255,255,255,0.1);
            }}
            th {{
                background: rgba(102, 126, 234, 0.3);
                color: #fff;
                font-weight: bold;
            }}
            tr:hover {{ background: rgba(255,255,255,0.05); }}
            .badge {{
                display: inline-block;
                padding: 5px 12px;
                border-radius: 15px;
                font-size: 12px;
                font-weight: bold;
            }}
            .badge-success {{ background: #00b894; color: white; }}
            .badge-danger {{ background: #e74c3c; color: white; }}
            .badge-warning {{ background: #fdcb6e; color: #333; }}
            .badge-info {{ background: #74b9ff; color: white; }}
            .badge-pending {{ background: #f39c12; color: white; }}
            
            .search-box {{
                display: flex;
                gap: 10px;
                margin-bottom: 15px;
            }}
            .search-box input {{
                flex: 1;
                padding: 12px 15px;
                border: 2px solid rgba(255,255,255,0.2);
                border-radius: 8px;
                background: rgba(255,255,255,0.1);
                color: #fff;
                font-family: inherit;
            }}
            .search-box input::placeholder {{ color: #888; }}
            .search-box input:focus {{ outline: none; border-color: #667eea; }}
            
            .bot-commands {{
                background: rgba(102, 126, 234, 0.2);
                border: 1px solid rgba(102, 126, 234, 0.3);
                border-radius: 12px;
                padding: 20px;
            }}
            .bot-commands h3 {{ color: #fff; margin-bottom: 15px; }}
            .command-item {{
                background: rgba(255,255,255,0.1);
                padding: 12px 15px;
                border-radius: 8px;
                margin-bottom: 10px;
                display: flex;
                justify-content: space-between;
                align-items: center;
                border-right: 4px solid #667eea;
            }}
            .command-item code {{
                background: rgba(102, 126, 234, 0.3);
                padding: 5px 10px;
                border-radius: 5px;
                font-family: monospace;
                color: #81ecec;
            }}
            .command-item span {{ color: #b2bec3; font-size: 14px; }}
            
            .hidden {{ display: none; }}
            
            @media (max-width: 768px) {{
                .stats-grid {{ grid-template-columns: repeat(2, 1fr); }}
                .stat-card .value {{ font-size: 22px; }}
                table {{ font-size: 13px; }}
                th, td {{ padding: 8px 5px; }}
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>🎛️ لوحة التحكم</h1>
                <div class="header-btns">
                    <a href="/admin/products" class="btn btn-success">🏪 المنتجات</a>
                    <a href="/admin/categories" class="btn btn-info">🏷️ الأقسام</a>
                    <button class="btn btn-primary" onclick="location.reload()">🔄 تحديث</button>
                    <a href="/logout_admin" class="btn btn-danger">🚪 خروج</a>
                </div>
            </div>
            
            <div class="stats-grid">
                <div class="stat-card">
                    <div class="icon">👥</div>
                    <div class="value">{total_users}</div>
                    <div class="label">المستخدمين</div>
                </div>
                <div class="stat-card">
                    <div class="icon">📦</div>
                    <div class="value">{available_products}</div>
                    <div class="label">منتجات متاحة</div>
                </div>
                <div class="stat-card">
                    <div class="icon">🛒</div>
                    <div class="value">{active_carts}</div>
                    <div class="label">سلات نشطة</div>
                </div>
                <div class="stat-card">
                    <div class="icon">📊</div>
                    <div class="value">{conversion_rate:.1f}%</div>
                    <div class="label">معدل الإتمام</div>
                </div>
                <div class="stat-card">
                    <div class="icon">🧾</div>
                    <div class="value">{completed_invoices}</div>
                    <div class="label">فواتير مكتملة</div>
                </div>
                <div class="stat-card">
                    <div class="icon">💳</div>
                    <div class="value">{total_invoice_revenue:.0f}</div>
                    <div class="label">إيرادات الفواتير</div>
                </div>
                <div class="stat-card">
                    <div class="icon">💰</div>
                    <div class="value">{total_balance:.0f}</div>
                    <div class="label">إجمالي الأرصدة</div>
                </div>
                <div class="stat-card">
                    <div class="icon">✅</div>
                    <div class="value">{sold_products}</div>
                    <div class="label">مباعة</div>
                </div>
            </div>
            
            <!-- ===== قسم إحصائيات السلة ===== -->
            <div class="section">
                <h2>🛒 أكثر المنتجات إضافة للسلة <span class="count">{len(top_cart_products)}</span></h2>
                <div style="overflow-x: auto;">
                <table>
                    <thead>
                        <tr>
                            <th>#</th>
                            <th>المنتج</th>
                            <th>إضافات للسلة</th>
                            <th>مشتريات</th>
                            <th>معدل التحويل</th>
                        </tr>
                    </thead>
                    <tbody>
                        {''.join([f'''
                        <tr>
                            <td>{i+1}</td>
                            <td>{p['name']}</td>
                            <td><span style="color:#a29bfe">{p['add_count']}</span></td>
                            <td><span style="color:#00b894">{p['purchase_count']}</span></td>
                            <td><span style="color:#f1c40f">{(p['purchase_count']/p['add_count']*100 if p['add_count'] > 0 else 0):.1f}%</span></td>
                        </tr>
                        ''' for i, p in enumerate(top_cart_products)]) if top_cart_products else '<tr><td colspan="5" style="text-align:center;color:#888">لا توجد بيانات بعد</td></tr>'}
                    </tbody>
                </table>
                </div>
            </div>
            
            <!-- ===== قسم الفواتير ===== -->
            <div class="section">
                <h2>🧾 الفواتير <span class="count">{len(invoices_list)}</span></h2>
                <div class="search-box">
                    <input type="text" id="invoiceSearch" placeholder="🔍 بحث برقم الفاتورة أو رقم العميل..." onkeyup="searchTable('invoiceSearch', 'invoicesTable')">
                </div>
                <div style="overflow-x: auto;">
                <table id="invoicesTable">
                    <thead>
                        <tr>
                            <th>رقم الفاتورة</th>
                            <th>التاجر</th>
                            <th>المبلغ</th>
                            <th>رقم العميل</th>
                            <th>الحالة</th>
                        </tr>
                    </thead>
                    <tbody>
                        {''.join([f"""
                        <tr>
                            <td><code>{inv['id']}</code></td>
                            <td>{inv['merchant_name']} <small style="color:#888">({inv['merchant_id']})</small></td>
                            <td style="color:#00cec9; font-weight:bold;">{inv['amount']} ريال</td>
                            <td dir="ltr">{inv['customer_phone']}</td>
                            <td><span class="badge {'badge-success' if inv['status'] == 'completed' else 'badge-danger' if inv['status'] in ['expired', 'failed', 'declined'] else 'badge-pending'}">{'مكتمل' if inv['status'] == 'completed' else 'مرفوضة' if inv['status'] in ['expired', 'failed', 'declined'] else 'معلق'}</span></td>
                        </tr>
                        """ for inv in invoices_list]) if invoices_list else '<tr><td colspan="5" style="text-align:center; color:#888;">لا توجد فواتير</td></tr>'}
                    </tbody>
                </table>
                </div>
            </div>
            
            <!-- ===== قسم المدفوعات ===== -->
            <div class="section">
                <h2>💳 المدفوعات <span class="count">{len(payments_list)}</span></h2>
                <div style="overflow-x: auto;">
                <table>
                    <thead>
                        <tr>
                            <th>رقم الطلب</th>
                            <th>المستخدم</th>
                            <th>المبلغ</th>
                            <th>النوع</th>
                            <th>الحالة</th>
                        </tr>
                    </thead>
                    <tbody>
                        {''.join([f"""
                        <tr>
                            <td><code>{pay['order_id'][:15]}...</code></td>
                            <td>{pay['user_id']}</td>
                            <td style="color:#00cec9; font-weight:bold;">{pay['amount']} ريال</td>
                            <td><span class="badge {'badge-info' if pay['is_invoice'] else 'badge-warning'}">{'فاتورة' if pay['is_invoice'] else 'شحن'}</span></td>
                            <td><span class="badge {'badge-success' if pay['status'] == 'completed' else 'badge-danger' if pay['status'] == 'failed' else 'badge-pending'}">{'مكتمل' if pay['status'] == 'completed' else 'فشل' if pay['status'] == 'failed' else 'معلق'}</span></td>
                        </tr>
                        """ for pay in payments_list]) if payments_list else '<tr><td colspan="5" style="text-align:center; color:#888;">لا توجد مدفوعات</td></tr>'}
                    </tbody>
                </table>
                </div>
            </div>
            
            <!-- ===== قسم المستخدمين ===== -->
            <div class="section">
                <h2>👥 المستخدمين <span class="count">{len(users_list)}</span></h2>
                <div class="search-box">
                    <input type="text" id="userSearch" placeholder="🔍 بحث بالآيدي..." onkeyup="searchTable('userSearch', 'usersTable')">
                </div>
                <div style="overflow-x: auto;">
                <table id="usersTable">
                    <thead>
                        <tr>
                            <th>آيدي المستخدم</th>
                            <th>الاسم</th>
                            <th>الرصيد</th>
                        </tr>
                    </thead>
                    <tbody>
                        {''.join([f'''
                        <tr>
                            <td><code>{user['id']}</code></td>
                            <td>{user['name']}</td>
                            <td style="color:#00cec9; font-weight:bold;">{user['balance']:.2f} ريال</td>
                        </tr>
                        ''' for user in users_list]) if users_list else '<tr><td colspan="3" style="text-align:center; color:#888;">لا يوجد مستخدمين</td></tr>'}
                    </tbody>
                </table>
                </div>
            </div>
            
            <!-- ===== أوامر البوت ===== -->
            <div class="section">
                <h2>🤖 أوامر البوت</h2>
                <div class="bot-commands">
                    <div class="command-item">
                        <code>/فاتورة</code>
                        <span>إنشاء فاتورة جديدة</span>
                    </div>
                    <div class="command-item">
                        <code>/add ID AMOUNT</code>
                        <span>شحن رصيد مستخدم</span>
                    </div>
                    <div class="command-item">
                        <code>/توليد 50 10</code>
                        <span>توليد 10 مفاتيح بقيمة 50 ريال</span>
                    </div>
                    <div class="command-item">
                        <code>/المفاتيح</code>
                        <span>عرض إحصائيات المفاتيح</span>
                    </div>
                    <div class="command-item">
                        <code>/add_product</code>
                        <span>إضافة منتج جديد</span>
                    </div>
                </div>
            </div>
            
            <div class="section">
                <h2>� المفاتيح <span class="count">{len(charge_keys_display)}</span></h2>
                <div style="overflow-x: auto;">
                <table>
                    <thead>
                        <tr>
                            <th>المفتاح</th>
                            <th>القيمة</th>
                            <th>الحالة</th>
                            <th>مستخدم بواسطة</th>
                        </tr>
                    </thead>
                    <tbody>
                        {''.join([f"""
                        <tr>
                            <td><code>{key['code']}</code></td>
                            <td style="color:#00cec9;">{key['amount']} ريال</td>
                            <td><span class="badge {'badge-success' if not key['used'] else 'badge-danger'}">{'نشط' if not key['used'] else 'مستخدم'}</span></td>
                            <td>{key['used_by'] if key['used'] else '-'}</td>
                        </tr>
                        """ for key in charge_keys_display[:30]]) if charge_keys_display else '<tr><td colspan="4" style="text-align:center; color:#888;">لا توجد مفاتيح</td></tr>'}
                    </tbody>
                </table>
                </div>
            </div>
        </div>
        
        <script>
            // دالة البحث في الجداول
            function searchTable(inputId, tableId) {{
                const input = document.getElementById(inputId);
                const filter = input.value.toLowerCase();
                const table = document.getElementById(tableId);
                const rows = table.getElementsByTagName('tr');
                
                for (let i = 1; i < rows.length; i++) {{
                    const cells = rows[i].getElementsByTagName('td');
                    let found = false;
                    for (let j = 0; j < cells.length; j++) {{
                        if (cells[j].textContent.toLowerCase().includes(filter)) {{
                            found = true;
                            break;
                        }}
                    }}
                    rows[i].style.display = found ? '' : 'none';
                }}
            }}
            
            // تحديث تلقائي كل 60 ثانية
            setTimeout(() => location.reload(), 60000);
        </script>
    </body>
    </html>
    """

# API لشحن رصيد من لوحة التحكم (للأدمن فقط)
@app.route('/api/add_balance', methods=['POST'])
def api_add_balance():
    # ===== التحقق من صلاحية الأدمن =====
    if not session.get('is_admin'):
        return {'status': 'error', 'message': 'غير مصرح!'}
    
    data = request.json
    user_id = str(data.get('user_id'))
    amount = float(data.get('amount'))
    
    if not user_id or amount <= 0:
        return {'status': 'error', 'message': 'بيانات غير صحيحة'}
    
    add_balance(user_id, amount)
    
    # إشعار المستخدم
    try:
        bot.send_message(int(user_id), f"🎉 تم شحن رصيدك بمبلغ {amount} ريال!")
    except:
        pass
    
    return {'status': 'success'}

# --- API لإضافة منتج (مصحح للحفظ في Firebase) ---
@app.route('/api/add_product', methods=['POST'])
def api_add_product():
    # ===== التحقق من صلاحية الأدمن =====
    if not session.get('is_admin'):
        return {'status': 'error', 'message': 'غير مصرح!'}
    
    try:
        data = request.json
        name = data.get('name')
        price = data.get('price')
        category = data.get('category')
        details = data.get('details', '')
        image = data.get('image', '')
        hidden_data = data.get('hidden_data')
        
        # التحقق من البيانات
        if not name or not price or not hidden_data:
            return {'status': 'error', 'message': 'بيانات غير كاملة'}
        
        # إنشاء بيانات المنتج
        new_id = str(uuid.uuid4())
        item = {
            'id': new_id,
            'item_name': name,
            'price': float(price),
            'seller_id': str(ADMIN_ID),
            'seller_name': 'المالك',
            'hidden_data': hidden_data,
            'category': category,
            'details': details,
            'image_url': image,
            'sold': False,
            'created_at': firestore.SERVER_TIMESTAMP
        }
        
        # 1. الحفظ في Firebase (المهم)
        db.collection('products').document(new_id).set(item)
        print(f"✅ تم حفظ المنتج {new_id} في Firestore: {name}")
        
        # 2. تحديث الذاكرة المحلية (للعرض السريع)
        marketplace_items.append(item)
        print(f"✅ تم إضافة المنتج للذاكرة. إجمالي المنتجات: {len(marketplace_items)}")
        
        # 3. إشعار المالك (داخل try/except لضمان عدم توقف العملية)
        try:
            bot.send_message(
                ADMIN_ID,
                f"✅ **تم إضافة منتج جديد**\n📦 {name}\n💰 {price} ريال",
                parse_mode="Markdown"
            )
        except Exception as e:
            print(f"فشل إرسال الإشعار: {e}")
            
        return {'status': 'success', 'message': 'تم الحفظ في قاعدة البيانات'}

    except Exception as e:
        print(f"Error in add_product: {e}")
        return {'status': 'error', 'message': f'حدث خطأ في السيرفر: {str(e)}'}

# --- API لتوليد المفاتيح (مصحح للحفظ في Firebase) ---
@app.route('/api/generate_keys', methods=['POST'])
def api_generate_keys():
    # ===== التحقق من صلاحية الأدمن =====
    if not session.get('is_admin'):
        return {'status': 'error', 'message': 'غير مصرح!'}
    
    try:
        data = request.json
        amount = float(data.get('amount'))
        count = int(data.get('count', 1))
        
        if amount <= 0 or count <= 0 or count > 100:
            return {'status': 'error', 'message': 'أرقام غير صحيحة'}
        
        generated_keys = []
        batch = db.batch() # استخدام الدفعات للحفظ السريع
        
        for _ in range(count):
            # إنشاء كود عشوائي
            key_code = f"KEY-{random.randint(10000, 99999)}-{random.randint(1000, 9999)}"
            
            key_data = {
                'amount': amount,
                'used': False,
                'used_by': None,
                'created_at': firestore.SERVER_TIMESTAMP
            }
            
            # تجهيز الحفظ في Firebase
            doc_ref = db.collection('charge_keys').document(key_code)
            batch.set(doc_ref, key_data)
            
            # تحديث الذاكرة
            charge_keys[key_code] = key_data
            generated_keys.append(key_code)
            
        # تنفيذ الحفظ في Firebase دفعة واحدة
        batch.commit()
        
        return {'status': 'success', 'keys': generated_keys}

    except Exception as e:
        print(f"Error generating keys: {e}")
        return {'status': 'error', 'message': f'فشل التوليد: {str(e)}'}

# ==================== نظام الكود المؤقت للدخول ====================

# API لإرسال كود التحقق
@app.route('/api/admin/send_code', methods=['POST'])
@limiter.limit("3 per minute")  # 🔒 Rate Limiting: منع تخمين كلمة مرور الأدمن
def api_send_admin_code():
    global admin_login_codes, failed_login_attempts
    
    try:
        data = request.json
        password = data.get('password', '')
        client_ip = request.headers.get('X-Forwarded-For', request.remote_addr)
        
        # التحقق من الحظر بسبب محاولات فاشلة
        if client_ip in failed_login_attempts:
            attempt_data = failed_login_attempts[client_ip]
            if attempt_data.get('blocked_until', 0) > time.time():
                remaining = int(attempt_data['blocked_until'] - time.time())
                return jsonify({
                    'status': 'error',
                    'message': f'⛔ تم حظرك مؤقتاً. حاول بعد {remaining} ثانية'
                })
        
        # التحقق من كلمة المرور
        admin_password = os.environ.get('ADMIN_PASS', 'admin123')
        
        if password != admin_password:
            # تسجيل المحاولة الفاشلة
            if client_ip not in failed_login_attempts:
                failed_login_attempts[client_ip] = {'count': 0, 'blocked_until': 0}
            
            failed_login_attempts[client_ip]['count'] += 1
            attempts_left = 5 - failed_login_attempts[client_ip]['count']
            
            # حظر بعد 5 محاولات
            if failed_login_attempts[client_ip]['count'] >= 5:
                failed_login_attempts[client_ip]['blocked_until'] = time.time() + 900  # 15 دقيقة
                
                # إرسال تنبيه أمني للمالك
                try:
                    alert_msg = f"""
⚠️ *تنبيه أمني!*

محاولات دخول فاشلة متعددة للوحة التحكم!

🌐 *IP:* `{client_ip}`
⏰ *الوقت:* {time.strftime('%Y-%m-%d %H:%M:%S')}
🔒 *الحالة:* تم الحظر لمدة 15 دقيقة
                    """
                    if BOT_ACTIVE:
                        bot.send_message(ADMIN_ID, alert_msg, parse_mode='Markdown')
                except Exception as e:
                    print(f"Failed to send security alert: {e}")
                
                return jsonify({
                    'status': 'error',
                    'message': '⛔ تم حظرك لمدة 15 دقيقة بسبب محاولات فاشلة متكررة'
                })
            
            return jsonify({
                'status': 'error',
                'message': f'❌ كلمة مرور خاطئة! المحاولات المتبقية: {attempts_left}'
            })
        
        # كلمة المرور صحيحة - توليد كود عشوائي
        code = str(random.randint(100000, 999999))
        
        # حفظ الكود مع وقت الانتهاء (3 دقائق)
        admin_login_codes = {
            'code': code,
            'created_at': time.time(),
            'expires_at': time.time() + 180,  # 3 دقائق
            'used': False,
            'ip': client_ip
        }
        
        # إرسال الكود للمالك عبر البوت
        try:
            if BOT_ACTIVE:
                code_msg = f"""
🔐 *طلب دخول للوحة التحكم*

📍 *الكود:* `{code}`
⏰ *صالح لمدة:* 3 دقائق
🌐 *IP:* `{client_ip}`
⏱️ *الوقت:* {time.strftime('%Y-%m-%d %H:%M:%S')}

⚠️ *إذا لم تكن أنت، تجاهل هذا الكود!*
                """
                bot.send_message(ADMIN_ID, code_msg, parse_mode='Markdown')
                
                # مسح المحاولات الفاشلة عند النجاح
                if client_ip in failed_login_attempts:
                    del failed_login_attempts[client_ip]
                
                return jsonify({'status': 'success', 'message': 'تم إرسال الكود'})
            else:
                return jsonify({
                    'status': 'error',
                    'message': '❌ البوت غير متصل! لا يمكن إرسال الكود'
                })
        except Exception as e:
            print(f"Error sending code: {e}")
            return jsonify({
                'status': 'error',
                'message': '❌ فشل إرسال الكود للبوت'
            })
            
    except Exception as e:
        print(f"Error in send_code: {e}")
        return jsonify({'status': 'error', 'message': 'خطأ في السيرفر'})

# API للتحقق من الكود
@app.route('/api/admin/verify_code', methods=['POST'])
def api_verify_admin_code():
    global admin_login_codes
    
    try:
        data = request.json
        code = data.get('code', '').strip()
        client_ip = request.headers.get('X-Forwarded-For', request.remote_addr)
        
        # التحقق من وجود كود نشط
        if not admin_login_codes or not admin_login_codes.get('code'):
            return jsonify({
                'status': 'error',
                'message': '❌ لا يوجد كود نشط. اطلب كود جديد'
            })
        
        # التحقق من انتهاء الصلاحية
        if time.time() > admin_login_codes.get('expires_at', 0):
            admin_login_codes = {}  # مسح الكود المنتهي
            return jsonify({
                'status': 'error',
                'message': '⏰ انتهت صلاحية الكود! اطلب كود جديد'
            })
        
        # التحقق من استخدام الكود مسبقاً
        if admin_login_codes.get('used'):
            return jsonify({
                'status': 'error',
                'message': '❌ تم استخدام هذا الكود مسبقاً'
            })
        
        # التحقق من صحة الكود
        if code != admin_login_codes.get('code'):
            return jsonify({
                'status': 'error',
                'message': '❌ كود خاطئ!'
            })
        
        # الكود صحيح - تسجيل الدخول
        admin_login_codes['used'] = True
        session['is_admin'] = True
        
        # إرسال إشعار بنجاح الدخول
        try:
            if BOT_ACTIVE:
                success_msg = f"""
✅ *تم تسجيل الدخول بنجاح!*

🌐 *IP:* `{client_ip}`
⏰ *الوقت:* {time.strftime('%Y-%m-%d %H:%M:%S')}
                """
                bot.send_message(ADMIN_ID, success_msg, parse_mode='Markdown')
        except:
            pass
        
        # مسح الكود
        admin_login_codes = {}
        
        return jsonify({'status': 'success', 'message': 'تم التحقق بنجاح'})
        
    except Exception as e:
        print(f"Error in verify_code: {e}")
        return jsonify({'status': 'error', 'message': 'خطأ في السيرفر'})

# مسار لتسجيل خروج الآدمن
@app.route('/logout_admin')
def logout_admin():
    session.pop('is_admin', None)
    return redirect('/dashboard')

# ==================== صفحة إدارة المنتجات للمالك ====================


# صفحة إدارة الأقسام (للمالك فقط)

# صفحة إدارة المنتجات (للمالك فقط)
@app.route('/admin/products')
def admin_products():
    # التحقق من تسجيل الدخول كمالك
    if not session.get('is_admin'):
        return redirect('/dashboard')
    
    return render_template('admin_products.html', admin_id=ADMIN_ID)

# صفحة إدارة الأقسام (للمالك فقط)
@app.route('/admin/categories')
def admin_categories():
    # التحقق من تسجيل الدخول كمالك
    if not session.get('is_admin'):
        return redirect('/dashboard')
    
    return render_template('admin_categories.html')

# API لجلب جميع المنتجات (للمالك)
@app.route('/api/admin/get_products')
def api_get_products():
    # التحقق من الصلاحية
    if not session.get('is_admin'):
        return jsonify({'status': 'error', 'message': 'غير مصرح'})
    
    try:
        available = []
        sold = []
        
        if db:
            # جلب جميع المنتجات من Firebase
            products_ref = db.collection('products')
            
            # المنتجات المتاحة
            available_query = query_where(products_ref, 'sold', '==', False)
            for doc in available_query.stream():
                data = doc.to_dict()
                data['id'] = doc.id
                available.append(data)
            
            # المنتجات المباعة
            sold_query = query_where(products_ref, 'sold', '==', True)
            for doc in sold_query.stream():
                data = doc.to_dict()
                data['id'] = doc.id
                sold.append(data)
        else:
            # من الذاكرة
            for item in marketplace_items:
                if item.get('sold'):
                    sold.append(item)
                else:
                    available.append(item)
        
        return jsonify({
            'status': 'success',
            'available': available,
            'sold': sold
        })
        
    except Exception as e:
        logger.error(f"Error getting products: {e}")
        return jsonify({'status': 'error', 'message': 'حدث خطأ، حاول لاحقاً'})

# API لإضافة منتج جديد (للمالك)
@app.route('/api/admin/add_product_new', methods=['POST'])
def api_add_product_new():
    # التحقق من الصلاحية
    if not session.get('is_admin'):
        return jsonify({'status': 'error', 'message': 'غير مصرح'})
    
    try:
        data = request.json
        name = data.get('name', '').strip()
        price = float(data.get('price', 0))
        category = data.get('category', '').strip()
        details = data.get('details', '').strip()
        hidden_data = data.get('hidden_data', '').strip()
        buyer_instructions = data.get('buyer_instructions', '').strip()
        image = data.get('image', '').strip()
        delivery_type = data.get('delivery_type', 'instant').strip()
        
        # التحقق من نوع التسليم
        if delivery_type not in ['instant', 'manual']:
            delivery_type = 'instant'
        
        # التحقق من البيانات الأساسية
        if not name or price <= 0 or not category:
            return jsonify({'status': 'error', 'message': 'بيانات ناقصة (الاسم، السعر، الفئة)'})
        
        # التحقق حسب نوع التسليم
        if delivery_type == 'instant' and not hidden_data:
            return jsonify({'status': 'error', 'message': 'البيانات السرية مطلوبة للتسليم الفوري'})
        
        if delivery_type == 'manual' and not buyer_instructions:
            return jsonify({'status': 'error', 'message': 'يجب تحديد ما تحتاجه من المشتري'})
        
        # إنشاء المنتج
        product_id = str(uuid.uuid4())
        product_data = {
            'id': product_id,
            'item_name': name,
            'price': price,
            'category': category,
            'details': details,
            'hidden_data': hidden_data,
            'buyer_instructions': buyer_instructions,
            'image_url': image,
            'seller_id': ADMIN_ID,
            'seller_name': 'المتجر الرسمي',
            'delivery_type': delivery_type,
            'sold': False,
            'created_at': time.time()
        }
        
        # حفظ في Firebase
        if db:
            db.collection('products').document(product_id).set(product_data)
            print(f"✅ تم حفظ المنتج في Firebase: {name} (التسليم: {delivery_type})")
        
        # إضافة للذاكرة
        marketplace_items.append(product_data)
        
        return jsonify({'status': 'success', 'product_id': product_id})
        
    except Exception as e:
        logger.error(f"Error adding product: {e}")
        return jsonify({'status': 'error', 'message': 'حدث خطأ، حاول لاحقاً'})

# API لحذف منتج (للمالك)
@app.route('/api/admin/delete_product', methods=['POST'])
def api_delete_product():
    # التحقق من الصلاحية
    if not session.get('is_admin'):
        return jsonify({'status': 'error', 'message': 'غير مصرح'})
    
    try:
        data = request.json
        product_id = data.get('product_id')
        
        if not product_id:
            return jsonify({'status': 'error', 'message': 'معرف المنتج مطلوب'})
        
        # حذف من Firebase
        if db:
            db.collection('products').document(product_id).delete()
            print(f"✅ تم حذف المنتج من Firebase: {product_id}")
        
        # حذف من الذاكرة
        global marketplace_items
        marketplace_items = [item for item in marketplace_items if item.get('id') != product_id]
        
        return jsonify({'status': 'success'})
        
    except Exception as e:
        logger.error(f"Error deleting product: {e}")
        return jsonify({'status': 'error', 'message': 'حدث خطأ، حاول لاحقاً'})

# ============ إدارة الأقسام ============

# API لجلب الأقسام
@app.route('/api/admin/get_categories', methods=['GET'])
def api_get_categories():
    """جلب قائمة الأقسام"""
    # ✅ التحقق من صلاحية الأدمن
    if not session.get('is_admin'):
        return jsonify({'status': 'error', 'message': 'غير مصرح'})
    
    try:
        # حساب عدد المنتجات لكل قسم
        category_counts = {}
        for item in marketplace_items:
            cat = item.get('category', '')
            if cat:
                category_counts[cat] = category_counts.get(cat, 0) + 1
        
        # إضافة عدد المنتجات لكل قسم
        result = []
        for cat in categories_list:
            cat_data = cat.copy()
            cat_data['product_count'] = category_counts.get(cat['name'], 0)
            result.append(cat_data)
        
        return jsonify({'status': 'success', 'categories': result})
    except Exception as e:
        logger.error(f"Error getting categories: {e}")
        return jsonify({'status': 'error', 'message': 'حدث خطأ، حاول لاحقاً'})

# API لإضافة قسم جديد
@app.route('/api/admin/add_category', methods=['POST'])
def api_add_category():
    """إضافة قسم جديد"""
    if not session.get('is_admin'):
        return jsonify({'status': 'error', 'message': 'غير مصرح'})
    
    try:
        data = request.json
        name = data.get('name', '').strip()
        image_url = data.get('image_url', '').strip()
        delivery_type = data.get('delivery_type', 'instant').strip()
        
        if delivery_type not in ['instant', 'manual']:
            delivery_type = 'instant'
        
        if not name:
            return jsonify({'status': 'error', 'message': 'اسم القسم مطلوب'})
        
        # التحقق من عدم تكرار الاسم
        for cat in categories_list:
            if cat['name'] == name:
                return jsonify({'status': 'error', 'message': 'هذا القسم موجود مسبقاً'})
        
        # إنشاء القسم الجديد
        import uuid
        cat_id = str(uuid.uuid4())[:8]
        new_order = len(categories_list) + 1
        
        new_category = {
            'id': cat_id,
            'name': name,
            'image_url': image_url or 'https://via.placeholder.com/100?text=' + name,
            'order': new_order,
            'delivery_type': delivery_type,
            'created_at': time.time()
        }
        
        # حفظ في Firebase
        if db:
            db.collection('categories').document(cat_id).set(new_category)
            print(f"✅ تم حفظ القسم في Firebase: {name} ({delivery_type})")
        
        # إضافة للذاكرة
        categories_list.append(new_category)
        
        return jsonify({'status': 'success', 'category': new_category})
        
    except Exception as e:
        logger.error(f"Error adding category: {e}")
        return jsonify({'status': 'error', 'message': 'حدث خطأ، حاول لاحقاً'})

# API لتعديل قسم
@app.route('/api/admin/update_category', methods=['POST'])
def api_update_category():
    """تعديل قسم موجود"""
    if not session.get('is_admin'):
        return jsonify({'status': 'error', 'message': 'غير مصرح'})
    
    try:
        data = request.json
        cat_id = data.get('id')
        new_name = data.get('name', '').strip()
        new_image = data.get('image_url', '').strip()
        new_delivery_type = data.get('delivery_type', '').strip()
        
        if not cat_id:
            return jsonify({'status': 'error', 'message': 'معرف القسم مطلوب'})
        
        # البحث عن القسم
        cat_found = None
        old_name = None
        for cat in categories_list:
            if cat['id'] == cat_id:
                cat_found = cat
                old_name = cat['name']
                break
        
        if not cat_found:
            return jsonify({'status': 'error', 'message': 'القسم غير موجود'})
        
        # تحديث القسم
        if new_name:
            cat_found['name'] = new_name
        if new_image:
            cat_found['image_url'] = new_image
        if new_delivery_type in ['instant', 'manual']:
            cat_found['delivery_type'] = new_delivery_type
        
        # تحديث في Firebase
        if db:
            db.collection('categories').document(cat_id).update({
                'name': cat_found['name'],
                'image_url': cat_found['image_url'],
                'delivery_type': cat_found.get('delivery_type', 'instant')
            })
        
        # تحديث اسم القسم في المنتجات إذا تغير
        if old_name and new_name and old_name != new_name:
            for item in marketplace_items:
                if item.get('category') == old_name:
                    item['category'] = new_name
                    # تحديث في Firebase أيضاً
                    if db and item.get('id'):
                        try:
                            db.collection('products').document(item['id']).update({'category': new_name})
                        except:
                            pass
        
        return jsonify({'status': 'success', 'category': cat_found})
        
    except Exception as e:
        logger.error(f"Error updating category: {e}")
        return jsonify({'status': 'error', 'message': 'حدث خطأ، حاول لاحقاً'})

# API لحذف قسم
@app.route('/api/admin/delete_category', methods=['POST'])
def api_delete_category():
    """حذف قسم"""
    if not session.get('is_admin'):
        return jsonify({'status': 'error', 'message': 'غير مصرح'})
    
    try:
        global categories_list
        data = request.json
        cat_id = data.get('id')
        
        if not cat_id:
            return jsonify({'status': 'error', 'message': 'معرف القسم مطلوب'})
        
        # البحث عن القسم
        cat_found = None
        for cat in categories_list:
            if cat['id'] == cat_id:
                cat_found = cat
                break
        
        if not cat_found:
            return jsonify({'status': 'error', 'message': 'القسم غير موجود'})
        
        # التحقق من عدم وجود منتجات في القسم
        product_count = 0
        for item in marketplace_items:
            if item.get('category') == cat_found['name']:
                product_count += 1
        
        if product_count > 0:
            return jsonify({
                'status': 'error', 
                'message': f'لا يمكن حذف القسم - يوجد {product_count} منتج فيه'
            })
        
        # حذف من Firebase
        if db:
            db.collection('categories').document(cat_id).delete()
            print(f"✅ تم حذف القسم من Firebase: {cat_found['name']}")
        
        # حذف من الذاكرة
        categories_list = [c for c in categories_list if c['id'] != cat_id]
        
        return jsonify({'status': 'success'})
        
    except Exception as e:
        logger.error(f"Error deleting category: {e}")
        return jsonify({'status': 'error', 'message': 'حدث خطأ، حاول لاحقاً'})

# API لإعادة ترتيب الأقسام
@app.route('/api/admin/reorder_categories', methods=['POST'])
def api_reorder_categories():
    """إعادة ترتيب الأقسام"""
    if not session.get('is_admin'):
        return jsonify({'status': 'error', 'message': 'غير مصرح'})
    
    try:
        data = request.json
        new_order = data.get('order', [])  # قائمة بمعرفات الأقسام بالترتيب الجديد
        
        if not new_order:
            return jsonify({'status': 'error', 'message': 'الترتيب مطلوب'})
        
        # تحديث الترتيب
        for idx, cat_id in enumerate(new_order):
            for cat in categories_list:
                if cat['id'] == cat_id:
                    cat['order'] = idx + 1
                    # تحديث في Firebase
                    if db:
                        try:
                            db.collection('categories').document(cat_id).update({'order': idx + 1})
                        except:
                            pass
                    break
        
        # إعادة ترتيب القائمة
        categories_list.sort(key=lambda x: x.get('order', 999))
        
        return jsonify({'status': 'success'})
        
    except Exception as e:
        logger.error(f"Error reordering categories: {e}")
        return jsonify({'status': 'error', 'message': 'حدث خطأ، حاول لاحقاً'})

# API لجلب الأقسام للعرض العام (بدون تسجيل دخول)
@app.route('/api/categories', methods=['GET'])
def api_public_categories():
    """جلب الأقسام للعرض في الموقع"""
    try:
        result = []
        for cat in categories_list:
            result.append({
                'name': cat['name'],
                'image_url': cat.get('image_url', ''),
                'delivery_type': cat.get('delivery_type', 'instant')
            })
        return jsonify({
            'status': 'success', 
            'categories': result,
            'columns': display_settings.get('categories_columns', 3)
        })
    except Exception as e:
        logger.error(f"Error in public categories: {e}")
        return jsonify({'status': 'error', 'message': 'حدث خطأ، حاول لاحقاً'})

# API لجلب إعدادات العرض
@app.route('/api/admin/get_display_settings', methods=['GET'])
def api_get_display_settings():
    """جلب إعدادات العرض"""
    # ✅ التحقق من صلاحية الأدمن
    if not session.get('is_admin'):
        return jsonify({'status': 'error', 'message': 'غير مصرح'})
    
    return jsonify({
        'status': 'success',
        'categories_columns': display_settings.get('categories_columns', 3)
    })

# API لتعديل إعدادات العرض
@app.route('/api/admin/set_display_settings', methods=['POST'])
def api_set_display_settings():
    """تعديل إعدادات العرض"""
    if not session.get('is_admin'):
        return jsonify({'status': 'error', 'message': 'غير مصرح'})
    
    try:
        data = request.json
        cols = data.get('categories_columns')
        
        if cols and cols in [2, 3, 4]:
            display_settings['categories_columns'] = cols
            
            # حفظ في Firebase
            if db:
                db.collection('settings').document('display').set({
                    'categories_columns': cols
                }, merge=True)
            
            return jsonify({'status': 'success'})
        else:
            return jsonify({'status': 'error', 'message': 'قيمة غير صالحة'})
            
    except Exception as e:
        logger.error(f"Error setting display settings: {e}")
        return jsonify({'status': 'error', 'message': 'حدث خطأ، حاول لاحقاً'})

# تحميل البيانات من Firebase عند بدء التشغيل (يعمل مع Gunicorn وlocal)
print("🚀 بدء تشغيل التطبيق...")
load_all_data_from_firebase()

# التأكد من أن جميع المنتجات لديها UUID
ensure_product_ids()

if __name__ == "__main__":
    # هذا السطر يجعل البوت يعمل على المنفذ الصحيح في ريندر أو 10000 في جهازك
    port = int(os.environ.get("PORT", 10000))
    print(f"✅ التطبيق يعمل على المنفذ {port}")
    app.run(host="0.0.0.0", port=port)
