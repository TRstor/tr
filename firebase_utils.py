#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
أدوات Firebase - تخزين إحصائيات اللاعبين ومباريات PvP للعبة XO
"""

import json
import firebase_admin
from firebase_admin import credentials, firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from config import FIREBASE_CREDENTIALS


def init_firebase():
    """تهيئة اتصال Firebase"""
    try:
        if not FIREBASE_CREDENTIALS:
            print("❌ يرجى تعيين FIREBASE_CREDENTIALS في متغيرات البيئة")
            return None
        cred_dict = json.loads(FIREBASE_CREDENTIALS)
        cred = credentials.Certificate(cred_dict)
        firebase_admin.initialize_app(cred)
        db = firestore.client()
        print("✅ تم الاتصال بـ Firebase بنجاح")
        return db
    except Exception as e:
        print(f"❌ خطأ في الاتصال بـ Firebase: {e}")
        return None


db = init_firebase()


# ============================
# === المستخدمون والإحصائيات ===
# ============================

def get_or_create_user(user_id, name=""):
    """جلب مستخدم أو إنشاؤه بسجل إحصائيات صفري"""
    ref = db.collection("users").document(str(user_id))
    doc = ref.get()
    if doc.exists:
        data = doc.to_dict()
        # تحديث الاسم إذا تغيّر
        if name and data.get("name") != name:
            ref.update({"name": name})
            data["name"] = name
        return {"id": doc.id, **data}

    new_data = {
        "user_id": int(user_id),
        "name": name or "لاعب",
        "wins": 0,
        "losses": 0,
        "draws": 0,
        "bot_easy_wins": 0,
        "bot_easy_losses": 0,
        "bot_easy_draws": 0,
        "bot_hard_wins": 0,
        "bot_hard_losses": 0,
        "bot_hard_draws": 0,
        "pvp_wins": 0,
        "pvp_losses": 0,
        "pvp_draws": 0,
        "created_at": firestore.SERVER_TIMESTAMP,
    }
    ref.set(new_data)
    return {"id": str(user_id), **new_data}


def record_result(user_id, mode, result):
    """
    mode: 'bot_easy' | 'bot_hard' | 'pvp'
    result: 'win' | 'loss' | 'draw'
    """
    if mode not in ("bot_easy", "bot_hard", "pvp"):
        return
    if result not in ("win", "loss", "draw"):
        return

    ref = db.collection("users").document(str(user_id))
    # الإجمالي
    total_key = {"win": "wins", "loss": "losses", "draw": "draws"}[result]
    # حسب النمط
    mode_key = f"{mode}_{ {'win':'wins','loss':'losses','draw':'draws'}[result] }"

    ref.update({
        total_key: firestore.Increment(1),
        mode_key: firestore.Increment(1),
    })


def get_user_stats(user_id):
    """جلب إحصائيات مستخدم"""
    doc = db.collection("users").document(str(user_id)).get()
    if doc.exists:
        return {"id": doc.id, **doc.to_dict()}
    return None


def get_leaderboard(limit=10):
    """أعلى اللاعبين حسب عدد مرات الفوز"""
    docs = db.collection("users") \
        .order_by("wins", direction=firestore.Query.DESCENDING) \
        .limit(limit) \
        .stream()
    return [{"id": d.id, **d.to_dict()} for d in docs]


# ============================
# === مباريات PvP ===
# ============================

def create_game(game_id, player_x_id, player_x_name, x_chat_id):
    """إنشاء مباراة PvP جديدة بانتظار اللاعب الثاني"""
    db.collection("games").document(game_id).set({
        "player_x_id": int(player_x_id),
        "player_x_name": player_x_name,
        "player_o_id": None,
        "player_o_name": None,
        "board": "---------",  # 9 خانات
        "turn": "X",
        "status": "waiting",  # waiting | posted | playing | finished
        "winner": None,  # X | O | draw | None
        "x_chat_id": int(x_chat_id),
        "x_msg_id": None,
        "o_chat_id": None,
        "o_msg_id": None,
        "inline_message_id": None,
        "created_at": firestore.SERVER_TIMESTAMP,
    })


def get_game(game_id):
    doc = db.collection("games").document(game_id).get()
    if doc.exists:
        return {"id": doc.id, **doc.to_dict()}
    return None


def get_pending_games():
    """جلب كل المباريات التي لم تنتهِ (waiting | posted | playing) لفحص انتهاء الصلاحية."""
    docs = db.collection("games") \
        .where(filter=FieldFilter("status", "in", ["waiting", "posted", "playing"])) \
        .stream()
    return [{"id": d.id, **d.to_dict()} for d in docs]


def update_game(game_id, data):
    db.collection("games").document(game_id).update(data)


def delete_game(game_id):
    db.collection("games").document(game_id).delete()
