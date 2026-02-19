#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
أدوات Firebase - التعامل مع قاعدة البيانات Firestore
"""

import json
import os
import firebase_admin
from firebase_admin import credentials, firestore

from config import FIREBASE_CREDENTIALS

# === تهيئة Firebase ===
def init_firebase():
    """تهيئة اتصال Firebase"""
    try:
        if FIREBASE_CREDENTIALS:
            cred_dict = json.loads(FIREBASE_CREDENTIALS)
            cred = credentials.Certificate(cred_dict)
        else:
            print("❌ يرجى تعيين FIREBASE_CREDENTIALS في متغيرات البيئة على Render")
            return None
        
        firebase_admin.initialize_app(cred)
        db = firestore.client()
        print("✅ تم الاتصال بـ Firebase بنجاح")
        return db
    except Exception as e:
        print(f"❌ خطأ في الاتصال بـ Firebase: {e}")
        return None

db = init_firebase()

# ============================
# === دوال العمليات ===
# ============================

def add_operation(user_id, title, details=""):
    """إضافة عملية جديدة"""
    doc_ref = db.collection("operations").document()
    doc_ref.set({
        "user_id": user_id,
        "title": title,
        "details": details,
        "created_at": firestore.SERVER_TIMESTAMP
    })
    return doc_ref.id

def get_operations(user_id):
    """جلب جميع عمليات المستخدم"""
    ops = db.collection("operations") \
        .where("user_id", "==", user_id) \
        .order_by("created_at") \
        .stream()
    return [{"id": doc.id, **doc.to_dict()} for doc in ops]

def get_operation_by_id(op_id):
    """جلب عملية بالمعرّف"""
    doc = db.collection("operations").document(op_id).get()
    if doc.exists:
        return {"id": doc.id, **doc.to_dict()}
    return None

def delete_operation(op_id):
    """حذف عملية"""
    db.collection("operations").document(op_id).delete()

# ============================
# === دوال الإيميلات ===
# ============================

def add_email(user_id, email, max_clients=5):
    """إضافة إيميل أساسي"""
    doc_ref = db.collection("emails").document()
    doc_ref.set({
        "user_id": user_id,
        "email": email,
        "max_clients": max_clients,
        "created_at": firestore.SERVER_TIMESTAMP
    })
    return doc_ref.id

def get_emails(user_id):
    """جلب جميع الإيميلات"""
    docs = db.collection("emails") \
        .where("user_id", "==", user_id) \
        .order_by("created_at") \
        .stream()
    return [{"id": doc.id, **doc.to_dict()} for doc in docs]

def get_email_by_id(email_id):
    """جلب إيميل بالمعرّف"""
    doc = db.collection("emails").document(email_id).get()
    if doc.exists:
        return {"id": doc.id, **doc.to_dict()}
    return None

def delete_email(email_id):
    """حذف إيميل وجميع عملائه"""
    # حذف العملاء أولاً
    clients = db.collection("emails").document(email_id) \
        .collection("clients").stream()
    for c in clients:
        c.reference.delete()
    # حذف الإيميل
    db.collection("emails").document(email_id).delete()

# ============================
# === دوال العملاء ===
# ============================

def add_client(email_id, name, phone, start_date, end_date):
    """إضافة عميل تحت إيميل معين"""
    doc_ref = db.collection("emails").document(email_id) \
        .collection("clients").document()
    doc_ref.set({
        "name": name,
        "phone": phone,
        "start_date": start_date,
        "end_date": end_date,
        "created_at": firestore.SERVER_TIMESTAMP
    })
    return doc_ref.id

def get_clients(email_id):
    """جلب عملاء إيميل معين"""
    docs = db.collection("emails").document(email_id) \
        .collection("clients").stream()
    return [{"id": doc.id, **doc.to_dict()} for doc in docs]

def get_client_by_id(email_id, client_id):
    """جلب عميل بالمعرّف"""
    doc = db.collection("emails").document(email_id) \
        .collection("clients").document(client_id).get()
    if doc.exists:
        return {"id": doc.id, **doc.to_dict()}
    return None

def delete_client(email_id, client_id):
    """حذف عميل"""
    db.collection("emails").document(email_id) \
        .collection("clients").document(client_id).delete()

def update_client(email_id, client_id, data):
    """تحديث بيانات عميل"""
    db.collection("emails").document(email_id) \
        .collection("clients").document(client_id).update(data)

def count_clients(email_id):
    """عدد العملاء في إيميل"""
    docs = db.collection("emails").document(email_id) \
        .collection("clients").stream()
    return sum(1 for _ in docs)
