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
    from google.cloud.firestore_v1.base_query import FieldFilter
    ops = db.collection("operations") \
        .where(filter=FieldFilter("user_id", "==", user_id)) \
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

def add_email(user_id, email, subscription_type="", max_clients=5):
    """إضافة إيميل أساسي"""
    doc_ref = db.collection("emails").document()
    doc_ref.set({
        "user_id": user_id,
        "email": email,
        "subscription_type": subscription_type,
        "max_clients": max_clients,
        "created_at": firestore.SERVER_TIMESTAMP
    })
    return doc_ref.id

def get_emails(user_id):
    """جلب جميع الإيميلات"""
    from google.cloud.firestore_v1.base_query import FieldFilter
    docs = db.collection("emails") \
        .where(filter=FieldFilter("user_id", "==", user_id)) \
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

def update_email(email_id, data):
    """تحديث بيانات الإيميل"""
    db.collection("emails").document(email_id).update(data)

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

def search_clients_by_name(user_id, search_term):
    """البحث عن عملاء بالاسم"""
    results = []
    from google.cloud.firestore_v1.base_query import FieldFilter
    emails = db.collection("emails") \
        .where(filter=FieldFilter("user_id", "==", user_id)) \
        .stream()
    
    for email_doc in emails:
        email_data = email_doc.to_dict()
        email_id = email_doc.id
        clients = db.collection("emails").document(email_id) \
            .collection("clients").stream()
        
        for client_doc in clients:
            client_data = client_doc.to_dict()
            client_name = client_data.get("name", "").lower()
            if search_term.lower() in client_name:
                results.append({
                    "client_id": client_doc.id,
                    "email_id": email_id,
                    "email": email_data.get("email", ""),
                    "subscription_type": email_data.get("subscription_type", ""),
                    **client_data
                })
    return results

def get_all_clients_with_emails(user_id):
    """جلب جميع العملاء مع بيانات الإيميل للتنبيهات"""
    results = []
    from google.cloud.firestore_v1.base_query import FieldFilter
    emails = db.collection("emails") \
        .where(filter=FieldFilter("user_id", "==", user_id)) \
        .stream()
    
    for email_doc in emails:
        email_data = email_doc.to_dict()
        email_id = email_doc.id
        clients = db.collection("emails").document(email_id) \
            .collection("clients").stream()
        
        for client_doc in clients:
            client_data = client_doc.to_dict()
            results.append({
                "client_id": client_doc.id,
                "email_id": email_id,
                "email": email_data.get("email", ""),
                "subscription_type": email_data.get("subscription_type", ""),
                **client_data
            })
    return results
