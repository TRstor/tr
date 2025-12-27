#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
أدوات Firebase
===============
جميع دوال التعامل مع قاعدة بيانات Firebase
"""

import os
import json
import time
import uuid
import firebase_admin
from firebase_admin import credentials, firestore

# === متغيرات عامة ===
db = None
USE_FIELD_FILTER = False

# محاولة استيراد FieldFilter للنسخ الجديدة
try:
    from google.cloud.firestore_v1.base_query import FieldFilter
    USE_FIELD_FILTER = True
except ImportError:
    USE_FIELD_FILTER = False

# === تهيئة Firebase ===
def init_firebase():
    """تهيئة اتصال Firebase"""
    global db
    
    firebase_credentials_json = os.environ.get("FIREBASE_CREDENTIALS")
    
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
        return db
    except Exception as e:
        print(f"⚠️ Firebase غير متاح: {e}")
        print("⚠️ سيتم العمل بدون قاعدة بيانات Firebase (في الذاكرة فقط)")
        db = None
        return None

# === دالة Query متوافقة ===
def query_where(collection_ref, field, op, value):
    """استخدام where بطريقة متوافقة مع جميع النسخ"""
    if USE_FIELD_FILTER:
        return collection_ref.where(filter=FieldFilter(field, op, value))
    else:
        return collection_ref.where(field, op, value)

# === دوال الرصيد ===
def get_balance(user_id):
    """جلب رصيد المستخدم من Firebase"""
    try:
        if not db:
            return 0.0
        uid = str(user_id)
        doc = db.collection('users').document(uid).get()
        if doc.exists:
            return doc.to_dict().get('balance', 0.0)
        return 0.0
    except Exception as e:
        print(f"⚠️ خطأ في جلب الرصيد: {e}")
        return 0.0

def add_balance(user_id, amount, users_wallets=None):
    """إضافة رصيد للمستخدم في Firebase والذاكرة"""
    uid = str(user_id)
    
    # تحديث الذاكرة إذا تم تمريرها
    if users_wallets is not None:
        if uid not in users_wallets:
            users_wallets[uid] = 0.0
        users_wallets[uid] += float(amount)
    
    # جلب الرصيد الحالي من Firebase
    current_balance = get_balance(uid)
    new_balance = current_balance + float(amount)
    
    # حفظ في Firebase
    try:
        if db:
            db.collection('users').document(uid).set({
                'balance': new_balance,
                'telegram_id': uid,
                'updated_at': firestore.SERVER_TIMESTAMP
            }, merge=True)
            print(f"✅ تم حفظ رصيد المستخدم {uid}: {new_balance} ريال في Firestore")
            return new_balance
    except Exception as e:
        print(f"❌ خطأ في حفظ الرصيد إلى Firebase: {e}")
    
    return new_balance

def deduct_balance(user_id, amount, users_wallets=None):
    """خصم رصيد من المستخدم"""
    uid = str(user_id)
    
    # تحديث الذاكرة إذا تم تمريرها
    if users_wallets is not None:
        if uid in users_wallets:
            users_wallets[uid] -= float(amount)
    
    # جلب الرصيد الحالي من Firebase
    current_balance = get_balance(uid)
    new_balance = current_balance - float(amount)
    
    # حفظ في Firebase
    try:
        if db:
            db.collection('users').document(uid).set({
                'balance': new_balance,
                'telegram_id': uid,
                'updated_at': firestore.SERVER_TIMESTAMP
            }, merge=True)
            print(f"✅ تم خصم {amount} ريال من المستخدم {uid}. الرصيد الجديد: {new_balance}")
            return new_balance
    except Exception as e:
        print(f"❌ خطأ في خصم الرصيد: {e}")
    
    return new_balance

# === دوال المنتجات ===
def get_products(sold=False):
    """جلب المنتجات من Firebase"""
    try:
        if not db:
            return []
        products_ref = query_where(db.collection('products'), 'sold', '==', sold)
        products = []
        for doc in products_ref.stream():
            data = doc.to_dict()
            data['id'] = doc.id
            products.append(data)
        return products
    except Exception as e:
        print(f"⚠️ خطأ في جلب المنتجات: {e}")
        return []

def get_product_by_id(product_id):
    """جلب منتج بالـ ID"""
    try:
        if not db:
            return None
        doc = db.collection('products').document(product_id).get()
        if doc.exists:
            data = doc.to_dict()
            data['id'] = doc.id
            return data
        return None
    except Exception as e:
        print(f"⚠️ خطأ في جلب المنتج: {e}")
        return None

def add_product(product_data):
    """إضافة منتج جديد"""
    try:
        if not db:
            return None
        product_id = str(uuid.uuid4())
        product_data['created_at'] = firestore.SERVER_TIMESTAMP
        product_data['sold'] = False
        db.collection('products').document(product_id).set(product_data)
        return product_id
    except Exception as e:
        print(f"❌ خطأ في إضافة المنتج: {e}")
        return None

def update_product(product_id, data):
    """تحديث منتج"""
    try:
        if not db:
            return False
        db.collection('products').document(product_id).update(data)
        return True
    except Exception as e:
        print(f"❌ خطأ في تحديث المنتج: {e}")
        return False

def mark_product_sold(product_id, buyer_id, buyer_name):
    """تعليم المنتج كمباع"""
    try:
        if not db:
            return False
        db.collection('products').document(product_id).update({
            'sold': True,
            'buyer_id': str(buyer_id),
            'buyer_name': buyer_name,
            'sold_at': firestore.SERVER_TIMESTAMP
        })
        return True
    except Exception as e:
        print(f"❌ خطأ في تعليم المنتج كمباع: {e}")
        return False

# === دوال الأقسام ===
def get_categories():
    """جلب الأقسام من Firebase"""
    try:
        if not db:
            return []
        categories = []
        for doc in db.collection('categories').order_by('order').stream():
            data = doc.to_dict()
            data['id'] = doc.id
            categories.append(data)
        return categories
    except Exception as e:
        print(f"⚠️ خطأ في جلب الأقسام: {e}")
        return []

def add_category(name, image_url='', delivery_type='instant', order=999):
    """إضافة قسم جديد"""
    try:
        if not db:
            return None
        cat_id = str(uuid.uuid4())
        db.collection('categories').document(cat_id).set({
            'name': name,
            'image_url': image_url,
            'delivery_type': delivery_type,
            'order': order,
            'created_at': firestore.SERVER_TIMESTAMP
        })
        return cat_id
    except Exception as e:
        print(f"❌ خطأ في إضافة القسم: {e}")
        return None

# === دوال مفاتيح الشحن ===
def get_charge_key(key_code):
    """جلب مفتاح شحن"""
    try:
        if not db:
            return None
        doc = db.collection('charge_keys').document(key_code).get()
        if doc.exists:
            return doc.to_dict()
        return None
    except Exception as e:
        print(f"⚠️ خطأ في جلب مفتاح الشحن: {e}")
        return None

def use_charge_key(key_code, user_id):
    """استخدام مفتاح شحن"""
    try:
        if not db:
            return False
        db.collection('charge_keys').document(key_code).update({
            'used': True,
            'used_by': str(user_id),
            'used_at': firestore.SERVER_TIMESTAMP
        })
        return True
    except Exception as e:
        print(f"❌ خطأ في استخدام مفتاح الشحن: {e}")
        return False

def create_charge_key(key_code, amount):
    """إنشاء مفتاح شحن جديد"""
    try:
        if not db:
            return False
        db.collection('charge_keys').document(key_code).set({
            'amount': float(amount),
            'used': False,
            'created_at': firestore.SERVER_TIMESTAMP
        })
        return True
    except Exception as e:
        print(f"❌ خطأ في إنشاء مفتاح الشحن: {e}")
        return False

# === دوال الطلبات المعلقة (الدفع) ===
def save_pending_payment(order_id, data):
    """حفظ طلب دفع معلق"""
    try:
        if not db:
            return False
        data['created_at'] = firestore.SERVER_TIMESTAMP
        db.collection('pending_payments').document(order_id).set(data)
        return True
    except Exception as e:
        print(f"❌ خطأ في حفظ الطلب المعلق: {e}")
        return False

def get_pending_payment(order_id):
    """جلب طلب دفع معلق"""
    try:
        if not db:
            return None
        doc = db.collection('pending_payments').document(order_id).get()
        if doc.exists:
            return doc.to_dict()
        return None
    except Exception as e:
        print(f"⚠️ خطأ في جلب الطلب المعلق: {e}")
        return None

def update_pending_payment(order_id, data):
    """تحديث طلب دفع معلق"""
    try:
        if not db:
            return False
        db.collection('pending_payments').document(order_id).update(data)
        return True
    except Exception as e:
        print(f"❌ خطأ في تحديث الطلب المعلق: {e}")
        return False

# === دوال السلة ===
def get_user_cart(user_id):
    """جلب سلة المستخدم"""
    try:
        if not db:
            return None
        doc = db.collection('carts').document(str(user_id)).get()
        if doc.exists:
            return doc.to_dict()
        return None
    except Exception as e:
        print(f"⚠️ خطأ في جلب السلة: {e}")
        return None

def save_user_cart(user_id, cart_data):
    """حفظ سلة المستخدم"""
    try:
        if not db:
            return False
        db.collection('carts').document(str(user_id)).set(cart_data, merge=True)
        return True
    except Exception as e:
        print(f"❌ خطأ في حفظ السلة: {e}")
        return False

def clear_user_cart(user_id):
    """مسح سلة المستخدم"""
    try:
        if not db:
            return False
        db.collection('carts').document(str(user_id)).delete()
        return True
    except Exception as e:
        print(f"❌ خطأ في مسح السلة: {e}")
        return False

# === دوال سجل الشحن ===
def add_charge_history(user_id, amount, method='key', order_id=''):
    """إضافة سجل شحن"""
    try:
        if not db:
            return False
        db.collection('charge_history').add({
            'user_id': str(user_id),
            'amount': float(amount),
            'method': method,
            'order_id': order_id,
            'created_at': firestore.SERVER_TIMESTAMP
        })
        return True
    except Exception as e:
        print(f"❌ خطأ في إضافة سجل الشحن: {e}")
        return False

# === دوال سجل المشتريات ===
def add_purchase_history(buyer_id, seller_id, product_data, order_id=''):
    """إضافة سجل شراء"""
    try:
        if not db:
            return False
        db.collection('purchases').add({
            'buyer_id': str(buyer_id),
            'seller_id': str(seller_id),
            'item_name': product_data.get('item_name', ''),
            'price': float(product_data.get('price', 0)),
            'category': product_data.get('category', ''),
            'order_id': order_id,
            'created_at': firestore.SERVER_TIMESTAMP
        })
        return True
    except Exception as e:
        print(f"❌ خطأ في إضافة سجل الشراء: {e}")
        return False

# === دالة تحميل جميع البيانات ===
def load_all_data():
    """تحميل جميع البيانات من Firebase"""
    data = {
        'products': [],
        'users': {},
        'categories': [],
        'charge_keys': {},
        'carts': {},
        'pending_payments': {}
    }
    
    if not db:
        print("⚠️ Firebase غير متاح")
        return data
    
    try:
        print("📥 جاري تحميل البيانات من Firebase...")
        
        # المنتجات
        data['products'] = get_products(sold=False)
        print(f"  ✅ {len(data['products'])} منتج")
        
        # المستخدمين
        for doc in db.collection('users').stream():
            data['users'][doc.id] = doc.to_dict().get('balance', 0.0)
        print(f"  ✅ {len(data['users'])} مستخدم")
        
        # الأقسام
        data['categories'] = get_categories()
        print(f"  ✅ {len(data['categories'])} قسم")
        
        # مفاتيح الشحن
        keys_ref = query_where(db.collection('charge_keys'), 'used', '==', False)
        for doc in keys_ref.stream():
            data['charge_keys'][doc.id] = doc.to_dict()
        print(f"  ✅ {len(data['charge_keys'])} مفتاح شحن")
        
        # السلات
        for doc in db.collection('carts').stream():
            data['carts'][doc.id] = doc.to_dict()
        print(f"  ✅ {len(data['carts'])} سلة")
        
        # الطلبات المعلقة
        pending_ref = query_where(db.collection('pending_payments'), 'status', '==', 'pending')
        for doc in pending_ref.stream():
            data['pending_payments'][doc.id] = doc.to_dict()
        print(f"  ✅ {len(data['pending_payments'])} طلب معلق")
        
        print("🎉 تم تحميل جميع البيانات!")
        
    except Exception as e:
        print(f"❌ خطأ في تحميل البيانات: {e}")
    
    return data

# تهيئة Firebase عند الاستيراد
db = init_firebase()
