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
#   - PvP فوز = 10، تعادل = 3، خسارة = 1
#   - ضد البوت صعب: فوز = 2، تعادل = 1
#   - ضد البوت سهل: فوز = 1، تعادل = 0
#   - الخسارة ضد البوت = 0
POINTS_TABLE = {
    ("pvp", "win"): 10,
    ("pvp", "draw"): 3,
    ("pvp", "loss"): 1,
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


def get_last_season():
    """جلب أحدث موسم مُؤرشف (أو None إن لم يوجد)."""
    try:
        docs = list(
            db.collection("seasons")
            .order_by("reset_at", direction=firestore.Query.DESCENDING)
            .limit(1)
            .stream()
        )
        if docs:
            d = docs[0]
            return {"id": d.id, **d.to_dict()}
    except Exception as e:
        print(f"⚠️ get_last_season: {e}")
    return None


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


def get_flags():
    """جلب أعلام التفعيل (feature flags) من meta/flags."""
    doc = db.collection("meta").document("flags").get()
    if doc.exists:
        return doc.to_dict()
    return {}


def set_flag(name, value):
    """تعيين علم ميزة."""
    db.collection("meta").document("flags").set({name: bool(value)}, merge=True)


def export_all():
    """تصدير جميع مجموعات Firestore إلى قاموس قابل للتسلسل JSON."""
    import datetime as _dt

    def _json_safe(v):
        if isinstance(v, _dt.datetime):
            return {"__dt__": v.isoformat()}
        if isinstance(v, dict):
            return {k: _json_safe(x) for k, x in v.items()}
        if isinstance(v, list):
            return [_json_safe(x) for x in v]
        return v

    result = {}
    for col in ("users", "games", "seasons", "meta", "queue"):
        docs = []
        try:
            for d in db.collection(col).stream():
                docs.append({"_id": d.id, **_json_safe(d.to_dict() or {})})
        except Exception as e:
            print(f"⚠️ export_all {col}: {e}")
        result[col] = docs
    return result


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
            pts += POINTS_TABLE[("pvp", "loss")] * data.get("pvp_losses", 0)
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


# ============================
# === طابور "العب الآن" ===
# ============================

def queue_add(user_id, name, chat_id):
    """أضف لاعباً للطابور (أو حدّث بياناته إن كان موجوداً)."""
    db.collection("queue").document(str(user_id)).set({
        "user_id": int(user_id),
        "name": name or "لاعب",
        "chat_id": int(chat_id),
        "joined_at": firestore.SERVER_TIMESTAMP,
    })


def queue_remove(user_id):
    """أزل لاعباً من الطابور (بصمت إن لم يكن موجوداً)."""
    try:
        db.collection("queue").document(str(user_id)).delete()
    except Exception:
        pass


def queue_in(user_id):
    """هل اللاعب حالياً في الطابور؟"""
    return db.collection("queue").document(str(user_id)).get().exists


def queue_size():
    """عدد اللاعبين المنتظرين."""
    try:
        # count() أخف من stream()
        agg = db.collection("queue").count().get()
        return int(agg[0][0].value)
    except Exception:
        # fallback
        return sum(1 for _ in db.collection("queue").limit(500).stream())


def queue_try_match(new_user_id, new_name, new_chat_id):
    """
    محاولة مطابقة ذرّية:
      - إن وجد منتظراً غيرك → احذفه من الطابور وأرجعه (مباراة!).
      - إن لم يوجد → أضفك للطابور وأرجع None (انتظار).
    ذرّية عبر Firestore Transaction لمنع race conditions.
    """
    queue_ref = db.collection("queue")

    @firestore.transactional
    def _txn(transaction):
        # ابحث عن أقدم منتظر غير الـ new_user_id
        docs = list(queue_ref.order_by("joined_at").limit(5).stream(transaction=transaction))
        opponent = None
        for d in docs:
            if d.id != str(new_user_id):
                opponent = d
                break
        if opponent is not None:
            transaction.delete(opponent.reference)
            data = opponent.to_dict()
            return {"id": opponent.id, **data}
        # لا يوجد منافس → أضف الداخل للطابور
        transaction.set(queue_ref.document(str(new_user_id)), {
            "user_id": int(new_user_id),
            "name": new_name or "لاعب",
            "chat_id": int(new_chat_id),
            "joined_at": firestore.SERVER_TIMESTAMP,
        })
        return None

    transaction = db.transaction()
    return _txn(transaction)


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
