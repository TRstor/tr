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
        "points": 0,
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


# جدول النقاط:
#   - PvP فوز = 3، تعادل = 1
#   - ضد البوت صعب: فوز = 2، تعادل = 1
#   - ضد البوت سهل: فوز = 1، تعادل = 0
#   - الخسارة = 0 دائماً
POINTS_TABLE = {
    ("pvp", "win"): 3,
    ("pvp", "draw"): 1,
    ("pvp", "loss"): 0,
    ("bot_hard", "win"): 2,
    ("bot_hard", "draw"): 1,
    ("bot_hard", "loss"): 0,
    ("bot_easy", "win"): 1,
    ("bot_easy", "draw"): 0,
    ("bot_easy", "loss"): 0,
}


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
    # النقاط
    pts = POINTS_TABLE.get((mode, result), 0)

    updates = {
        total_key: firestore.Increment(1),
        mode_key: firestore.Increment(1),
    }
    if pts:
        updates["points"] = firestore.Increment(pts)
    ref.update(updates)


def get_user_stats(user_id):
    """جلب إحصائيات مستخدم"""
    doc = db.collection("users").document(str(user_id)).get()
    if doc.exists:
        return {"id": doc.id, **doc.to_dict()}
    return None


def get_leaderboard(limit=25):
    """أعلى اللاعبين حسب النقاط"""
    docs = db.collection("users") \
        .order_by("points", direction=firestore.Query.DESCENDING) \
        .limit(limit) \
        .stream()
    return [{"id": d.id, **d.to_dict()} for d in docs]


def reset_all_points():
    """يُصفّر حقل `points` لكل اللاعبين — عبر batch writes."""
    count = 0
    batch = db.batch()
    ops = 0
    for d in db.collection("users").stream():
        batch.update(d.reference, {"points": 0})
        ops += 1
        count += 1
        if ops >= 450:
            batch.commit()
            batch = db.batch()
            ops = 0
    if ops:
        batch.commit()
    return count


def archive_season(season_id, reset_time_utc, top_users):
    """أرشفة لقطة أفضل 25 لاعب قبل التصفير."""
    db.collection("seasons").document(season_id).set({
        "season_id": season_id,
        "reset_at": reset_time_utc,
        "top": [
            {
                "user_id": u.get("user_id"),
                "name": u.get("name", "لاعب"),
                "points": u.get("points", 0),
            }
            for u in top_users
        ],
    })


def get_meta():
    """جلب وثيقة meta/leaderboard (تحتوي last_reset_at)."""
    doc = db.collection("meta").document("leaderboard").get()
    if doc.exists:
        return doc.to_dict()
    return {}


def set_last_reset(ts):
    """تعيين last_reset_at في meta/leaderboard."""
    db.collection("meta").document("leaderboard").set(
        {"last_reset_at": ts}, merge=True
    )


def backfill_points():
    """
    حساب النقاط للمستخدمين الذين لا يملكون حقل `points` (مستخدمون قدامى).
    يُستدعى مرة واحدة عند بدء التشغيل — آمن للتكرار.
    """
    updated = 0
    try:
        docs = db.collection("users").stream()
        for d in docs:
            data = d.to_dict()
            if "points" in data:
                continue  # محسوب سابقاً
            pts = 0
            pts += POINTS_TABLE[("pvp", "win")] * data.get("pvp_wins", 0)
            pts += POINTS_TABLE[("pvp", "draw")] * data.get("pvp_draws", 0)
            pts += POINTS_TABLE[("bot_hard", "win")] * data.get("bot_hard_wins", 0)
            pts += POINTS_TABLE[("bot_hard", "draw")] * data.get("bot_hard_draws", 0)
            pts += POINTS_TABLE[("bot_easy", "win")] * data.get("bot_easy_wins", 0)
            d.reference.update({"points": pts})
            updated += 1
        print(f"✅ backfill_points: updated {updated} users")
    except Exception as e:
        print(f"⚠️ backfill_points: {e}")
    return updated


# ============================
# === مباريات PvP ===
# ============================

def create_game(game_id, player_x_id, player_x_name, x_chat_id):
    """إنشاء مباراة PvP جديدة: المنشئ يلعب كـ ❌"""
    db.collection("games").document(game_id).set({
        "player_x_id": int(player_x_id),
        "player_x_name": player_x_name,
        "player_o_id": None,
        "player_o_name": None,
        "board": "---------",  # 9 خانات
        "turn": "X",
        "status": "waiting",
        "winner": None,
        "x_chat_id": int(x_chat_id) if x_chat_id else None,
        "x_msg_id": None,
        "o_chat_id": None,
        "o_msg_id": None,
        "inline_message_id": None,
        "created_at": firestore.SERVER_TIMESTAMP,
    })


def create_game_symbol(game_id, creator_id, creator_name, symbol):
    """
    إنشاء مباراة مع اختيار رمز المنشئ (X أو O).
    إذا اختار X → هو player_x ويبدأ أولاً.
    إذا اختار O → هو player_o، والدور للـ X الذي سينضم.
    """
    symbol = symbol.upper()
    base = {
        "player_x_id": None,
        "player_x_name": None,
        "player_o_id": None,
        "player_o_name": None,
        "board": "---------",
        "turn": "X",
        "status": "waiting",
        "winner": None,
        "x_chat_id": None,
        "x_msg_id": None,
        "o_chat_id": None,
        "o_msg_id": None,
        "inline_message_id": None,
        "created_at": firestore.SERVER_TIMESTAMP,
    }
    if symbol == "X":
        base["player_x_id"] = int(creator_id)
        base["player_x_name"] = creator_name
    else:
        base["player_o_id"] = int(creator_id)
        base["player_o_name"] = creator_name
    db.collection("games").document(game_id).set(base)


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
