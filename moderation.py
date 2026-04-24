#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
أدوات الإشراف للمالك: حظر/كتم/تحذير/حد المباريات/مكافحة farming
كل البيانات تُخزَّن في وثيقة المستخدم ضمن مجموعة `users` — لا تُمسح عند تصفير النقاط.
"""

from datetime import datetime, timezone, timedelta
from firebase_admin import firestore
from firebase_utils import db


# ====== قراءة الحالة ======

def _user_ref(uid):
    return db.collection("users").document(str(uid))


def get_user_doc(uid):
    doc = _user_ref(uid).get()
    if doc.exists:
        return {"id": doc.id, **doc.to_dict()}
    return None


def is_banned(uid):
    """يعيد (bool, reason, until) — يفكّ الحظر تلقائياً إذا انتهى وقته."""
    u = get_user_doc(uid)
    if not u:
        return False, "", None
    if not u.get("banned"):
        return False, "", None
    until = u.get("ban_until")
    if until:
        try:
            if hasattr(until, "to_datetime"):
                until_dt = until.to_datetime()
            elif hasattr(until, "timestamp"):
                until_dt = until
            else:
                until_dt = until
            # Firestore returns datetime with tz
            now = datetime.now(timezone.utc)
            if until_dt and until_dt.tzinfo is None:
                until_dt = until_dt.replace(tzinfo=timezone.utc)
            if until_dt and now >= until_dt:
                # انتهى الحظر — فكّ تلقائياً
                _user_ref(uid).update({
                    "banned": False,
                    "ban_until": None,
                })
                _log_action(uid, "auto_unban", "انتهت مدة الحظر", by=0)
                return False, "", None
        except Exception:
            pass
    return True, u.get("ban_reason", ""), until


def is_muted(uid):
    u = get_user_doc(uid)
    return bool(u and u.get("muted"))


# ====== إجراءات ======

def _log_action(uid, action_type, reason, by):
    """يُضيف دخلاً لسجل اللاعب — يُبقي آخر 30 فقط."""
    try:
        u = get_user_doc(uid) or {}
        log = u.get("actions_log") or []
        log.append({
            "ts": datetime.now(timezone.utc).isoformat(),
            "type": action_type,
            "reason": reason or "",
            "by": int(by) if by else 0,
        })
        log = log[-30:]
        _user_ref(uid).set({"actions_log": log}, merge=True)
    except Exception as e:
        print(f"⚠️ _log_action: {e}")


def ban_user(uid, reason="", duration_hours=None, by=0):
    """حظر دائم (duration=None) أو مؤقت بعدد ساعات."""
    data = {"banned": True, "ban_reason": reason or ""}
    if duration_hours:
        until = datetime.now(timezone.utc) + timedelta(hours=int(duration_hours))
        data["ban_until"] = until
    else:
        data["ban_until"] = None
    _user_ref(uid).set(data, merge=True)
    kind = f"ban_{duration_hours}h" if duration_hours else "ban_permanent"
    _log_action(uid, kind, reason, by)


def unban_user(uid, by=0):
    _user_ref(uid).set({"banned": False, "ban_until": None}, merge=True)
    _log_action(uid, "unban", "", by)


def mute_user(uid, by=0):
    _user_ref(uid).set({"muted": True}, merge=True)
    _log_action(uid, "mute", "", by)


def unmute_user(uid, by=0):
    _user_ref(uid).set({"muted": False}, merge=True)
    _log_action(uid, "unmute", "", by)


def warn_user(uid, reason="", by=0):
    """يزيد عدّاد التحذيرات ويعيد العدد الجديد."""
    _user_ref(uid).update({"warnings": firestore.Increment(1)})
    _log_action(uid, "warn", reason, by)
    u = get_user_doc(uid) or {}
    return int(u.get("warnings", 0))


def clear_warnings(uid, by=0):
    _user_ref(uid).set({"warnings": 0}, merge=True)
    _log_action(uid, "clear_warnings", "", by)


def adjust_points(uid, delta, reason="", by=0):
    _user_ref(uid).update({"points": firestore.Increment(int(delta))})
    _log_action(uid, f"points_{'+' if delta >= 0 else ''}{int(delta)}", reason, by)


def get_action_log(uid):
    u = get_user_doc(uid) or {}
    return u.get("actions_log") or []


# ====== قوائم وبحث ======

def list_all_users(order_by="created_at"):
    """كل المستخدمين المسجّلين (للمالك). لا يُمسح أحد على التصفير."""
    try:
        docs = list(db.collection("users").stream())
        users = [{"id": d.id, **d.to_dict()} for d in docs]
        # ترتيب: الأحدث أولاً (الذين ليس لديهم created_at يُوضعون في الآخر)
        def _key(u):
            v = u.get(order_by)
            try:
                if v is None:
                    return 0
                if hasattr(v, "timestamp"):
                    return -v.timestamp()
            except Exception:
                pass
            return 0
        try:
            users.sort(key=_key)
        except Exception:
            pass
        return users
    except Exception as e:
        print(f"⚠️ list_all_users: {e}")
        return []


def list_banned_users():
    return [u for u in list_all_users() if u.get("banned")]


def search_users(query):
    """بحث: ID رقمي، @username، أو جزء من الاسم/اليوزر."""
    query = (query or "").strip()
    if not query:
        return []
    # ID رقمي
    if query.isdigit():
        u = get_user_doc(query)
        return [u] if u else []
    # @username
    if query.startswith("@"):
        uname = query[1:].strip().lower()
        results = []
        for u in list_all_users():
            if (u.get("username") or "").lower() == uname:
                results.append(u)
        return results
    # نصي جزئي
    q = query.lower()
    results = []
    for u in list_all_users():
        name = (u.get("name") or "").lower()
        uname = (u.get("username") or "").lower()
        if q in name or q in uname:
            results.append(u)
    return results[:25]


# ====== الحدود اليومية ومكافحة farming ======

def _today_str():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def check_and_increment_daily_matches(uid, daily_limit):
    """
    يُزيد عدّاد مباريات اليوم إذا ما بلغ الحد.
    يعيد (allowed: bool, count_after: int, limit: int).
    إذا daily_limit=0 → لا حد.
    """
    if not daily_limit:
        return True, 0, 0
    u = get_user_doc(uid) or {}
    today = _today_str()
    cnt = int(u.get("matches_today", 0) or 0)
    date = u.get("matches_today_date") or ""
    if date != today:
        cnt = 0
    if cnt >= int(daily_limit):
        return False, cnt, int(daily_limit)
    _user_ref(uid).set({
        "matches_today": cnt + 1,
        "matches_today_date": today,
    }, merge=True)
    return True, cnt + 1, int(daily_limit)


def record_pair_match(uid1, uid2, pair_limit=3):
    """
    يسجّل مباراة بين لاعبَين (بعد انتهائها).
    يعيد count بعد التسجيل — لو تجاوز الحد فالمفروض تُصدر تنبيه + لا نمنح نقاطاً.
    """
    if not (uid1 and uid2) or int(uid1) == int(uid2):
        return 0
    a, b = sorted([str(uid1), str(uid2)])
    key = f"{a}_{b}"
    today = _today_str()
    ref = db.collection("meta").document("pair_counts")
    try:
        snap = ref.get()
        data = snap.to_dict() if snap.exists else {}
        date = data.get("_date") or ""
        if date != today:
            # تصفير يومي
            data = {"_date": today}
        cnt = int(data.get(key, 0) or 0) + 1
        data[key] = cnt
        data["_date"] = today
        ref.set(data)
        return cnt
    except Exception as e:
        print(f"⚠️ record_pair_match: {e}")
        return 0


def get_pair_count(uid1, uid2):
    if not (uid1 and uid2):
        return 0
    a, b = sorted([str(uid1), str(uid2)])
    key = f"{a}_{b}"
    today = _today_str()
    try:
        snap = db.collection("meta").document("pair_counts").get()
        if not snap.exists:
            return 0
        data = snap.to_dict() or {}
        if data.get("_date") != today:
            return 0
        return int(data.get(key, 0) or 0)
    except Exception:
        return 0
