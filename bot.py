#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
بوت تيليجرام - لعبة XO (إكس-أو)
الأنماط:
  - ضد البوت (سهل / صعب Minimax لا يُهزم)
  - ضد لاعب ثاني عبر رابط تحدٍّ
  - إحصائيات شخصية + لوحة شرف
"""

import secrets
import random
import threading
import time as time_mod
import json
from datetime import datetime, timezone, timedelta

import telebot
from telebot import types

from config import BOT_TOKEN, ADMIN_ID
from firebase_utils import (
    get_or_create_user, record_result, get_user_stats, get_leaderboard,
    create_game, create_game_symbol, get_game, update_game, delete_game,
    get_pending_games, backfill_points,
    reset_all_points, archive_season, get_meta, set_last_reset,
    queue_add, queue_remove, queue_in, queue_size, queue_try_match,
    get_last_season,
    get_flags, set_flag, export_all,
    get_active_game_for_user,
    get_help_sections, set_help_section, delete_help_section,
    get_bot_config, set_bot_config,
)
from moderation import (
    is_banned, is_muted, ban_user, unban_user,
    mute_user, unmute_user, warn_user, clear_warnings, adjust_points,
    get_action_log, get_user_doc,
    list_all_users, list_banned_users, search_users,
    check_and_increment_daily_matches, record_pair_match, get_pair_count,
    get_pair_points, add_pair_points,
)
from security_utils import (
    encrypt_field, decrypt_field,
    totp_enabled, verify_totp, totp_provisioning_uri, generate_totp_secret,
    request_2fa, get_pending_2fa, consume_2fa, cancel_2fa,
)

# إعدادات الإشراف (قابلة للتعديل لاحقاً من اللوحة)
DAILY_MATCH_LIMIT = 150             # حد أقصى للمباريات اليومية للاعب (0 = بلا حد)
PAIR_DAILY_POINTS_CAP = 300         # حد أقصى للنقاط من نفس الخصم في اليوم
USERS_PAGE_SIZE = 10                # عدد المستخدمين في كل صفحة


def _enforce_daily_limit(uid):
    """يفحص الحد اليومي ويُزيده بناءً على الإعدادات الديناميكية."""
    if ADMIN_ID and int(uid) == int(ADMIN_ID):
        return True
        
    config = get_bot_config()
    current_limit = config.get("daily_limit", DAILY_MATCH_LIMIT)
    
    if not current_limit:
        return True
    try:
        allowed, count_after, limit = check_and_increment_daily_matches(uid, current_limit)
    except Exception as e:
        print(f"⚠️ daily limit check: {e}")
        return True
    if not allowed:
        try:
            bot.send_message(
                uid,
                f"⛔ *وصلت الحد اليومي*\n\n"
                f"الحد المسموح: *{limit}* مباريات/يوم.\n"
                f"يُعاد التعيين عند منتصف الليل (UTC).",
                parse_mode="Markdown",
            )
        except Exception:
            pass
    return allowed


def _has_active_game_block(uid):
    """يفحص هل لدى المستخدم مباراة PvP نشطة."""
    try:
        active = get_active_game_for_user(uid)
    except Exception as e:
        print(f"⚠️ active game check: {e}")
        return False
    if not active:
        return False
    gid = active.get("id")
    x_name = active.get("player_x_name", "X")
    o_name = active.get("player_o_name") or "—"
    text = (
        "⚠️ *لديك مباراة نشطة بالفعل*\n\n"
        f"❌ {x_name}  ⚔️  ⭕ {o_name}\n\n"
        "لا يمكنك بدء مباراة جديدة قبل إنهاء الحالية.\n"
        "اختر:"
    )
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("▶️ متابعة المباراة", callback_data=f"resume_{gid}"))
    kb.add(types.InlineKeyboardButton("🏳️ استسلام (تنتهي المباراة)",
                                       callback_data=f"resign_{gid}"))
    try:
        bot.send_message(uid, text, reply_markup=kb, parse_mode="Markdown")
    except Exception:
        pass
    return True


# حالات بحث المالك (واحدة لكل مالك)
admin_search_waiting = {}
admin_help_state = {}

if not BOT_TOKEN:
    print("❌ يرجى تعيين BOT_TOKEN في متغيرات البيئة")
    raise SystemExit(1)

bot = telebot.TeleBot(BOT_TOKEN, parse_mode=None)


# ============================
# === قوائم أوامر البوت (Scopes) ===
# ============================
def setup_bot_commands():
    try:
        from telebot.types import (
            BotCommand,
            BotCommandScopeAllPrivateChats,
            BotCommandScopeAllGroupChats,
            BotCommandScopeChat,
            BotCommandScopeDefault,
        )

        private_cmds = [
            BotCommand("start", "🎮 بدء البوت"),
        ]
        group_cmds = []
        admin_cmds = [
            BotCommand("start",     "🎮 بدء البوت"),
            BotCommand("admin",     "👑 لوحة الإدارة"),
            BotCommand("status",    "📊 حالة البوت"),
            BotCommand("backup",    "💾 نسخة احتياطية"),
            BotCommand("reset",     "♻️ إعادة الضبط (2FA)"),
            BotCommand("2fa_setup", "🔐 إعداد التحقق الثنائي"),
        ]

        for scope in (
            BotCommandScopeDefault(),
            BotCommandScopeAllPrivateChats(),
            BotCommandScopeAllGroupChats(),
        ):
            try:
                bot.delete_my_commands(scope=scope)
            except Exception:
                pass
        if ADMIN_ID:
            try:
                bot.delete_my_commands(scope=BotCommandScopeChat(int(ADMIN_ID)))
            except Exception:
                pass

        bot.set_my_commands(private_cmds, scope=BotCommandScopeDefault())
        bot.set_my_commands(private_cmds, scope=BotCommandScopeAllPrivateChats())
        bot.set_my_commands(group_cmds,   scope=BotCommandScopeAllGroupChats())
        if ADMIN_ID:
            try:
                bot.set_my_commands(admin_cmds, scope=BotCommandScopeChat(int(ADMIN_ID)))
            except Exception as e:
                print(f"⚠️ set admin commands: {e}")
        print("✅ تم ضبط قوائم الأوامر (default/private/group/admin).")
    except Exception as e:
        print(f"⚠️ setup_bot_commands: {e}")


_BOT_USERNAME_CACHE = {"value": None}


def get_bot_username():
    if _BOT_USERNAME_CACHE["value"] is not None:
        return _BOT_USERNAME_CACHE["value"]
    try:
        _BOT_USERNAME_CACHE["value"] = bot.get_me().username or ""
    except Exception as e:
        print(f"⚠️ تعذر جلب اسم البوت: {e}")
        _BOT_USERNAME_CACHE["value"] = ""
    return _BOT_USERNAME_CACHE["value"]


# ============================
# === حُرّاس مكان الأمر ===
# ============================
def private_only(func):
    from functools import wraps

    @wraps(func)
    def wrapper(message, *args, **kwargs):
        try:
            chat_type = getattr(message.chat, "type", "private")
        except Exception:
            chat_type = "private"
        if chat_type != "private":
            return
        return func(message, *args, **kwargs)

    return wrapper


def group_only(func):
    from functools import wraps

    @wraps(func)
    def wrapper(message, *args, **kwargs):
        try:
            chat_type = getattr(message.chat, "type", "private")
        except Exception:
            chat_type = "private"
        if chat_type not in ("group", "supergroup"):
            return
        return func(message, *args, **kwargs)

    return wrapper


# مباريات ضد البوت (بالذاكرة فقط)
bot_games = {}

# وقت إقلاع البوت
BOT_START_TIME = datetime.now(timezone.utc)

# جلسات حاسبة الشعبية (بالذاكرة)
popcalc_sessions = {}

# جدول الشعبية → النقاط
POP_TIERS = [
    (0,         2_000,     6),
    (2_001,     4_000,     10),
    (4_001,     8_000,     14),
    (8_001,     15_000,    16),
    (15_001,    50_000,    20),
    (50_001,    120_000,   24),
    (120_001,   260_000,   28),
    (260_001,   500_000,   32),
    (500_001,   900_000,   36),
    (900_001,   2_000_000, 40),
    (2_000_001, 10**12,    42),
]


def pop_points(popularity):
    for lo, hi, pts in POP_TIERS:
        if lo <= popularity <= hi:
            return pts
    return POP_TIERS[-1][2]


# جدول نقاط معركة الفريق
TEAM_TIERS = [
    (2_000,     5_000,     6),
    (5_001,     12_000,    10),
    (12_001,    26_000,    14),
    (26_001,    48_000,    16),
    (48_001,    120_000,   20),
    (120_001,   200_000,   24),
    (200_001,   400_000,   28),
    (400_001,   560_000,   32),
    (560_001,   800_000,   34),
    (800_001,   2_000_000, 36),
    (2_000_001, 10**12,    38),
]


def team_points(popularity):
    if popularity < TEAM_TIERS[0][0]:
        return TEAM_TIERS[0][2]
    for lo, hi, pts in TEAM_TIERS:
        if lo <= popularity <= hi:
            return pts
    return TEAM_TIERS[-1][2]


EMPTY = "-"
PLAYER_X = "X"
PLAYER_O = "O"

CHALLENGE_TIMEOUT_SECONDS = 120
MOVE_TIMEOUT_SECONDS = 10
QUICK_MATCH_TIMEOUT_SECONDS = 60
quick_search_sessions = {}
_qs_lock = threading.Lock()

# ====== أعلام الميزات ======
FEATURES = {
    "xo_enabled": True,
    "popcalc_enabled": True,
    "teamcalc_enabled": True,
}


def load_flags():
    try:
        stored = get_flags() or {}
        for k in list(FEATURES.keys()):
            if k in stored:
                FEATURES[k] = bool(stored[k])
    except Exception as e:
        print(f"⚠️ load_flags: {e}")


# ====== جدولة إعادة ضبط النقاط الأسبوعية ======
RIYADH_OFFSET = timedelta(hours=3)
RESET_WEEKDAY = 4  # الجمعة


def last_scheduled_reset(now_utc):
    now_ry = now_utc + RIYADH_OFFSET
    days_since = (now_ry.weekday() - RESET_WEEKDAY) % 7
    last_ry = (now_ry - timedelta(days=days_since)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return (last_ry - RIYADH_OFFSET).replace(tzinfo=timezone.utc)


def next_scheduled_reset(now_utc):
    return last_scheduled_reset(now_utc) + timedelta(days=7)


def format_time_left(delta):
    total = int(delta.total_seconds())
    if total <= 0:
        return "الآن"
    days = total // 86400
    hours = (total % 86400) // 3600
    minutes = (total % 3600) // 60
    if days > 0:
        return f"{days} يوم {hours} ساعة"
    if hours > 0:
        return f"{hours} ساعة {minutes} دقيقة"
    return f"{minutes} دقيقة"


WIN_LINES = [
    (0, 1, 2), (3, 4, 5), (6, 7, 8),
    (0, 3, 6), (1, 4, 7), (2, 5, 8),
    (0, 4, 8), (2, 4, 6),
]


# ============================
# === منطق اللعبة ===
# ============================

def check_winner(board):
    for a, b, c in WIN_LINES:
        if board[a] != EMPTY and board[a] == board[b] == board[c]:
            return board[a]
    if EMPTY not in board:
        return "draw"
    return None


def available_moves(board):
    return [i for i, v in enumerate(board) if v == EMPTY]


def minimax(board, is_maximizing, ai_symbol, human_symbol):
    result = check_winner(board)
    if result == ai_symbol:
        return 1
    if result == human_symbol:
        return -1
    if result == "draw":
        return 0

    if is_maximizing:
        best = -2
        for m in available_moves(board):
            board[m] = ai_symbol
            score = minimax(board, False, ai_symbol, human_symbol)
            board[m] = EMPTY
            if score > best:
                best = score
        return best
    else:
        best = 2
        for m in available_moves(board):
            board[m] = human_symbol
            score = minimax(board, True, ai_symbol, human_symbol)
            board[m] = EMPTY
            if score < best:
                best = score
        return best


def best_move_hard(board, ai_symbol, human_symbol):
    best_score = -2
    best = None
    moves = available_moves(board)
    random.shuffle(moves)
    for m in moves:
        board[m] = ai_symbol
        score = minimax(board, False, ai_symbol, human_symbol)
        board[m] = EMPTY
        if score > best_score:
            best_score = score
            best = m
    return best


def best_move_easy(board):
    moves = available_moves(board)
    return random.choice(moves) if moves else None


# ============================
# === الواجهات ===
# ============================

def start_menu_kb():
    kb = types.InlineKeyboardMarkup(row_width=1)
    xo_lbl = "🎮 لعبة XO" if FEATURES["xo_enabled"] else "🎮 لعبة XO  🔒"
    kb.add(types.InlineKeyboardButton(xo_lbl, callback_data="open_xo"))
    kb.add(types.InlineKeyboardButton("🧮 الحاسبات", callback_data="open_calcs"))
    return kb


def calcs_menu_kb():
    kb = types.InlineKeyboardMarkup(row_width=1)
    pc_lbl = "🔥 حاسبة المعركة الفردية" if FEATURES["popcalc_enabled"] else "🔥 حاسبة المعركة الفردية  🔒"
    tc_lbl = "⚔️ حاسبة معركة الفريق" if FEATURES["teamcalc_enabled"] else "⚔️ حاسبة معركة الفريق  🔒"
    kb.add(types.InlineKeyboardButton(pc_lbl, callback_data="open_popcalc"))
    kb.add(types.InlineKeyboardButton(tc_lbl, callback_data="open_teamcalc"))
    kb.add(types.InlineKeyboardButton("🔙 رجوع", callback_data="back_start"))
    return kb


def popcalc_menu_kb():
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("🧮 حاسبة جديدة", callback_data="popcalc_new"))
    kb.add(types.InlineKeyboardButton("📋 جدول النقاط", callback_data="popcalc_tiers"))
    kb.add(types.InlineKeyboardButton("🔙 رجوع", callback_data="open_calcs"))
    return kb


def popcalc_cancel_kb():
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("❌ إلغاء", callback_data="popcalc_cancel"))
    return kb


def popcalc_back_kb():
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("🔙 رجوع", callback_data="open_popcalc"))
    return kb


def popcalc_result_kb():
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("🔁 حاسبة جديدة", callback_data="popcalc_new"))
    kb.add(types.InlineKeyboardButton("🔙 رجوع", callback_data="open_popcalc"))
    return kb


def popcalc_intro_text():
    return (
        "🔥 *حاسبة المعركة الفردية*\n\n"
        "احسب نقاط الفوز والخسارة قبل أن تدخل المعركة!\n\n"
        "اضغط « *حاسبة جديدة* » لتبدأ."
    )


def popcalc_tiers_text():
    return (
        "📊 *إحصائيات المعركة الفردية*\n\n"
        "{من 0 إلى 2,000} = 6 نقطة\n"
        "{من 2,001 إلى 4,000} = 10 نقطة\n"
        "{من 4,001 إلى 8,000} = 14 نقطة\n"
        "{من 8,001 إلى 15,000} = 16 نقطة\n"
        "{من 15,001 إلى 50,000} = 20 نقطة\n"
        "{من 50,001 إلى 120,000} = 24 نقطة\n"
        "{من 120,001 إلى 260,000} = 28 نقطة\n"
        "{من 260,001 إلى 500,000} = 32 نقطة\n"
        "{من 500,001 إلى 900,000} = 36 نقطة\n"
        "{من 900,001 إلى 2,000,000} = 40 نقطة\n"
        "{من 2,000,001 إلى ∞} = 42 نقطة\n\n"
        "*في حالة فوزك:*\n"
        "ستحصل على نقاطك + ونصف نقاط خصمك\n\n"
        "*في حالة خسارتك:*\n"
        "ستحصل على نصف نقاطك فقط\n\n"
        "_ملاحظة: الإحصائية تقريبية وليست دقيقة 100%_"
    )


def teamcalc_menu_kb():
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("🧮 حاسبة جديدة", callback_data="teamcalc_new"))
    kb.add(types.InlineKeyboardButton("📋 جدول النقاط", callback_data="teamcalc_tiers"))
    kb.add(types.InlineKeyboardButton("🔙 رجوع", callback_data="open_calcs"))
    return kb


def teamcalc_cancel_kb():
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("❌ إلغاء", callback_data="teamcalc_cancel"))
    return kb


def teamcalc_result_kb():
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("🔁 حاسبة جديدة", callback_data="teamcalc_new"))
    kb.add(types.InlineKeyboardButton("🔙 رجوع", callback_data="open_teamcalc"))
    return kb


def teamcalc_intro_text():
    return (
        "⚔️ *حاسبة معركة الفريق*\n\n"
        "احسب نقاط الفوز والخسارة قبل أن تدخل معركة الفريق!\n\n"
        "اضغط « *حاسبة جديدة* » لتبدأ."
    )


def teamcalc_tiers_text():
    return (
        "📊 *إحصائيات معركة الفريق*\n\n"
        "{من 2,000 إلى 5,000} = 6 نقطة\n"
        "{من 5,001 إلى 12,000} = 10 نقطة\n"
        "{من 12,001 إلى 26,000} = 14 نقطة\n"
        "{من 26,001 إلى 48,000} = 16 نقطة\n"
        "{من 48,001 إلى 120,000} = 20 نقطة\n"
        "{من 120,001 إلى 200,000} = 24 نقطة\n"
        "{من 200,001 إلى 400,000} = 28 نقطة\n"
        "{من 400,001 إلى 560,000} = 32 نقطة\n"
        "{من 560,001 إلى 800,000} = 34 نقطة\n"
        "{من 800,001 إلى 2,000,000} = 36 نقطة\n"
        "{من 2,000,001 إلى ∞} = 38 نقطة\n\n"
        "*في حالة فوزك:*\n"
        "ستحصل على نقاطك + ونصف نقاط خصمك\n\n"
        "*في حالة خسارتك:*\n"
        "ستحصل على نصف نقاطك فقط\n\n"
        "_ملاحظة: الإحصائية تقريبية وليست دقيقة 100%_"
    )


def main_menu_kb():
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(types.InlineKeyboardButton("⚡ العب الآن (خصم عشوائي)", callback_data="quick_match"))
    kb.row(
        types.InlineKeyboardButton("🤖 ضد البوت", callback_data="menu_bot"),
        types.InlineKeyboardButton("👥 ضد صديق", callback_data="menu_pvp"),
    )
    kb.row(
        types.InlineKeyboardButton("📊 إحصائياتي", callback_data="menu_stats"),
        types.InlineKeyboardButton("🏆 لوحة الشرف", callback_data="menu_leaderboard"),
    )
    kb.add(types.InlineKeyboardButton("ℹ️ كيف تلعب", callback_data="menu_help"))
    kb.add(types.InlineKeyboardButton("🔙 رجوع", callback_data="back_start"))
    return kb


def difficulty_kb():
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("🟢 سهل", callback_data="bot_start_easy"),
        types.InlineKeyboardButton("🔴 صعب", callback_data="bot_start_hard"),
    )
    kb.add(types.InlineKeyboardButton("🔙 رجوع", callback_data="back_main"))
    return kb


def pvp_menu_kb():
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(
        types.InlineKeyboardButton("➕ إنشاء تحدٍّ جديد", callback_data="pvp_create"),
        types.InlineKeyboardButton("🔙 رجوع", callback_data="back_main"),
    )
    return kb


def board_kb(board, prefix, disabled=False):
    kb = types.InlineKeyboardMarkup(row_width=3)
    row = []
    for i, cell in enumerate(board):
        if cell == PLAYER_X:
            label = "❌"
        elif cell == PLAYER_O:
            label = "⭕"
        else:
            label = "·"
        if disabled or cell != EMPTY:
            cb = f"{prefix}:noop"
        else:
            cb = f"{prefix}:move:{i}"
        row.append(types.InlineKeyboardButton(label, callback_data=cb))
        if len(row) == 3:
            kb.row(*row)
            row = []
    if prefix == "bot" or disabled:
        kb.row(
            types.InlineKeyboardButton("🏠 القائمة", callback_data="back_main"),
        )
    return kb


# ============================
# === تنسيق الرسائل ===
# ============================

def fmt_bot_game(game):
    diff_txt = "🟢 سهل" if game["difficulty"] == "easy" else "🔴 صعب"
    turn_txt = "دورك ❌" if game["turn"] == PLAYER_X else "دور البوت ⭕"
    return (
        f"🎮 *لعبة XO - ضد البوت ({diff_txt})*\n"
        f"{turn_txt}\n"
    )


def fmt_pvp_game(game, viewer_id, is_group=False):
    x_name = game.get("player_x_name", "X")
    o_name = game.get("player_o_name") or "بانتظار لاعب..."
    turn = game.get("turn", PLAYER_X)
    status = game.get("status", "waiting")

    header = f"🎮 *لعبة XO - ضد صديق*\n❌ {x_name}  ⚔️  ⭕ {o_name}\n"

    if status == "waiting":
        header += "\n⏳ بانتظار انضمام اللاعب الثاني..."
        return header

    if status == "finished":
        winner = game.get("winner")
        if winner == "draw":
            header += "\n🤝 تعادل!"
        else:
            winner_name = x_name if winner == PLAYER_X else o_name
            header += f"\n🏆 الفائز: {winner_name} ({'❌' if winner==PLAYER_X else '⭕'})"
        return header

    turn_symbol = "❌" if turn == PLAYER_X else "⭕"
    turn_name = x_name if turn == PLAYER_X else o_name

    if is_group:
        header += f"\n⏳ دور {turn_name} ({turn_symbol})"
    else:
        your_turn = (
            (turn == PLAYER_X and viewer_id == game.get("player_x_id")) or
            (turn == PLAYER_O and viewer_id == game.get("player_o_id"))
        )
        if your_turn:
            header += f"\n👉 دورك الآن! ({turn_symbol})"
        else:
            header += f"\n⏳ دور {turn_name} ({turn_symbol})"
    return header


def board_list(board_str):
    return list(board_str)


def board_str(board_list_):
    return "".join(board_list_)


# ============================
# === أوامر البوت ===
# ============================

@bot.message_handler(commands=["start"])
@private_only
def cmd_start(message):
    uid = message.chat.id
    name = message.from_user.first_name or "لاعب"
    username = message.from_user.username or ""
    get_or_create_user(uid, name, username)

    if not require_not_banned_msg(message):
        return

    parts = (message.text or "").split(maxsplit=1)
    if len(parts) == 2 and parts[1].startswith("join_"):
        game_id = parts[1][len("join_"):]
        if not require_username(message):
            return
        if not FEATURES["xo_enabled"] and not is_admin(uid):
            bot.send_message(uid, "🔒 لعبة XO متوقفة مؤقتاً")
            return
        handle_join_game(uid, name, game_id)
        return

    if not require_username(message):
        return

    config = get_bot_config()
    default_text = "👋 أهلاً *{name}*!\n\n🆔 ID: `{uid}`\n👤 اليوزر: `@{username}`\n\nاختر من القائمة:"
    custom_text = config.get("start_msg", default_text)
    
    # حل احترافي: تشفير اليوزر نيم لتفادي أخطاء الماركداون (الشرطة السفلية)، وكتابة "لا يوجد" إن كان فارغاً
    user_display = _md_escape(username) if username else "لا يوجد"
    text = custom_text.replace("{name}", _md_escape(name)).replace("{uid}", str(uid)).replace("{username}", user_display)
    
    quest = config.get("daily_quest")
    if quest:
        text += f"\n\n🎯 *المهمة اليومية:*\n_{quest}_"

    bot.send_message(uid, text, reply_markup=start_menu_kb(), parse_mode="Markdown")

def require_username(message):
    username = (message.from_user.username or "").strip()
    if username:
        return True
    uid = message.chat.id
    text = (
        "🔒 *لا يمكن استخدام البوت بدون يوزر*\n\n"
        "لضمان عدالة المسابقة ومنع الانتحال، نطلب من كل لاعب إضافة "
        "*اسم مستخدم (Username)* في تليجرام.\n\n"
        "📱 *كيفية الإضافة:*\n"
        "1️⃣ افتح الإعدادات ⚙️\n"
        "2️⃣ اضغط على *اسم المستخدم / Username*\n"
        "3️⃣ اختر اسماً وأكّده\n"
        "4️⃣ عُد وأرسل /start من جديد\n\n"
        "✅ بعد الإضافة ستتمكن من اللعب والمنافسة على الجوائز."
    )
    try:
        bot.send_message(uid, text, parse_mode="Markdown")
    except Exception:
        pass
    return False


def _fmt_ban_until(until):
    try:
        if hasattr(until, "to_datetime"):
            dt = until.to_datetime()
        else:
            dt = until
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        riyadh = dt.astimezone(timezone(timedelta(hours=3)))
        return riyadh.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return "—"


# ==========================================
# --- نظام الحماية التلقائي من السبام ---
# ==========================================
user_spam_tracker = {}
SPAM_LIMIT = 7
SPAM_WINDOW = 3.0
AUTO_BAN_HOURS = 0.25


def _is_spamming(uid):
    if ADMIN_ID and int(uid) == int(ADMIN_ID):
        return False
    now = time_mod.time()
    times = user_spam_tracker.get(uid, [])
    times = [t for t in times if now - t < SPAM_WINDOW]
    times.append(now)
    user_spam_tracker[uid] = times
    if len(times) >= SPAM_LIMIT:
        user_spam_tracker[uid] = []
        return True
    return False


def require_not_banned_msg(message):
    uid = message.chat.id
    if ADMIN_ID and int(uid) == int(ADMIN_ID):
        return True
    if _is_spamming(uid):
        try:
            ban_user(uid, reason="نظام الحماية: سبام (إرسال رسائل سريعة)", duration_hours=AUTO_BAN_HOURS, by="Auto-System")
            bot.send_message(uid, "🚫 *نظام الحماية التلقائي*\n\nتم حظرك مؤقتاً لمدة 15 دقيقة بسبب إرسال رسائل بسرعة غير طبيعية (سبام).\nالرجاء التوقف عن الإزعاج.", parse_mode="Markdown")
        except Exception:
            pass
        return False
    if is_muted(uid):
        return False
    banned, reason, until = is_banned(uid)
    if banned:
        txt = "🚫 *تم حظرك من استخدام البوت*"
        if reason:
            txt += f"\n📝 السبب: {reason}"
        if until:
            txt += f"\n⏳ حتى: {_fmt_ban_until(until)}"
        else:
            txt += "\n⛔ حظر دائم"
        try:
            bot.send_message(uid, txt, parse_mode="Markdown")
        except Exception:
            pass
        return False
    return True


def require_not_banned_call(call):
    uid = call.from_user.id
    if ADMIN_ID and int(uid) == int(ADMIN_ID):
        return True
    if _is_spamming(uid):
        try:
            ban_user(uid, reason="نظام الحماية: سبام (نقر سريع جداً)", duration_hours=AUTO_BAN_HOURS, by="Auto-System")
            bot.answer_callback_query(call.id, "🚫 تم حظرك 15 دقيقة بسبب الضغط العشوائي والسريع!", show_alert=True)
            bot.send_message(uid, "🚫 *نظام الحماية التلقائي*\n\nتم حظرك مؤقتاً لمدة 15 دقيقة بسبب استخدام برامج النقر التلقائي أو الضغط السريع (سبام).", parse_mode="Markdown")
        except Exception:
            pass
        return False
    if is_muted(uid):
        return False
    banned, reason, until = is_banned(uid)
    if banned:
        msg = "🚫 محظور"
        if reason:
            msg += f" — {reason[:60]}"
        try:
            bot.send_message(uid, msg)
        except Exception:
            pass
        return False
    return True


# ============================
# === أوامر المالك ===
# ============================

def is_admin(uid):
    return ADMIN_ID and int(uid) == int(ADMIN_ID)


@bot.message_handler(commands=["admin"])
@private_only
def cmd_admin(message):
    uid = message.chat.id
    if not is_admin(uid):
        return
    bot.send_message(uid, admin_panel_text(),
                     reply_markup=admin_panel_kb(), parse_mode="Markdown")


def admin_panel_text():
    xo = "✅" if FEATURES["xo_enabled"] else "🔒"
    pc = "✅" if FEATURES["popcalc_enabled"] else "🔒"
    tc = "✅" if FEATURES["teamcalc_enabled"] else "🔒"
    return (
        "🛠 *لوحة المالك*\n\n"
        f"الميزات الحالية:\n"
        f"  🎮 XO  ·  {xo}\n"
        f"  🔥 الفردية  ·  {pc}\n"
        f"  ⚔️ الفريق  ·  {tc}\n\n"
        "اختر إجراءً:"
    )


def admin_panel_kb():
    kb = types.InlineKeyboardMarkup(row_width=2)
    xo_on = FEATURES["xo_enabled"]
    pc_on = FEATURES["popcalc_enabled"]
    tc_on = FEATURES["teamcalc_enabled"]
    xo_btn = ("🔒 إيقاف XO" if xo_on else "✅ تفعيل XO")
    pc_btn = ("🔒 إيقاف الفردية" if pc_on else "✅ تفعيل الفردية")
    tc_btn = ("🔒 إيقاف الفريق" if tc_on else "✅ تفعيل الفريق")

    kb.add(types.InlineKeyboardButton("━━━ ⚙️ الميزات ━━━", callback_data="admin_noop"))
    kb.row(
        types.InlineKeyboardButton(xo_btn, callback_data="admin_toggle_xo"),
        types.InlineKeyboardButton(pc_btn, callback_data="admin_toggle_popcalc"),
    )
    kb.add(types.InlineKeyboardButton(tc_btn, callback_data="admin_toggle_teamcalc"))

    kb.add(types.InlineKeyboardButton("━━━ 👥 اللاعبون ━━━", callback_data="admin_noop"))
    kb.row(
        types.InlineKeyboardButton("👥 الكل", callback_data="admin_users_0"),
        types.InlineKeyboardButton("🔍 بحث", callback_data="admin_search"),
    )
    kb.row(
        types.InlineKeyboardButton("🚫 المحظورون", callback_data="admin_banned"),
        types.InlineKeyboardButton("📋 تفصيلية Top 25", callback_data="admin_leaderboard"),
    )

    kb.add(types.InlineKeyboardButton("━━━ ✨ أزرار جديدة ━━━", callback_data="admin_noop"))
    kb.row(
        types.InlineKeyboardButton("👋 رسالة الترحيب", callback_data="admin_cfg_start"),
        types.InlineKeyboardButton("🎁 نص الجوائز", callback_data="admin_cfg_prizes"),
    )
    kb.row(
        types.InlineKeyboardButton("🔢 حد المباريات", callback_data="admin_cfg_limit"),
        types.InlineKeyboardButton("🎯 المهام اليومية", callback_data="admin_cfg_quest"),
    )

    kb.add(types.InlineKeyboardButton("━━━ 🛠 النظام ━━━", callback_data="admin_noop"))
    kb.row(
        types.InlineKeyboardButton("📦 Backup", callback_data="admin_backup"),
        types.InlineKeyboardButton("📊 الحالة", callback_data="admin_status"),
    )
    kb.add(types.InlineKeyboardButton("🧹 تصفير كل النقاط", callback_data="admin_reset_ask"))
    kb.add(types.InlineKeyboardButton("📖 إدارة أقسام المساعدة", callback_data="admin_help_list"))
    return kb


@bot.message_handler(commands=["backup"])
@private_only
def cmd_backup(message):
    uid = message.chat.id
    if not is_admin(uid):
        return
    _send_backup(uid)


@bot.message_handler(commands=["2fa_setup", "twofa", "2fa"])
@private_only
def cmd_2fa_setup(message):
    uid = message.chat.id
    if not is_admin(uid):
        return
    if totp_enabled():
        uri = totp_provisioning_uri(account_name=str(uid), issuer="XO Bot")
        text = (
            "🔐 *المصادقة الثنائية مُفعّلة*\n\n"
            "الأوامر الخطرة (تصفير النقاط) تتطلب رمز 6 أرقام.\n\n"
            "لإعادة الربط بتطبيق Authenticator، استخدم الرابط:\n"
            f"`{uri}`\n\n"
            "أو ضع `TOTP_SECRET` نفسه يدوياً في التطبيق."
        )
    else:
        new_secret = generate_totp_secret()
        text = (
            "⚠️ *2FA غير مفعّل*\n\n"
            "لتفعيلها:\n"
            "1️⃣ أضف هذا المتغيّر في Render:\n"
            f"`TOTP_SECRET = {new_secret}`\n\n"
            "2️⃣ في تطبيق Authenticator: اختر *إدخال يدوي* وألصق السرّ.\n"
            "3️⃣ أعد تشغيل البوت.\n\n"
            "بعدها سيتم طلب رمز 6 أرقام عند تنفيذ /reset أو تصفير النقاط."
        )
    bot.send_message(uid, text, parse_mode="Markdown")


def _execute_2fa_action(uid, action):
    if action == "reset":
        try:
            archived, reset_n = _do_full_reset()
            bot.send_message(
                uid,
                f"✅ *تم التحقق ونُفّذ الإجراء*\n\n"
                f"• تمت أرشفة: *{archived}* لاعباً\n"
                f"• تم تصفير نقاط: *{reset_n}* مستخدماً",
                parse_mode="Markdown",
            )
        except Exception as e:
            bot.send_message(uid, f"❌ فشل التنفيذ: {e}")
    else:
        bot.send_message(uid, "⚠️ إجراء غير معروف.")


def _format_uptime(td):
    total = int(td.total_seconds())
    d, rem = divmod(total, 86400)
    h, rem = divmod(rem, 3600)
    m, s = divmod(rem, 60)
    parts = []
    if d: parts.append(f"{d}ي")
    if h: parts.append(f"{h}س")
    if m: parts.append(f"{m}د")
    if not parts: parts.append(f"{s}ث")
    return " ".join(parts)


def _build_status_text():
    now = datetime.now(timezone.utc)
    uptime = now - BOT_START_TIME

    fs_ok = False
    users_count = "-"
    fs_ping_ms = -1
    try:
        from firebase_utils import db as _db
        t0 = time_mod.time()
        agg = _db.collection("users").count().get()
        users_count = agg[0][0].value if agg else 0
        fs_ping_ms = int((time_mod.time() - t0) * 1000)
        fs_ok = True
    except Exception as e:
        print(f"⚠️ status firestore: {e}")

    bot_active = len(bot_games)
    try:
        qs = queue_size()
    except Exception:
        qs = "-"

    mem_str = "-"
    try:
        import resource
        rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        mem_mb = rss_kb / 1024
        mem_str = f"{mem_mb:.1f} MB"
    except Exception:
        pass

    fs_line = f"✅ متصل ({fs_ping_ms}ms)" if fs_ok else "❌ غير متصل"
    xo_line = "✅ مُفعّلة" if FEATURES["xo_enabled"] else "🔒 متوقفة"
    pc_line = "✅ مُفعّلة" if FEATURES["popcalc_enabled"] else "🔒 متوقفة"
    tc_line = "✅ مُفعّلة" if FEATURES["teamcalc_enabled"] else "🔒 متوقفة"

    return (
        "📊 *حالة البوت*\n\n"
        f"⏱ وقت التشغيل: *{_format_uptime(uptime)}*\n"
        f"🕒 منذ: `{BOT_START_TIME.strftime('%Y-%m-%d %H:%M UTC')}`\n"
        f"💾 الذاكرة: *{mem_str}*\n\n"
        f"🔥 Firestore: {fs_line}\n"
        f"👥 المستخدمون: *{users_count}*\n\n"
        f"🎮 مباريات ضد البوت (نشطة): *{bot_active}*\n"
        f"📭 طابور Quick Match: *{qs}*\n\n"
        f"🎯 لعبة XO: {xo_line}\n"
        f"🔥 حاسبة المعركة الفردية: {pc_line}\n"
        f"⚔️ حاسبة معركة الفريق: {tc_line}"
    )


@bot.message_handler(commands=["status"])
@private_only
def cmd_status(message):
    uid = message.chat.id
    if not is_admin(uid):
        return
    bot.send_message(uid, _build_status_text(), parse_mode="Markdown")


def _send_backup(uid):
    import io
    try:
        bot.send_chat_action(uid, "upload_document")
    except Exception:
        pass
    try:
        data = export_all()
        counts = {k: len(v) for k, v in data.items()}
        payload = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        fname = f"backup_{datetime.now(timezone.utc).strftime('%Y-%m-%d_%H%M%S')}.json"
        f = io.BytesIO(payload)
        f.name = fname
        caption = (
            "📦 *نسخة احتياطية*\n\n"
            + "\n".join([f"• {k}: *{v}*" for k, v in counts.items()])
            + f"\n\n🕒 {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
        )
        bot.send_document(uid, f, caption=caption, parse_mode="Markdown",
                          visible_file_name=fname)
    except Exception as e:
        bot.send_message(uid, f"❌ فشل التصدير: {e}")


# ============================
# === إدارة اللاعبين — عرض ===
# ============================

def _user_line_short(u, idx=None):
    name = u.get("name", "لاعب")
    uid_ = u.get("user_id") or u.get("id") or "—"
    uname = u.get("username") or ""
    pts = u.get("points", 0)
    tags = []
    if u.get("banned"):
        tags.append("🚫")
    if u.get("muted"):
        tags.append("🔇")
    if (u.get("warnings") or 0) > 0:
        tags.append(f"⚠️{u.get('warnings')}")
    tag_str = (" " + "".join(tags)) if tags else ""
    uname_str = f" `@{uname}`" if uname else ""
    prefix = f"{idx}. " if idx is not None else ""
    return f"{prefix}*{_md_escape(name)}*{tag_str}\n   🆔 `{uid_}` |{uname_str} | نقاط: *{pts}*"


def _send_users_page(uid, mid, page, edit=False):
    users = list_all_users()
    total = len(users)
    per = USERS_PAGE_SIZE
    pages = max(1, (total + per - 1) // per)
    page = max(0, min(page, pages - 1))
    start = page * per
    chunk = users[start:start + per]
    header = (
        f"👥 *كل المستخدمين المسجّلين*\n"
        f"الإجمالي: *{total}* | الصفحة {page + 1}/{pages}\n\n"
    )
    parts = []
    for i, u in enumerate(chunk):
        try:
            parts.append(_user_line_short(u, idx=start + i + 1))
        except Exception as e:
            parts.append(f"{start + i + 1}. _تعذر عرض المستخدم_ (`{u.get('id','?')}`)")
            print(f"⚠️ user_line: {e}")
    body = "\n\n".join(parts) if parts else "_لا يوجد مستخدمون._"
    text = header + body
    if len(text) > 3800:
        text = text[:3800] + "\n\n…"

    kb = types.InlineKeyboardMarkup(row_width=2)
    for u in chunk:
        tid = u.get("user_id") or u.get("id")
        kb.add(types.InlineKeyboardButton(
            f"👤 {(u.get('name') or 'لاعب')[:22]}",
            callback_data=f"admin_u_{tid}",
        ))
    nav = []
    if page > 0:
        nav.append(types.InlineKeyboardButton("◀️ السابق", callback_data=f"admin_users_{page-1}"))
    if page < pages - 1:
        nav.append(types.InlineKeyboardButton("التالي ▶️", callback_data=f"admin_users_{page+1}"))
    if nav:
        kb.row(*nav)
    kb.add(types.InlineKeyboardButton("🔙 رجوع للوحة", callback_data="admin_back"))

    try:
        if edit:
            bot.edit_message_text(text, uid, mid, reply_markup=kb, parse_mode="Markdown")
        else:
            bot.send_message(uid, text, reply_markup=kb, parse_mode="Markdown")
    except Exception as e:
        print(f"⚠️ users_page Markdown fail: {e}")
        plain = text.replace("*", "").replace("_", "").replace("`", "")
        try:
            if edit:
                bot.edit_message_text(plain, uid, mid, reply_markup=kb)
            else:
                bot.send_message(uid, plain, reply_markup=kb)
        except Exception as e2:
            print(f"⚠️ users_page plain fail: {e2}")
            bot.send_message(uid, f"❌ خطأ: {e2}")


def _render_banned_list(banned):
    if not banned:
        return "🚫 *قائمة المحظورين*\n\n_لا يوجد محظورون حالياً._"
    lines = [f"🚫 *قائمة المحظورين* — العدد: *{len(banned)}*\n"]
    for i, u in enumerate(banned, 1):
        until = u.get("ban_until")
        kind = "⛔ دائم" if not until else f"⏳ حتى {_fmt_ban_until(until)}"
        reason = u.get("ban_reason") or "—"
        lines.append(
            f"{i}. *{_md_escape(u.get('name','لاعب'))}* — {kind}\n"
            f"   🆔 `{u.get('user_id') or u.get('id')}` | 📝 {reason}"
        )
    return "\n".join(lines)


def _send_admin_search_results(uid, query, results):
    if not results:
        text = f"🔍 *البحث*: `{query}`\n\nلا توجد نتائج."
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("🔙 رجوع للوحة", callback_data="admin_back"))
        bot.send_message(uid, text, reply_markup=kb, parse_mode="Markdown")
        return
    text = f"🔍 *نتائج البحث*: `{query}` — عددها {len(results)}\n\n"
    text += "\n\n".join(_user_line_short(u, idx=i+1) for i, u in enumerate(results[:20]))
    kb = types.InlineKeyboardMarkup(row_width=1)
    for u in results[:10]:
        tid = u.get("user_id") or u.get("id")
        kb.add(types.InlineKeyboardButton(
            f"👤 {u.get('name','لاعب')[:28]}",
            callback_data=f"admin_u_{tid}",
        ))
    kb.add(types.InlineKeyboardButton("🔙 رجوع للوحة", callback_data="admin_back"))
    bot.send_message(uid, text, reply_markup=kb, parse_mode="Markdown")


def _send_user_profile(uid, mid, target_id, edit=False):
    u = get_user_doc(target_id)
    if not u:
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("🔙 رجوع", callback_data="admin_back"))
        txt = f"❌ لا يوجد لاعب بالـID `{target_id}`"
        if edit:
            bot.edit_message_text(txt, uid, mid, reply_markup=kb, parse_mode="Markdown")
        else:
            bot.send_message(uid, txt, reply_markup=kb, parse_mode="Markdown")
        return

    name = u.get("name", "لاعب")
    uid_ = u.get("user_id") or u.get("id")
    uname = u.get("username") or "—"
    pts = u.get("points", 0)
    w = u.get("wins", 0); l = u.get("losses", 0); d = u.get("draws", 0)
    pw = u.get("pvp_wins", 0); pl = u.get("pvp_losses", 0); pd = u.get("pvp_draws", 0)
    warnings = u.get("warnings", 0)
    banned = u.get("banned")
    muted = u.get("muted")
    mt = u.get("matches_today", 0)

    status = []
    if banned:
        until = u.get("ban_until")
        status.append("🚫 محظور " + (f"(حتى {_fmt_ban_until(until)})" if until else "(دائم)"))
        if u.get("ban_reason"):
            status.append(f"📝 سبب الحظر: {u.get('ban_reason')}")
    if muted:
        status.append("🔇 مكتوم")
    if warnings:
        status.append(f"⚠️ تحذيرات: *{warnings}*")

    log = get_action_log(target_id)[-5:]
    log_lines = []
    for entry in reversed(log):
        ts = (entry.get("ts") or "")[:16].replace("T", " ")
        log_lines.append(f"• `{ts}` — {entry.get('type','?')} — {entry.get('reason','') or '—'}")

    text = (
        f"👤 *{_md_escape(name)}*\n"
        f"🆔 `{uid_}` | 👤 `@{uname}`\n\n"
        f"🏆 *النقاط*: *{pts}*\n"
        f"📊 الإجمالي: ف{w} / خ{l} / ت{d}\n"
        f"🆚 PvP: ف{pw} / خ{pl} / ت{pd}\n"
        f"📅 مباريات اليوم: *{mt}*\n"
    )
    if status:
        text += "\n" + "\n".join(status) + "\n"
    if log_lines:
        text += "\n📜 *آخر الإجراءات:*\n" + "\n".join(log_lines)

    kb = types.InlineKeyboardMarkup(row_width=2)
    if banned:
        kb.add(types.InlineKeyboardButton("✅ رفع الحظر", callback_data=f"admin_act_unban_{uid_}"))
    else:
        kb.row(
            types.InlineKeyboardButton("🚫 حظر دائم", callback_data=f"admin_act_banperm_{uid_}"),
            types.InlineKeyboardButton("⏰ حظر 24س", callback_data=f"admin_act_ban24_{uid_}"),
        )
        kb.add(types.InlineKeyboardButton("📅 حظر أسبوع", callback_data=f"admin_act_ban168_{uid_}"))
    if muted:
        kb.add(types.InlineKeyboardButton("🔊 إلغاء الكتم", callback_data=f"admin_act_unmute_{uid_}"))
    else:
        kb.add(types.InlineKeyboardButton("🔇 كتم", callback_data=f"admin_act_mute_{uid_}"))
    kb.row(
        types.InlineKeyboardButton("⚠️ تحذير", callback_data=f"admin_act_warn_{uid_}"),
        types.InlineKeyboardButton("🧹 تصفير التحذيرات", callback_data=f"admin_act_clearw_{uid_}"),
    )
    kb.row(
        types.InlineKeyboardButton("➕ 5 نقاط", callback_data=f"admin_act_pts+5_{uid_}"),
        types.InlineKeyboardButton("➖ 5 نقاط", callback_data=f"admin_act_pts-5_{uid_}"),
    )
    kb.add(types.InlineKeyboardButton("📜 السجل الكامل", callback_data=f"admin_act_log_{uid_}"))
    kb.add(types.InlineKeyboardButton("🔙 رجوع للوحة", callback_data="admin_back"))

    if edit:
        try:
            bot.edit_message_text(text, uid, mid, reply_markup=kb, parse_mode="Markdown")
        except Exception:
            bot.send_message(uid, text, reply_markup=kb, parse_mode="Markdown")
    else:
        bot.send_message(uid, text, reply_markup=kb, parse_mode="Markdown")


def _handle_admin_action(call, data):
    uid = call.message.chat.id
    mid = call.message.message_id
    parts = data.split("_")
    if len(parts) < 4:
        return
    kind = parts[2]
    target = parts[-1]
    try:
        tid = int(target)
    except Exception:
        tid = target

    done = ""
    try:
        if kind == "unban":
            unban_user(tid, by=uid); done = "✅ تم رفع الحظر"
        elif kind == "banperm":
            ban_user(tid, reason="حظر دائم من المالك", duration_hours=None, by=uid)
            done = "🚫 حظر دائم"
        elif kind == "ban24":
            ban_user(tid, reason="حظر 24 ساعة", duration_hours=24, by=uid)
            done = "⏰ حظر 24 ساعة"
        elif kind == "ban168":
            ban_user(tid, reason="حظر أسبوع", duration_hours=168, by=uid)
            done = "📅 حظر أسبوع"
        elif kind == "mute":
            mute_user(tid, by=uid); done = "🔇 مكتوم"
        elif kind == "unmute":
            unmute_user(tid, by=uid); done = "🔊 فُكّ الكتم"
        elif kind == "warn":
            n = warn_user(tid, reason="تحذير من المالك", by=uid)
            done = f"⚠️ تحذير ({n})"
        elif kind == "clearw":
            clear_warnings(tid, by=uid); done = "🧹 صُفّرت التحذيرات"
        elif kind.startswith("pts"):
            delta = int(kind[3:])
            adjust_points(tid, delta, reason="تعديل يدوي", by=uid)
            done = f"{'➕' if delta>=0 else '➖'} {abs(delta)} نقاط"
        elif kind == "log":
            _send_full_log(uid, mid, tid); return
        try:
            bot.answer_callback_query(call.id, done or "تم")
        except Exception:
            pass
        _send_user_profile(uid, mid, tid, edit=True)
    except Exception as e:
        try:
            bot.answer_callback_query(call.id, f"❌ {e}", show_alert=True)
        except Exception:
            pass


def _send_full_log(uid, mid, target_id):
    log = get_action_log(target_id)
    if not log:
        text = "📜 *السجل فارغ*"
    else:
        lines = ["📜 *السجل الكامل* (آخر 30)\n"]
        for entry in reversed(log):
            ts = (entry.get("ts") or "")[:16].replace("T", " ")
            by = entry.get("by") or "-"
            lines.append(
                f"• `{ts}` — *{entry.get('type','?')}*\n"
                f"   📝 {entry.get('reason','') or '—'} | by `{by}`"
            )
        text = "\n".join(lines)
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("🔙 رجوع للبروفايل",
                                      callback_data=f"admin_u_{target_id}"))
    kb.add(types.InlineKeyboardButton("🔙 رجوع للوحة", callback_data="admin_back"))
    try:
        bot.edit_message_text(text, uid, mid, reply_markup=kb, parse_mode="Markdown")
    except Exception:
        bot.send_message(uid, text, reply_markup=kb, parse_mode="Markdown")


@bot.message_handler(commands=["reset"])
@private_only
def cmd_reset(message):
    uid = message.chat.id
    if not is_admin(uid):
        return
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("✅ نعم، صفّر الآن", callback_data="admin_reset_confirm"),
        types.InlineKeyboardButton("❌ إلغاء", callback_data="admin_reset_cancel"),
    )
    bot.send_message(
        uid,
        "⚠️ *تحذير*\n\nسيتم أرشفة أفضل 25 لاعباً في الموسم الحالي "
        "ثم *تصفير نقاط جميع المستخدمين*.\n\nهل أنت متأكد؟",
        reply_markup=kb, parse_mode="Markdown",
    )


def _do_full_reset():
    now = datetime.now(timezone.utc)
    top = get_leaderboard(25)
    target = last_scheduled_reset(now)
    season_id = target.strftime("%G-W%V") + "-manual-" + now.strftime("%H%M%S")
    archive_season(season_id, now, top)
    n = reset_all_points()
    set_last_reset(now)
    return len(top), n


def render_dynamic_help(active_tab="default"):
    """يرسم صفحة المساعدة الديناميكية من Firestore."""
    sections = get_help_sections()
    kb = types.InlineKeyboardMarkup(row_width=2)

    if not sections:
        kb.add(types.InlineKeyboardButton("🔙 رجوع", callback_data="back_main"))
        return "📖 *كيفية اللعب*\n\nلا توجد شروحات حالياً. سيقوم المالك بإضافتها قريباً.", kb

    tabs = sorted(sections.items())
    if active_tab == "default" and tabs:
        active_tab = tabs[0][0]

    row = []
    text = "📖 *كيفية اللعب*\n\n"
    for tab_id, data in tabs:
        label = data.get("title", "قسم")
        btn_text = ("• " + label) if tab_id == active_tab else label
        row.append(types.InlineKeyboardButton(btn_text, callback_data=f"help_{tab_id}"))

        if tab_id == active_tab:
            text += f"*{label}*\n\n{data.get('content', '')}"

        if len(row) == 2:
            kb.row(*row)
            row = []
    if row:
        kb.row(*row)

    kb.add(types.InlineKeyboardButton("🏠 القائمة", callback_data="back_main"))
    return text, kb


# ============================
# === انضمام لتحدي PvP ===
# ============================

def handle_join_game(uid, name, game_id):
    if _has_active_game_block(uid):
        return
    if not _enforce_daily_limit(uid):
        return
    game = get_game(game_id)
    if not game:
        bot.send_message(uid, "❌ التحدّي غير موجود أو انتهت صلاحيته.",
                         reply_markup=main_menu_kb())
        return

    if game["status"] != "waiting":
        bot.send_message(uid, "⚠️ هذا التحدّي بدأ بالفعل أو انتهى.",
                         reply_markup=main_menu_kb())
        return

    if game["player_x_id"] == uid:
        bot.send_message(uid, "😅 لا يمكنك الانضمام لتحدٍّ أنشأته بنفسك. شارك الرابط مع صديق.",
                         reply_markup=main_menu_kb())
        return

    creator_id = game.get("player_x_id")
    if creator_id and not _enforce_daily_limit(creator_id):
        bot.send_message(
            uid,
            "⚠️ منشئ هذا التحدّي وصل الحد اليومي للمباريات. اطلب منه المحاولة لاحقاً.",
        )
        return

    get_or_create_user(uid, name)

    board = board_list(game["board"])
    sent = bot.send_message(
        uid,
        fmt_pvp_game({**game, "player_o_id": uid, "player_o_name": name,
                      "status": "playing"}, uid),
        reply_markup=board_kb(board, f"pvp:{game_id}"),
        parse_mode="Markdown",
    )

    update_game(game_id, {
        "player_o_id": uid,
        "player_o_name": name,
        "status": "playing",
        "o_chat_id": uid,
        "o_msg_id": sent.message_id,
    })

    game = get_game(game_id)
    try:
        bot.edit_message_text(
            fmt_pvp_game(game, game["player_x_id"]),
            chat_id=game["x_chat_id"],
            message_id=game["x_msg_id"],
            reply_markup=board_kb(board_list(game["board"]), f"pvp:{game_id}"),
            parse_mode="Markdown",
        )
    except Exception as e:
        print(f"⚠️ فشل تحديث رسالة X: {e}")


# ============================
# === معالجة الأزرار ===
# ============================

@bot.callback_query_handler(func=lambda c: True)
def on_callback(call):
    try:
        bot.answer_callback_query(call.id)
    except Exception:
        pass
    try:
        _dispatch(call)
    except Exception as e:
        msg = str(e)
        if "message is not modified" in msg:
            return
        print(f"❌ خطأ: {e}")
        try:
            bot.send_message(call.from_user.id, "❌ حدث خطأ، حاول مرة أخرى")
        except Exception:
            pass


def _dispatch(call):
    data = call.data or ""

    if call.message is None:
        if data.startswith("pvp:"):
            handle_pvp_action(call, data)
        else:
            try:
                bot.answer_callback_query(call.id, "هذا الزر لا يعمل هنا")
            except Exception:
                pass
        return

    uid = call.message.chat.id
    mid = call.message.message_id

    # === حارس الحظر/الكتم ===
    if not require_not_banned_call(call):
        return

    # === حارس إيقاف اللعبة — المساعدة مستثناة ===
    game_actions = [
        "menu_bot", "menu_pvp", "quick_match", "pvp_create",
        "bot_start_easy", "bot_start_hard", "qm_cancel",
        "menu_stats", "menu_leaderboard", "menu_last_season",
    ]
    if data in game_actions or data.startswith(("bot:", "pvp:", "gchal:", "resume_", "resign_")):
        if not FEATURES["xo_enabled"] and not is_admin(call.from_user.id):
            try:
                bot.answer_callback_query(call.id, "🔒 الميزة متوقفة مؤقتاً", show_alert=True)
            except Exception:
                pass
            return

    # === تحدّي مجموعة ===
    if data.startswith("gchal:"):
        handle_group_challenge(call, data)
        return

    # === حارس اليوزر ===
    if not (call.from_user.username or "").strip():
        if not (ADMIN_ID and int(call.from_user.id) == int(ADMIN_ID)):
            try:
                bot.answer_callback_query(
                    call.id,
                    "🔒 أضف يوزر (Username) في تليجرام ثم أرسل /start",
                    show_alert=True,
                )
            except Exception:
                pass
            return

    # === أوامر المالك ===
    if data.startswith("admin_"):
        if not is_admin(uid):
            try:
                bot.answer_callback_query(call.id, "⛔ هذا الإجراء للمالك فقط")
            except Exception:
                pass
            return

        if data == "admin_reset_ask":
            kb = types.InlineKeyboardMarkup(row_width=2)
            kb.add(
                types.InlineKeyboardButton("✅ نعم، صفّر الآن", callback_data="admin_reset_confirm"),
                types.InlineKeyboardButton("❌ إلغاء", callback_data="admin_reset_cancel"),
            )
            bot.edit_message_text(
                "⚠️ *تحذير*\n\nسيتم أرشفة أفضل 25 لاعباً "
                "ثم *تصفير نقاط جميع المستخدمين*.\n\nهل أنت متأكد؟",
                uid, mid, reply_markup=kb, parse_mode="Markdown",
            )
            return

        if data == "admin_reset_cancel":
            bot.edit_message_text("❎ تم الإلغاء.", uid, mid)
            return

        if data == "admin_reset_confirm":
            if totp_enabled():
                request_2fa(uid, "reset")
                bot.edit_message_text(
                    "🔐 *مطلوب التحقق الثنائي*\n\n"
                    "أرسل الآن *رمز 6 أرقام* من تطبيق Authenticator "
                    "للموافقة على *تصفير كل النقاط*.\n\n"
                    "_الصلاحية: دقيقتان. أرسل /cancel للإلغاء._",
                    uid, mid, parse_mode="Markdown",
                )
                return
            try:
                archived, reset_n = _do_full_reset()
                bot.edit_message_text(
                    f"✅ *تمت إعادة التعيين بنجاح*\n\n"
                    f"• تمت أرشفة: *{archived}* لاعباً\n"
                    f"• تم تصفير نقاط: *{reset_n}* مستخدماً",
                    uid, mid, parse_mode="Markdown",
                )
            except Exception as e:
                bot.edit_message_text(f"❌ فشل التنفيذ: {e}", uid, mid)
            return

        if data == "admin_toggle_xo":
            FEATURES["xo_enabled"] = not FEATURES["xo_enabled"]
            try:
                set_flag("xo_enabled", FEATURES["xo_enabled"])
            except Exception as e:
                print(f"⚠️ set_flag xo: {e}")
            bot.edit_message_text(
                admin_panel_text(), uid, mid,
                reply_markup=admin_panel_kb(), parse_mode="Markdown",
            )
            return

        if data == "admin_toggle_popcalc":
            FEATURES["popcalc_enabled"] = not FEATURES["popcalc_enabled"]
            try:
                set_flag("popcalc_enabled", FEATURES["popcalc_enabled"])
            except Exception as e:
                print(f"⚠️ set_flag popcalc: {e}")
            bot.edit_message_text(
                admin_panel_text(), uid, mid,
                reply_markup=admin_panel_kb(), parse_mode="Markdown",
            )
            return

        if data == "admin_toggle_teamcalc":
            FEATURES["teamcalc_enabled"] = not FEATURES["teamcalc_enabled"]
            try:
                set_flag("teamcalc_enabled", FEATURES["teamcalc_enabled"])
            except Exception as e:
                print(f"⚠️ set_flag teamcalc: {e}")
            bot.edit_message_text(
                admin_panel_text(), uid, mid,
                reply_markup=admin_panel_kb(), parse_mode="Markdown",
            )
            return

        if data == "admin_backup":
            try:
                bot.answer_callback_query(call.id, "📦 جاري التحضير...")
            except Exception:
                pass
            _send_backup(uid)
            return

        if data == "admin_leaderboard":
            board = get_leaderboard(25)
            text = render_admin_leaderboard(board)
            kb = types.InlineKeyboardMarkup()
            kb.add(types.InlineKeyboardButton("🔙 رجوع", callback_data="admin_back"))
            bot.edit_message_text(text, uid, mid,
                                  reply_markup=kb, parse_mode="Markdown")
            return

        if data == "admin_back":
            bot.edit_message_text(admin_panel_text(), uid, mid,
                                  reply_markup=admin_panel_kb(), parse_mode="Markdown")
            return

        if data == "admin_noop":
            try:
                bot.answer_callback_query(call.id)
            except Exception:
                pass
            return

        if data == "admin_status":
            kb = types.InlineKeyboardMarkup()
            kb.add(types.InlineKeyboardButton("🔄 تحديث", callback_data="admin_status"))
            kb.add(types.InlineKeyboardButton("🔙 رجوع", callback_data="admin_back"))
            try:
                bot.edit_message_text(_build_status_text(), uid, mid,
                                      reply_markup=kb, parse_mode="Markdown")
            except Exception:
                pass
            try:
                bot.answer_callback_query(call.id, "✅ مُحدَّث")
            except Exception:
                pass
            return

        if data.startswith("admin_users_"):
            try:
                page = int(data.split("_")[-1])
            except Exception:
                page = 0
            _send_users_page(uid, mid, page, edit=True)
            return

        if data == "admin_search":
            admin_search_waiting[uid] = True
            kb = types.InlineKeyboardMarkup()
            kb.add(types.InlineKeyboardButton("❌ إلغاء", callback_data="admin_back"))
            bot.edit_message_text(
                "🔍 *بحث عن لاعب*\n\n"
                "أرسل الآن أحد الخيارات:\n"
                "• ID رقمي (مثل `123456789`)\n"
                "• يوزر (مثل `@user_name`)\n"
                "• جزء من الاسم (مثل `بو`)\n",
                uid, mid, reply_markup=kb, parse_mode="Markdown",
            )
            return

        if data == "admin_banned":
            banned = list_banned_users()
            text = _render_banned_list(banned)
            kb = types.InlineKeyboardMarkup(row_width=1)
            for u in banned[:10]:
                kb.add(types.InlineKeyboardButton(
                    f"👤 {u.get('name','لاعب')}",
                    callback_data=f"admin_u_{u.get('user_id') or u.get('id')}",
                ))
            kb.add(types.InlineKeyboardButton("🔙 رجوع", callback_data="admin_back"))
            bot.edit_message_text(text, uid, mid,
                                  reply_markup=kb, parse_mode="Markdown")
            return

        if data.startswith("admin_u_"):
            target = data[len("admin_u_"):]
            _send_user_profile(uid, mid, target, edit=True)
            return

        if data.startswith("admin_act_"):
            _handle_admin_action(call, data)
            return

        # --- إدارة أقسام المساعدة ---
        if data == "admin_help_list":
            sections = get_help_sections()
            text = "📖 *إدارة أقسام المساعدة*\n\nاختر قسماً لتعديله/حذفه، أو أضف قسماً جديداً:"
            kb = types.InlineKeyboardMarkup(row_width=1)
            for tid, sec in sections.items():
                kb.add(types.InlineKeyboardButton(f"✏️ {sec.get('title')}", callback_data=f"admin_help_edit_{tid}"))
            kb.add(types.InlineKeyboardButton("➕ إضافة قسم جديد", callback_data="admin_help_new"))
            kb.add(types.InlineKeyboardButton("🔙 رجوع للوحة", callback_data="admin_back"))
            bot.edit_message_text(text, uid, mid, reply_markup=kb, parse_mode="Markdown")
            return

        if data == "admin_help_new":
            admin_help_state[uid] = {"action": "wait_title", "msg_id": mid}
            kb = types.InlineKeyboardMarkup()
            kb.add(types.InlineKeyboardButton("❌ إلغاء", callback_data="admin_help_list"))
            bot.edit_message_text(
                "➕ *إضافة قسم جديد*\n\nأرسل الآن **عنوان الزر** (مثال: القوانين 📜):",
                uid, mid, reply_markup=kb, parse_mode="Markdown",
            )
            return

        if data.startswith("admin_help_edit_"):
            tid = data.replace("admin_help_edit_", "")
            sec = get_help_sections().get(tid, {})
            text = f"✏️ *تعديل قسم:* {sec.get('title')}\n\nماذا تريد أن تفعل؟"
            kb = types.InlineKeyboardMarkup(row_width=2)
            kb.add(types.InlineKeyboardButton("📝 تعديل المحتوى", callback_data=f"admin_help_settext_{tid}"))
            kb.add(types.InlineKeyboardButton("❌ حذف القسم", callback_data=f"admin_help_del_{tid}"))
            kb.add(types.InlineKeyboardButton("🔙 رجوع", callback_data="admin_help_list"))
            bot.edit_message_text(text, uid, mid, reply_markup=kb, parse_mode="Markdown")
            return

        if data.startswith("admin_help_settext_"):
            tid = data.replace("admin_help_settext_", "")
            admin_help_state[uid] = {"action": "wait_content", "tab_id": tid, "msg_id": mid}
            kb = types.InlineKeyboardMarkup()
            kb.add(types.InlineKeyboardButton("❌ إلغاء", callback_data="admin_help_list"))
            bot.edit_message_text(
                "📝 *تعديل المحتوى*\n\nأرسل الآن **النص الجديد** لهذا القسم:",
                uid, mid, reply_markup=kb, parse_mode="Markdown",
            )
            return

        if data.startswith("admin_help_del_"):
            tid = data.replace("admin_help_del_", "")
            delete_help_section(tid)
            try:
                bot.answer_callback_query(call.id, "✅ تم الحذف")
            except Exception:
                pass
            # إعادة عرض قائمة الأقسام بعد الحذف
            sections = get_help_sections()
            text = "📖 *إدارة أقسام المساعدة*\n\nاختر قسماً لتعديله/حذفه، أو أضف قسماً جديداً:"
            kb = types.InlineKeyboardMarkup(row_width=1)
            for t_id, sec in sections.items():
                kb.add(types.InlineKeyboardButton(f"✏️ {sec.get('title')}", callback_data=f"admin_help_edit_{t_id}"))
            kb.add(types.InlineKeyboardButton("➕ إضافة قسم جديد", callback_data="admin_help_new"))
            kb.add(types.InlineKeyboardButton("🔙 رجوع للوحة", callback_data="admin_back"))
            try:
                bot.edit_message_text(text, uid, mid, reply_markup=kb, parse_mode="Markdown")
            except Exception:
                pass
            return

        # --- معالجة أزرار الإعدادات الجديدة ---
        cfg_map = {
"admin_cfg_start": ("start_msg", "أرسل الآن نص **رسالة الترحيب** الجديد:\n(استخدم `{name}` للاسم، `{uid}` للآيدي، `{username}` لليوزر)"),            "admin_cfg_prizes": ("prizes_text", "أرسل الآن نص **الجوائز** التي تظهر في لوحة الشرف:"),
            "admin_cfg_limit": ("daily_limit", "أرسل الآن **رقم** الحد اليومي للمباريات (مثال: 100):"),
            "admin_cfg_quest": ("daily_quest", "أرسل الآن نص **المهمة اليومية** (ستظهر للجميع في البداية):")
        }
        if data in cfg_map:
            key, msg = cfg_map[data]
            admin_help_state[uid] = {"action": "wait_config", "key": key, "msg_id": mid}
            kb = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("❌ إلغاء", callback_data="admin_back"))
            try:
                bot.edit_message_text(msg, uid, mid, reply_markup=kb, parse_mode="Markdown")
            except Exception:
                pass
            return

    # === قوائم عامة ===
    if data == "back_main":
        if call.message and call.message.chat.type != "private":
            try:
                bot.answer_callback_query(call.id, "🏠 زر القائمة مخصص للعودة في المحادثة الخاصة فقط", show_alert=True)
            except Exception:
                pass
            return

        if not FEATURES["xo_enabled"] and not is_admin(uid):
            try:
                bot.answer_callback_query(call.id, "🔒 لعبة XO متوقفة مؤقتاً")
            except Exception:
                pass
            bot.edit_message_text("🏠 القائمة الرئيسية:\n(لعبة XO متوقفة حالياً)", uid, mid, reply_markup=start_menu_kb())
            return

        bot_games.pop(uid, None)
        bot.edit_message_text("🎮 *لعبة XO*\n\nاختر:", uid, mid,
                              reply_markup=main_menu_kb(), parse_mode="Markdown")
        return

    if data == "back_start":
        bot_games.pop(uid, None)
        popcalc_sessions.pop(uid, None)
        name = call.from_user.first_name or "لاعب"
        username = call.from_user.username or ""
        text = (
            f"👋 أهلاً بعودتك *{_md_escape(name)}*!\n\n"
            f"🆔 ID: `{uid}`\n"
            f"👤 اليوزر: `@{username}`\n\n"
            "اختر من القائمة:"
        )
        bot.edit_message_text(text, uid, mid,
                              reply_markup=start_menu_kb(), parse_mode="Markdown")
        return

    if data == "open_xo":
        if not FEATURES["xo_enabled"] and not is_admin(uid):
            try:
                bot.send_message(uid, "🔒 لعبة XO متوقفة مؤقتاً")
            except Exception:
                pass
            return
        bot.edit_message_text("🎮 *لعبة XO*\n\nاختر:", uid, mid,
                              reply_markup=main_menu_kb(), parse_mode="Markdown")
        return

    if data == "open_calcs":
        bot.edit_message_text(
            "🧮 *الحاسبات*\n\nاختر الحاسبة:",
            uid, mid, reply_markup=calcs_menu_kb(), parse_mode="Markdown",
        )
        return

    if data == "open_popcalc":
        if not FEATURES["popcalc_enabled"] and not is_admin(uid):
            try:
                bot.send_message(uid, "🔒 حاسبة المعركة الفردية متوقفة مؤقتاً")
            except Exception:
                pass
            return
        popcalc_sessions.pop(uid, None)
        bot.edit_message_text(
            popcalc_intro_text(), uid, mid,
            reply_markup=popcalc_menu_kb(), parse_mode="Markdown",
        )
        return

    if data == "popcalc_new":
        popcalc_sessions[uid] = {"stage": "your_pop", "msg_id": mid, "mode": "pop"}
        bot.edit_message_text(
            "🔥 *حاسبة المعركة الفردية*\n\n"
            "1️⃣ أرسل شعبيتك الآن كرقم (مثال: `50000`)",
            uid, mid,
            reply_markup=popcalc_cancel_kb(), parse_mode="Markdown",
        )
        return

    if data == "popcalc_tiers":
        bot.edit_message_text(
            popcalc_tiers_text(), uid, mid,
            reply_markup=popcalc_back_kb(), parse_mode="Markdown",
        )
        return

    if data == "popcalc_cancel":
        popcalc_sessions.pop(uid, None)
        bot.edit_message_text(
            popcalc_intro_text(), uid, mid,
            reply_markup=popcalc_menu_kb(), parse_mode="Markdown",
        )
        return

    if data == "open_teamcalc":
        if not FEATURES["teamcalc_enabled"] and not is_admin(uid):
            try:
                bot.send_message(uid, "🔒 حاسبة معركة الفريق متوقفة مؤقتاً")
            except Exception:
                pass
            return
        popcalc_sessions.pop(uid, None)
        bot.edit_message_text(
            teamcalc_intro_text(), uid, mid,
            reply_markup=teamcalc_menu_kb(), parse_mode="Markdown",
        )
        return

    if data == "teamcalc_new":
        popcalc_sessions[uid] = {"stage": "your_pop", "msg_id": mid, "mode": "team"}
        bot.edit_message_text(
            "⚔️ *حاسبة معركة الفريق*\n\n"
            "1️⃣ أرسل شعبيتك الآن كرقم (مثال: `50000`)",
            uid, mid,
            reply_markup=teamcalc_cancel_kb(), parse_mode="Markdown",
        )
        return

    if data == "teamcalc_tiers":
        kb = types.InlineKeyboardMarkup(row_width=1)
        kb.add(types.InlineKeyboardButton("🔙 رجوع", callback_data="open_teamcalc"))
        bot.edit_message_text(
            teamcalc_tiers_text(), uid, mid,
            reply_markup=kb, parse_mode="Markdown",
        )
        return

    if data == "teamcalc_cancel":
        popcalc_sessions.pop(uid, None)
        bot.edit_message_text(
            teamcalc_intro_text(), uid, mid,
            reply_markup=teamcalc_menu_kb(), parse_mode="Markdown",
        )
        return

    if data == "menu_bot":
        bot.edit_message_text("🤖 اختر مستوى الصعوبة:", uid, mid, reply_markup=difficulty_kb())
        return

    if data == "menu_pvp":
        bot.edit_message_text(
            "👥 *لعب ضد صديق*\n\n"
            "أنشئ تحدّياً وشارك الرابط مع صديقك.",
            uid, mid, reply_markup=pvp_menu_kb(), parse_mode="Markdown",
        )
        return

    if data == "quick_match":
        handle_quick_match(call)
        return

    if data == "qm_cancel":
        handle_quick_match_cancel(call)
        return

    if data.startswith("resume_"):
        gid = data[len("resume_"):]
        g = get_game(gid)
        if not g or g.get("status") != "playing":
            try:
                bot.send_message(uid, "⚠️ المباراة لم تعد متاحة.")
            except Exception:
                pass
            return
        try:
            board = board_list(g["board"])
            sent = bot.send_message(
                uid,
                fmt_pvp_game(g, uid),
                reply_markup=board_kb(board, f"pvp:{gid}"),
                parse_mode="Markdown",
            )
            if uid == g.get("player_x_id"):
                update_game(gid, {"x_chat_id": uid, "x_msg_id": sent.message_id})
            elif uid == g.get("player_o_id"):
                update_game(gid, {"o_chat_id": uid, "o_msg_id": sent.message_id})
        except Exception as e:
            print(f"⚠️ resume game: {e}")
        return

    if data.startswith("resign_"):
        gid = data[len("resign_"):]
        g = get_game(gid)
        if not g or g.get("status") != "playing":
            try:
                bot.send_message(uid, "⚠️ المباراة لم تعد متاحة.")
            except Exception:
                pass
            return
        if uid == g.get("player_x_id"):
            winner = PLAYER_O
        elif uid == g.get("player_o_id"):
            winner = PLAYER_X
        else:
            try:
                bot.send_message(uid, "⚠️ لست لاعباً في هذه المباراة.")
            except Exception:
                pass
            return
        try:
            finalize_pvp(gid, winner, resigned=True)
            bot.send_message(uid, "🏳️ تم الاستسلام. يمكنك الآن بدء مباراة جديدة.")
        except Exception as e:
            print(f"⚠️ resign: {e}")
            bot.send_message(uid, "❌ تعذّر الاستسلام، حاول لاحقاً.")
        return

    if data == "menu_stats":
        user = get_user_stats(uid) or get_or_create_user(uid, call.from_user.first_name or "")
        text = render_stats(user)
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("🔙 رجوع", callback_data="back_main"))
        bot.edit_message_text(text, uid, mid, reply_markup=kb, parse_mode="Markdown")
        return

    if data == "menu_leaderboard":
        # 1. جلب التوب 5 فقط
        board = get_leaderboard(5)
        
        # 2. جلب بيانات المستخدم الحالي لأسفل القائمة
        viewer_stats = get_user_stats(uid) or {}
        viewer_pts = viewer_stats.get("points", 0)
        viewer_rank = "غير مصنف"
        
        if viewer_pts > 0:
            found = False
            for i, u in enumerate(board):
                if str(u.get("user_id", "")) == str(uid) or str(u.get("id", "")) == str(uid):
                    viewer_rank = str(i + 1)
                    found = True
                    break
            
            if not found:
                try:
                    from firebase_utils import db
                    # حساب عدد الأشخاص اللي نقاطهم أعلى من المستخدم لنجيب تصنيفه بدقة
                    count_query = db.collection("users").where("points", ">", viewer_pts).count().get()
                    higher_users = count_query[0][0].value
                    viewer_rank = str(higher_users + 1)
                except Exception as e:
                    print(f"⚠️ error getting rank: {e}")
                    viewer_rank = "خارج التوب 5"
                    
        text = render_leaderboard(board, uid, viewer_pts, viewer_rank)
        kb = types.InlineKeyboardMarkup(row_width=1)
        kb.add(types.InlineKeyboardButton("🏅 الأسبوع السابق", callback_data="menu_last_season"))
        kb.add(types.InlineKeyboardButton("🔙 رجوع", callback_data="back_main"))
        bot.edit_message_text(text, uid, mid, reply_markup=kb, parse_mode="Markdown")
        return

    if data == "menu_last_season":
        season = get_last_season()
        text = render_last_season(season)
        kb = types.InlineKeyboardMarkup(row_width=1)
        kb.add(types.InlineKeyboardButton("🏆 لوحة الحالية", callback_data="menu_leaderboard"))
        kb.add(types.InlineKeyboardButton("🔙 رجوع", callback_data="back_main"))
        bot.edit_message_text(text, uid, mid, reply_markup=kb, parse_mode="Markdown")
        return

    if data == "menu_help":
        text, kb = render_dynamic_help("default")
        bot.edit_message_text(text, uid, mid, reply_markup=kb, parse_mode="Markdown")
        return

    if data.startswith("help_"):
        section = data[len("help_"):]
        text, kb = render_dynamic_help(section)
        try:
            bot.edit_message_text(text, uid, mid, reply_markup=kb, parse_mode="Markdown")
        except Exception:
            pass
        return

    if data in ("bot_start_easy", "bot_start_hard"):
        diff = "easy" if data == "bot_start_easy" else "hard"
        board = [EMPTY] * 9
        bot_games[uid] = {"board": board, "difficulty": diff, "msg_id": mid}
        game_view = {"board": board, "difficulty": diff, "turn": PLAYER_X}
        bot.edit_message_text(
            fmt_bot_game(game_view), uid, mid,
            reply_markup=board_kb(board, "bot"),
            parse_mode="Markdown",
        )
        return

    if data.startswith("bot:"):
        handle_bot_move(call, data)
        return

    if data == "pvp_create":
        handle_pvp_create(call)
        return

    if data.startswith("pvp:"):
        handle_pvp_action(call, data)
        return


# ============================
# === ضد البوت ===
# ============================

def handle_bot_move(call, data):
    uid = call.message.chat.id
    mid = call.message.message_id
    game = bot_games.get(uid)

    parts = data.split(":")
    action = parts[1] if len(parts) > 1 else ""

    if not game:
        bot.answer_callback_query(call.id, "لا توجد لعبة نشطة، ابدأ من جديد")
        bot.edit_message_text("القائمة الرئيسية:", uid, mid, reply_markup=main_menu_kb())
        return

    if action == "noop":
        bot.answer_callback_query(call.id)
        return

    if action == "resign":
        bot_games.pop(uid, None)
        record_result(uid, f"bot_{game['difficulty']}", "loss")
        bot.edit_message_text(
            "🏳️ انسحبت من اللعبة. احتُسبت خسارة.",
            uid, mid, reply_markup=main_menu_kb(),
        )
        return

    if action != "move":
        bot.answer_callback_query(call.id)
        return

    try:
        pos = int(parts[2])
    except (IndexError, ValueError):
        bot.answer_callback_query(call.id)
        return

    board = game["board"]
    if board[pos] != EMPTY:
        bot.answer_callback_query(call.id, "الخانة مأخوذة")
        return

    board[pos] = PLAYER_X
    result = check_winner(board)
    if result:
        finish_bot_game(uid, mid, game, board, result)
        return

    if game["difficulty"] == "easy":
        bot_pos = best_move_easy(board)
    else:
        bot_pos = best_move_hard(board, PLAYER_O, PLAYER_X)

    if bot_pos is not None:
        board[bot_pos] = PLAYER_O

    result = check_winner(board)
    if result:
        finish_bot_game(uid, mid, game, board, result)
        return

    game_view = {"board": board, "difficulty": game["difficulty"], "turn": PLAYER_X}
    bot.edit_message_text(
        fmt_bot_game(game_view), uid, mid,
        reply_markup=board_kb(board, "bot"),
        parse_mode="Markdown",
    )
    bot.answer_callback_query(call.id)


def finish_bot_game(uid, mid, game, board, result):
    diff = game["difficulty"]
    mode = f"bot_{diff}"
    if result == PLAYER_X:
        outcome = "win"
        txt = "🎉 *فزت!* أحسنت 👏"
    elif result == PLAYER_O:
        outcome = "loss"
        txt = "😔 *خسرت!* البوت فاز."
    else:
        outcome = "draw"
        txt = "🤝 *تعادل!*"

    record_result(uid, mode, outcome)
    bot_games.pop(uid, None)

    final_text = (
        f"{fmt_bot_game({'board': board, 'difficulty': diff, 'turn': PLAYER_X})}\n"
        f"{txt}"
    )
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(
        types.InlineKeyboardButton(
            f"🔁 العب مرة أخرى ({'🟢 سهل' if diff=='easy' else '🔴 صعب'})",
            callback_data=f"bot_start_{diff}",
        ),
        types.InlineKeyboardButton("🏠 القائمة", callback_data="back_main"),
    )
    bot.edit_message_text(
        final_text, uid, mid,
        reply_markup=kb, parse_mode="Markdown",
    )


# ============================
# === العب الآن (Quick Match) ===
# ============================

# التوثيق: قمنا بإزالة متغير الوقت (elapsed_sec) لنجعل الرسالة ثابتة وتعتمد على العد الصامت
def _qm_search_text(q_size):
    return (
        "🔍 *جاري البحث عن خصم...*\n\n"
        f"👥 اللاعبون في الطابور: *{q_size}*\n\n"
        f"⏳ _الرجاء الانتظار، سيتم إلغاء البحث تلقائياً بعد {QUICK_MATCH_TIMEOUT_SECONDS} ثانية._"
    )

def _qm_cancel_kb():
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("❌ إلغاء البحث", callback_data="qm_cancel"))
    return kb


def handle_quick_match(call):
    uid = call.message.chat.id
    mid = call.message.message_id
    name = call.from_user.first_name or "لاعب"
    get_or_create_user(uid, name)

    if _has_active_game_block(uid):
        return
    if not _enforce_daily_limit(uid):
        return

    try:
        opponent = queue_try_match(uid, name, uid)
    except Exception as e:
        print(f"⚠️ queue_try_match: {e}")
        bot.answer_callback_query(call.id, "تعذّر الاتصال، حاول مجدداً")
        return

    # تم تصحيح المسافات وحذف الكود المكرر أدناه
    if opponent is None:
        try:
            qsz = queue_size()
        except Exception:
            qsz = 1
            
        # التوثيق: استدعاء رسالة البحث الثابتة
        try:
            bot.edit_message_text(
                _qm_search_text(qsz), uid, mid,
                reply_markup=_qm_cancel_kb(), parse_mode="Markdown",
            )
        except Exception:
            pass

        with _qs_lock:
            quick_search_sessions[uid] = {
                "chat_id": uid, "msg_id": mid,
                "joined_at": datetime.now(timezone.utc),
                "name": name,
            }
            
        try:
            bot.answer_callback_query(call.id, "🔍 يبحث عن خصم...")
        except Exception:
            pass
        return

    _start_quick_match(
        x_player={"id": opponent["user_id"], "name": opponent["name"], "chat_id": opponent["chat_id"]},
        o_player={"id": uid, "name": name, "chat_id": uid, "msg_id": mid},
    )
    
    try:
        bot.answer_callback_query(call.id, "✅ وُجد خصم!")
    except Exception:
        pass

def handle_quick_match_cancel(call):
    uid = call.message.chat.id
    mid = call.message.message_id
    queue_remove(uid)
    with _qs_lock:
        quick_search_sessions.pop(uid, None)
    bot.edit_message_text(
        "✅ تم إلغاء البحث.", uid, mid, reply_markup=main_menu_kb(),
    )
    try:
        bot.answer_callback_query(call.id)
    except Exception:
        pass


def _start_quick_match(x_player, o_player):
    game_id = secrets.token_urlsafe(8)
    create_game(game_id, x_player["id"], x_player["name"], x_player["chat_id"])
    deadline = datetime.now(timezone.utc) + timedelta(seconds=MOVE_TIMEOUT_SECONDS)

    board = [EMPTY] * 9

    with _qs_lock:
        x_sess = quick_search_sessions.get(x_player["id"])
    x_msg_id = None
    if x_sess:
        x_msg_id = x_sess["msg_id"]
        try:
            bot.edit_message_text(
                f"🎮 *وُجد خصم!*\n❌ أنت  ⚔️  ⭕ {o_player['name']}\n\nاللوحة تُحمّل...",
                x_player["chat_id"], x_msg_id,
                reply_markup=board_kb(board, f"pvp:{game_id}"),
                parse_mode="Markdown",
            )
        except Exception as e:
            print(f"⚠️ quick_match edit X: {e}")
            x_msg_id = None

    if not x_msg_id:
        try:
            sent_x = bot.send_message(
                x_player["chat_id"],
                f"🎮 *وُجد خصم!*\n❌ أنت  ⚔️  ⭕ {o_player['name']}\n\nاللوحة تُحمّل...",
                reply_markup=board_kb(board, f"pvp:{game_id}"),
                parse_mode="Markdown",
            )
            x_msg_id = sent_x.message_id
        except Exception as e:
            print(f"⚠️ quick_match send X: {e}")

    update_game(game_id, {
        "player_o_id": int(o_player["id"]),
        "player_o_name": o_player["name"],
        "status": "playing",
        "turn": PLAYER_X,
        "turn_deadline": deadline,
        "x_chat_id": int(x_player["chat_id"]),
        "x_msg_id": x_msg_id,
        "o_chat_id": int(o_player["chat_id"]),
        "o_msg_id": int(o_player["msg_id"]),
    })

    with _qs_lock:
        quick_search_sessions.pop(o_player["id"], None)
        quick_search_sessions.pop(x_player["id"], None)

    refresh_pvp_messages(game_id)


def quick_match_checker():
    """
    التوثيق: فاحص الطابور (العد الصامت). يتحقق من الوقت المنقضي في الذاكرة فقط،
    ولا يرسل أي تعديلات لتيليجرام إلا عند حدوث (حدث الانتهاء).
    """
    while True:
        try:
            now = datetime.now(timezone.utc)
            with _qs_lock:
                sessions = list(quick_search_sessions.items())
                
            for uid, s in sessions:
                # التأكد من أن اللاعب لا يزال في الطابور ولم يتم حذفه مسبقاً
                with _qs_lock:
                    if uid not in quick_search_sessions:
                        continue

                # حساب الوقت المنقضي بصمت في الذاكرة
                elapsed = int((now - s["joined_at"]).total_seconds())

                # الحدث الوحيد: انتهاء الوقت المحدد للبحث
                if elapsed >= QUICK_MATCH_TIMEOUT_SECONDS:
                    # 1. إزالة اللاعب من قاعدة البيانات
                    try:
                        queue_remove(uid)
                    except Exception:
                        pass
                    
                    # 2. إزالة اللاعب من الذاكرة المؤقتة بأمان
                    with _qs_lock:
                        quick_search_sessions.pop(uid, None)
                        
                    # 3. إرسال التحديث لمرة واحدة فقط لإخبار المستخدم بانتهاء الوقت
                    try:
                        kb = types.InlineKeyboardMarkup(row_width=1)
                        kb.add(types.InlineKeyboardButton("🤖 العب ضد البوت", callback_data="menu_bot"))
                        kb.add(types.InlineKeyboardButton("🏠 القائمة", callback_data="back_main"))
                        
                        bot.edit_message_text(
                            "😔 *لم نجد لك خصماً الآن.*\n\nانتهى وقت البحث المخصص.",
                            s["chat_id"], s["msg_id"],
                            reply_markup=kb, parse_mode="Markdown",
                        )
                    except Exception as e:
                        if "message is not modified" not in str(e):
                            print(f"⚠️ فشل تحديث رسالة انتهاء البحث: {e}")
                    continue
                    
                # التوثيق: تم حذف الكود الذي كان يقوم بتحديث الرسالة كل 3 ثوانٍ بالكامل.

        except Exception as e:
            print(f"⚠️ خطأ في فاحص اللعب العشوائي: {e}")
            
        # إراحة الخادم وتقليل استهلاك المعالج
        time_mod.sleep(3)

# ============================
# === PvP ===
# ============================

def handle_pvp_create(call):
    uid = call.message.chat.id
    mid = call.message.message_id
    name = call.from_user.first_name or "لاعب"
    get_or_create_user(uid, name)

    if _has_active_game_block(uid):
        return

    username = get_bot_username() or "TR_XO_BOT"
    text = (
        "🎮 *العب مع صديقك*\n\n"
        "📤 اضغط الزر بالأسفل\n"
        "   أو\n"
        "⌨️ اكتب في أي محادثة:\n"
        f"`@{username} XO`\n\n"
        "ثم اختر: *❌* (تبدأ أولاً) أو *⭕*\n"
        "وأرسل البطاقة لتبدأ اللعبة فوراً!\n\n"
        f"⏳ صلاحية التحدّي: {CHALLENGE_TIMEOUT_SECONDS // 60} دقيقتان"
    )
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton(
        "📤 مشاركة التحدّي في محادثة",
        switch_inline_query="XO",
    ))
    kb.add(types.InlineKeyboardButton("🏠 القائمة", callback_data="back_main"))
    bot.edit_message_text(
        text, uid, mid, reply_markup=kb,
        parse_mode="Markdown", disable_web_page_preview=True,
    )


def handle_pvp_action(call, data):
    parts = data.split(":")
    if len(parts) < 3:
        try:
            bot.answer_callback_query(call.id)
        except Exception:
            pass
        return

    game_id = parts[1]
    action = parts[2]
    user_id = call.from_user.id
    user_name = call.from_user.first_name or "لاعب"
    is_inline = call.message is None

    if is_inline and call.inline_message_id:
        _g = get_game(game_id)
        if _g and not _g.get("inline_message_id"):
            try:
                update_game(game_id, {
                    "inline_message_id": call.inline_message_id,
                    "status": "posted" if _g.get("status") == "waiting" else _g.get("status"),
                })
            except Exception as e:
                print(f"⚠️ save inline_message_id: {e}")

    game = get_game(game_id)
    if not game:
        try:
            bot.answer_callback_query(call.id, "المباراة انتهت أو حُذفت")
        except Exception:
            pass
        if not is_inline:
            try:
                bot.edit_message_text(
                    "🏁 انتهت المباراة.",
                    call.message.chat.id, call.message.message_id,
                    reply_markup=main_menu_kb(),
                )
            except Exception:
                pass
        return

    status = game.get("status")

    if action == "noop":
        try:
            bot.answer_callback_query(call.id)
        except Exception:
            pass
        return

    if action == "cancel":
        creator_id = game.get("player_x_id") or game.get("player_o_id")
        if user_id != creator_id:
            bot.answer_callback_query(call.id, "لا يمكنك إلغاء هذا التحدّي")
            return
        if status not in ("waiting", "posted"):
            bot.answer_callback_query(call.id, "المباراة بدأت بالفعل")
            return
        expire_game(game_id, "❌ *ألغى المنشئ التحدّي.*")
        try:
            bot.answer_callback_query(call.id, "تم الإلغاء")
        except Exception:
            pass
        return

    if action == "resign":
        if status != "playing":
            bot.answer_callback_query(call.id)
            return
        if user_id not in (game.get("player_x_id"), game.get("player_o_id")):
            bot.answer_callback_query(call.id, "لست طرفاً في هذه المباراة")
            return
        winner = PLAYER_O if user_id == game.get("player_x_id") else PLAYER_X
        finalize_pvp(game_id, winner, resigned=True)
        try:
            bot.answer_callback_query(call.id)
        except Exception:
            pass
        return

    if action == "move" and status in ("waiting", "posted"):
        creator_id = game.get("player_x_id") or game.get("player_o_id")
        creator_symbol = PLAYER_X if game.get("player_x_id") else PLAYER_O

        if user_id == creator_id:
            bot.answer_callback_query(call.id, "⏳ انتظر انضمام الخصم")
            return

        target_id = game.get("target_id")
        if target_id and int(user_id) != int(target_id):
            try:
                bot.answer_callback_query(call.id, "هذا التحدّي موجّه لشخص آخر")
            except Exception:
                pass
            return

        get_or_create_user(user_id, user_name)
        deadline = datetime.now(timezone.utc) + timedelta(seconds=MOVE_TIMEOUT_SECONDS)
        updates = {"status": "playing", "turn": PLAYER_X, "turn_deadline": deadline}
        joined_symbol = PLAYER_O if creator_symbol == PLAYER_X else PLAYER_X
        if joined_symbol == PLAYER_O:
            updates["player_o_id"] = user_id
            updates["player_o_name"] = user_name
        else:
            updates["player_x_id"] = user_id
            updates["player_x_name"] = user_name

        update_game(game_id, updates)
        try:
            msg = (
                "✅ انضممت كـ ⭕ ! ينتظر دور ❌ أولاً"
                if joined_symbol == PLAYER_O
                else "✅ انضممت كـ ❌ — أنت تبدأ أولاً!"
            )
            bot.answer_callback_query(call.id, msg)
        except Exception:
            pass
        refresh_pvp_messages(game_id)
        _notify_creator_opponent_joined(game_id)
        return

    if action == "move":
        if status != "playing":
            bot.answer_callback_query(call.id, "المباراة غير نشطة")
            return
        if user_id not in (game.get("player_x_id"), game.get("player_o_id")):
            bot.answer_callback_query(call.id, "لست طرفاً في هذه المباراة")
            return

        try:
            pos = int(parts[3])
        except (IndexError, ValueError):
            bot.answer_callback_query(call.id)
            return

        turn = game.get("turn", PLAYER_X)
        expected_uid = game.get("player_x_id") if turn == PLAYER_X else game.get("player_o_id")
        if user_id != expected_uid:
            bot.answer_callback_query(call.id, "ليس دورك ⏳")
            return

        board = board_list(game["board"])
        if board[pos] != EMPTY:
            bot.answer_callback_query(call.id, "الخانة مأخوذة")
            return

        board[pos] = turn
        result = check_winner(board)
        next_turn = PLAYER_O if turn == PLAYER_X else PLAYER_X

        new_deadline = datetime.now(timezone.utc) + timedelta(seconds=MOVE_TIMEOUT_SECONDS)
        update_game(game_id, {
            "board": board_str(board),
            "turn": next_turn,
            "turn_deadline": new_deadline,
        })

        if result:
            finalize_pvp(game_id, result)
        else:
            refresh_pvp_messages(game_id)

        try:
            bot.answer_callback_query(call.id)
        except Exception:
            pass
        return


def refresh_pvp_messages(game_id):
    game = get_game(game_id)
    if not game:
        return

    if game.get("inline_message_id"):
        render_inline_board(game_id)

    board = board_list(game["board"])
    kb = board_kb(board, f"pvp:{game_id}")

    for player_key, chat_key, msg_key in [
        ("player_x_id", "x_chat_id", "x_msg_id"),
        ("player_o_id", "o_chat_id", "o_msg_id"),
    ]:
        chat_id = game.get(chat_key)
        msg_id = game.get(msg_key)
        viewer = game.get(player_key)
        if not (chat_id and msg_id and viewer):
            continue
        if player_key == "player_x_id" and game.get("inline_message_id"):
            continue

        is_group = str(chat_id).startswith("-")

        try:
            bot.edit_message_text(
                fmt_pvp_game(game, viewer, is_group),
                chat_id=chat_id, message_id=msg_id,
                reply_markup=kb, parse_mode="Markdown",
            )
        except Exception as e:
            if "message is not modified" not in str(e):
                print(f"⚠️ فشل تحديث رسالة {player_key}: {e}")


def finalize_pvp(game_id, winner, resigned=False):
    game = get_game(game_id)
    if not game:
        return

    update_game(game_id, {"status": "finished", "winner": winner})
    game = get_game(game_id)

    px = game.get("player_x_id")
    po = game.get("player_o_id")

    PVP_PTS = {"win": 15, "draw": 5, "loss": 2}

    pair_capped = False
    pair_pts_today = 0
    if px and po:
        record_pair_match(px, po, 10**9)
        pair_pts_today = get_pair_points(px, po)
        if pair_pts_today >= PAIR_DAILY_POINTS_CAP:
            pair_capped = True
            if pair_pts_today == PAIR_DAILY_POINTS_CAP:
                try:
                    if ADMIN_ID:
                        bot.send_message(
                            int(ADMIN_ID),
                            "⚠️ *بلغ الزوج الحد الأقصى للنقاط اليومية*\n\n"
                            f"🆔 `{px}` × `{po}`\n"
                            f"📊 النقاط: *{pair_pts_today}/{PAIR_DAILY_POINTS_CAP}*\n\n"
                            "_المباريات القادمة بينهما اليوم بلا نقاط._",
                            parse_mode="Markdown",
                        )
                except Exception:
                    pass

    def _rec(pid, mode, outcome):
        if mode == "pvp" and pair_capped:
            record_result(pid, mode, outcome, points_override=0)
        elif mode == "pvp":
            base = PVP_PTS[outcome]
            remaining = max(0, PAIR_DAILY_POINTS_CAP - pair_pts_today)
            granted = min(base, remaining)
            record_result(pid, mode, outcome, points_override=granted)
        else:
            record_result(pid, mode, outcome)

    if winner == "draw":
        if px: _rec(px, "pvp", "draw")
        if po: _rec(po, "pvp", "draw")
    elif winner == PLAYER_X:
        if px: _rec(px, "pvp", "win")
        if po: _rec(po, "pvp", "loss")
    elif winner == PLAYER_O:
        if po: _rec(po, "pvp", "win")
        if px: _rec(px, "pvp", "loss")

    if px and po and not pair_capped:
        if winner == "draw":
            match_total = PVP_PTS["draw"] * 2
        elif winner in (PLAYER_X, PLAYER_O):
            match_total = PVP_PTS["win"] + PVP_PTS["loss"]
        else:
            match_total = 0
        if match_total:
            granted = min(match_total, max(0, PAIR_DAILY_POINTS_CAP - pair_pts_today))
            if granted > 0:
                add_pair_points(px, po, granted)

    if game.get("inline_message_id"):
        render_inline_board(game_id)

    board = board_list(game["board"])
    final_board_kb = board_kb(board, f"pvp:{game_id}", disabled=True)

    for player_key, chat_key, msg_key in [
        ("player_x_id", "x_chat_id", "x_msg_id"),
        ("player_o_id", "o_chat_id", "o_msg_id"),
    ]:
        chat_id = game.get(chat_key)
        msg_id = game.get(msg_key)
        viewer = game.get(player_key)
        if not (chat_id and msg_id and viewer):
            continue
        if player_key == "player_x_id" and game.get("inline_message_id"):
            continue

        is_group = str(chat_id).startswith("-")
        suffix = ""

        if is_group:
            pass
        elif resigned:
            if winner == PLAYER_X and viewer == po:
                suffix = "\n🏳️ لقد انسحبت."
            elif winner == PLAYER_O and viewer == px:
                suffix = "\n🏳️ لقد انسحبت."
            elif winner == PLAYER_X and viewer == px:
                suffix = "\n🏆 فزت (انسحب الخصم)."
            elif winner == PLAYER_O and viewer == po:
                suffix = "\n🏆 فزت (انسحب الخصم)."
        else:
            if winner == "draw":
                suffix = "\n🤝 تعادل!"
            else:
                winner_is_you = (winner == PLAYER_X and viewer == px) or \
                                (winner == PLAYER_O and viewer == po)
                suffix = "\n🎉 فزت!" if winner_is_you else "\n😔 خسرت."

        try:
            bot.edit_message_text(
                fmt_pvp_game(game, viewer, is_group) + suffix,
                chat_id=chat_id, message_id=msg_id,
                reply_markup=final_board_kb, parse_mode="Markdown",
            )
        except Exception as e:
            if "message is not modified" not in str(e):
                print(f"⚠️ فشل تحديث رسالة النهاية {player_key}: {e}")


def get_user_rank(points):
    """
    تحديد رتبة اللاعب بناءً على النقاط المحددة من قبلك.
    """
    if points <= 299:
        return "برونز 🥉"
    elif points <= 599:
        return "ذهب 🟡"
    elif points <= 999:
        return "بلاتنيوم ⚪"
    elif points <= 1599:
        return "ماسي 💎"
    elif points <= 2399:
        return "زعيم 👑"
    elif points <= 3199:
        return "محترف ⚔️"
    else:
        return "أسطوري 🌌"
    
# ============================
# === عرض الإحصائيات ولوحة الشرف ===
# ============================

def render_stats(user):
    total_games = user.get("wins", 0) + user.get("losses", 0) + user.get("draws", 0)
    wr = (user.get("wins", 0) / total_games * 100) if total_games else 0
    points = user.get('points', 0)
    
    # جلب الرتبة بناءً على النقاط الحالية
    rank_label = get_user_rank(points)
    
    return (
        f"📊 *إحصائيات {user.get('name','لاعب')}*\n\n"
        f"🏅 *الرتبة:* {rank_label}\n"
        f"⭐ *النقاط:* {points}\n\n"
        f"🎮 إجمالي المباريات: {total_games}\n"
        f"🏆 انتصارات: {user.get('wins',0)}\n"
        f"💔 هزائم: {user.get('losses',0)}\n"
        f"🤝 تعادلات: {user.get('draws',0)}\n"
        f"📈 نسبة الفوز: {wr:.1f}%\n\n"
        "*ضد البوت (سهل):*\n"
        f"  فوز {user.get('bot_easy_wins',0)} / "
        f"خسارة {user.get('bot_easy_losses',0)} / "
        f"تعادل {user.get('bot_easy_draws',0)}\n"
        "*ضد البوت (صعب):*\n"
        f"  فوز {user.get('bot_hard_wins',0)} / "
        f"خسارة {user.get('bot_hard_losses',0)} / "
        f"تعادل {user.get('bot_hard_draws',0)}\n"
        "*ضد اللاعبين:*\n"
        f"  فوز {user.get('pvp_wins',0)} / "
        f"خسارة {user.get('pvp_losses',0)} / "
        f"تعادل {user.get('pvp_draws',0)}\n"
    )


def _md_escape(s):
    s = str(s or "لاعب")
    for ch in ("\\", "[", "]", "*", "_", "`", "(", ")"):
        s = s.replace(ch, "\\" + ch)
    return s


def _mention(u):
    name = _md_escape(u.get("name", "لاعب"))
    uid = u.get("user_id")
    if uid:
        return f"[{name}](tg://user?id={int(uid)})"
    return name

def render_leaderboard(users, viewer_id, viewer_pts=0, viewer_rank="غير مصنف"):
    try:
        time_left = format_time_left(
            next_scheduled_reset(datetime.now(timezone.utc)) - datetime.now(timezone.utc)
        )
    except Exception:
        time_left = "—"

    all_zero = bool(users) and all((u.get("points", 0) or 0) == 0 for u in users)

    if not users or all_zero:
        head = [
            "🏆 *لوحة الشرف (أفضل 5)*",
            f"⏳ يُعاد التصفير خلال: *{time_left}*",
            "",
            "🎁 *الجوائز:*",
            "┃ 🥇 الأول: 120 UC",
            "┃ 🥈 الثاني: 60 UC",
            "┃ 🥉 الثالث: 60 UC",
            "",
            "_تم التصفير. كن أول من يسجّل نقاطاً هذا الأسبوع!_",
        ]
        try:
            season = get_last_season()
        except Exception:
            season = None
        if season and season.get("top"):
            head.append("")
            head.append("🏅 *آخر فائزين (الأسبوع السابق):*")
            medals = ["🥇", "🥈", "🥉"]
            prizes = ["120 UC", "60 UC", "60 UC"]
            for i, u in enumerate(season.get("top", [])[:3]):
                pts = u.get("points", 0)
                name_cut = _md_escape(u.get('name','لاعب'))[:15]
                head.append(f"{medals[i]} *{name_cut}* • ⭐️ {pts} (🎁 {prizes[i]})")
        
        head.append("\n━━━━━━━━━━━━━━")
        head.append(f"👤 *أنت:* غير مصنف (0 نقطة)")
        return "\n".join(head)

    lines = [
        "🏆 *لوحة الشرف (أفضل 5)*",
        f"⏳ يُعاد التصفير خلال: *{time_left}*",
        "",
        "🎁 *الجوائز:*",
        "┃ 🥇 الأول: 120 UC",
        "┃ 🥈 الثاني: 60 UC",
        "┃ 🥉 الثالث: 60 UC",
        "",
    ]
    
    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
    for i, u in enumerate(users):
        prefix = medals[i] if i < len(medals) else f"{i+1}."
        me = " 👈 أنت" if str(u.get("user_id")) == str(viewer_id) or str(u.get("id")) == str(viewer_id) else ""
        pts = u.get("points", 0)
        
        rank = get_user_rank(pts) 
        name_cut = _md_escape(u.get('name','لاعب'))[:15]
        
        # التنسيق الجديد: الاسم ثم الرتبة ثم النقاط، مفصولة بنقاط قوية تمنع التداخل
        lines.append(f"{prefix} *{name_cut}* ⟨ {rank} ⟩ ⁞ ⭐️ {pts}{me}")
    
    lines.append("")
    lines.append("━━━━━━━━━━━━━━")
    if viewer_rank == "غير مصنف" or viewer_pts == 0:
        lines.append(f"👤 *أنت:* غير مصنف (0 نقطة)")
    else:
        lines.append(f"👤 *أنت:* المركز #{viewer_rank} | ⭐️ نقاطك: {viewer_pts}")

    return "\n".join(lines)


def render_admin_leaderboard(users):
    try:
        time_left = format_time_left(
            next_scheduled_reset(datetime.now(timezone.utc)) - datetime.now(timezone.utc)
        )
    except Exception:
        time_left = "—"
    if not users:
        return "🛠 *لوحة المالك التفصيلية*\n\nلا يوجد لاعبون."
    
    lines = [
        "🛠 *لوحة المالك — تفاصيل أعلى 25 لاعباً*",
        f"⏳ يُعاد التصفير خلال: *{time_left}*",
        "",
    ]
    
    medals = ["🥇", "🥈", "🥉"]
    for i, u in enumerate(users):
        prefix = medals[i] if i < 3 else f"{i+1}."
        pts = u.get("points", 0)
        uid_ = u.get("user_id", "—")
        uname = u.get("username", "") or ""
        uname_str = f"@{uname}" if uname else "—"
        
        name_cut = _md_escape(u.get('name','لاعب'))[:15]
        rank = get_user_rank(pts)
        
        lines.append(
            f"{prefix} *{name_cut}* ⟨ {rank} ⟩ ⁞ ⭐️ {pts}\n"
            f"└ 🆔 `{uid_}` | 👤 `{uname_str}`\n"
        )
        
    return "\n".join(lines)


def render_last_season(season):
    if not season or not season.get("top"):
        return (
            "🏅 *الأسبوع السابق*\n\n"
            "لا يوجد موسم مُؤرشف بعد.\n"
            "سيظهر هنا الفائزون بعد أول تصفير (الجمعة 00:00 بتوقيت الرياض)."
        )
    top = season.get("top", [])[:3]
    season_id = season.get("id") or season.get("season_id", "")
    lines = [
        f"🏅 *الفائزون — {season_id}*",
        "",
        "🎁 *الجوائز:*",
    ]
    medals = ["🥇", "🥈", "🥉"]
    prizes = ["120 UC", "60 UC", "60 UC"]
    for i, u in enumerate(top):
        pts = u.get("points", 0)
        name_cut = _md_escape(u.get('name','لاعب'))[:15]
        lines.append(f"┃ {medals[i]} *{name_cut}* • ⭐️ {pts} (🎁 {prizes[i]})")
        
    if len(top) < 3:
        lines.append("\n_(لم يكتمل عدد الفائزين هذا الأسبوع)_")
    return "\n".join(lines)

# ============================
# === Inline Mode ===
# ============================

@bot.inline_handler(func=lambda q: True)
def on_inline_query(inline_query):
    uid = inline_query.from_user.id
    name = inline_query.from_user.first_name or "لاعب"
    q = (inline_query.query or "").strip()
    q_lower = q.lower()
    parts = q_lower.split()

    numbers = []
    for word in parts:
        val = _parse_popularity(word)
        if val is not None:
            numbers.append(val)

    if len(numbers) >= 2:
        pop1, pop2 = numbers[0], numbers[1]

        if not FEATURES.get("popcalc_enabled", True) and not is_admin(uid):
            results = [
                types.InlineQueryResultArticle(
                    id="disabled_calc",
                    title="🔒 الحاسبة متوقفة",
                    description="حاسبة الشعبية متوقفة مؤقتاً من الإدارة.",
                    input_message_content=types.InputTextMessageContent(
                        message_text="🔒 حاسبة الشعبية متوقفة مؤقتاً.",
                    ),
                )
            ]
            try: bot.answer_inline_query(inline_query.id, results, cache_time=0, is_personal=True)
            except: pass
            return

        mode = "team" if "فريق" in q_lower or "team" in q_lower else "pop"
        points_fn = team_points if mode == "team" else pop_points
        title_prefix = "⚔️ معركة الفريق:" if mode == "team" else "🔥 المعركة الفردية:"

        own_pts = points_fn(pop1)
        opp_pts = points_fn(pop2)
        win_gain = own_pts + opp_pts // 2
        loss = own_pts // 2

        text = (
            f"🧮 *حاسبة الشعبية ({'فريق' if mode == 'team' else 'فردية'})*\n\n"
            f"🟢 شعبيتك: *{pop1:,}* ⟵ `({own_pts} نقطة)`\n"
            f"🔴 الخصم: *{pop2:,}* ⟵ `({opp_pts} نقطة)`\n\n"
            f"✅ فوزك يعطيك: *+{win_gain}* نقطة\n"
            f"❌ خسارتك تخصم: *-{loss}* نقطة"
        )

        results = [
            types.InlineQueryResultArticle(
                id=f"calc_{pop1}_{pop2}_{mode}",
                title=f"{title_prefix} فوز +{win_gain} | خسارة -{loss}",
                description=f"شعبيتك: {pop1:,} | الخصم: {pop2:,}",
                input_message_content=types.InputTextMessageContent(
                    message_text=text, parse_mode="Markdown"
                )
            )
        ]
        try: bot.answer_inline_query(inline_query.id, results, cache_time=0, is_personal=True)
        except: pass
        return

    if not FEATURES.get("xo_enabled", True) and not is_admin(uid):
        results = [
            types.InlineQueryResultArticle(
                id="disabled_xo",
                title="🔒 لعبة XO متوقفة",
                description="اللعبة متوقفة مؤقتاً من قبل الإدارة.",
                input_message_content=types.InputTextMessageContent(
                    message_text="🔒 لعبة XO متوقفة مؤقتاً. تابعنا لمعرفة وقت العودة!",
                ),
            )
        ]
        try: bot.answer_inline_query(inline_query.id, results, cache_time=0, is_personal=True)
        except: pass
        return

    results = []

    if q and len(q) > 4 and q_lower not in ("xo", "اكس", "او", "فريق"):
        existing = get_game(q)
        if existing and existing.get("status") in ("waiting", "posted") \
                and existing.get("player_x_id") == uid:
            board_text = (
                f"🎮 *لعبة XO*\n❌ {name}  ⚔️  ⭕ بانتظار لاعب...\n\n"
                "⭕ اضغط أي مربع للانضمام كـ ⭕!"
            )
            kb = board_kb([EMPTY] * 9, f"pvp:{q}")
            results.append(types.InlineQueryResultArticle(
                id=q,
                title="🎮 إرسال تحدّي XO (أنشأته مسبقاً)",
                description=f"لعبة ضد {name}",
                input_message_content=types.InputTextMessageContent(
                    message_text=board_text, parse_mode="Markdown",
                ),
                reply_markup=kb,
            ))
            try: bot.answer_inline_query(inline_query.id, results, cache_time=0, is_personal=True)
            except: pass
            return

    if q_lower == "xo" or "اكس" in q or "او" in q:
        get_or_create_user(uid, name)

        gid_x = secrets.token_urlsafe(8)
        gid_o = secrets.token_urlsafe(8)
        create_game_symbol(gid_x, uid, name, "X")
        create_game_symbol(gid_o, uid, name, "O")

        text_x = f"🎮 *لعبة XO*\n❌ {name}  ⚔️  ⭕ بانتظار لاعب...\n\n⭕ اضغط أي مربع للانضمام كـ ⭕!"
        kb_x = board_kb([EMPTY] * 9, f"pvp:{gid_x}")

        text_o = f"🎮 *لعبة XO*\n❌ بانتظار لاعب...  ⚔️  ⭕ {name}\n\n❌ اضغط أي مربع للانضمام كـ ❌ (وتبدأ أولاً)!"
        kb_o = board_kb([EMPTY] * 9, f"pvp:{gid_o}")

        results.append(types.InlineQueryResultArticle(
            id=gid_x,
            title="❌ ألعب كـ X (أبدأ أولاً)",
            description="إرسال تحدّي وأنت تلعب بالـ ❌",
            input_message_content=types.InputTextMessageContent(message_text=text_x, parse_mode="Markdown"),
            reply_markup=kb_x,
        ))
        results.append(types.InlineQueryResultArticle(
            id=gid_o,
            title="⭕ ألعب كـ O",
            description="إرسال تحدّي وأنت تلعب بالـ ⭕ (الخصم يبدأ)",
            input_message_content=types.InputTextMessageContent(message_text=text_o, parse_mode="Markdown"),
            reply_markup=kb_o,
        ))

        try: bot.answer_inline_query(inline_query.id, results, cache_time=0, is_personal=True)
        except: pass
        return

    bot_user = get_bot_username() or "يوزر_البوت"
    results.append(types.InlineQueryResultArticle(
        id="help_inline",
        title="✨ حاسبة الشعبية وتحديات XO",
        description="اضغط هنا لعرض دليل الاستخدام السريع 💡",
        input_message_content=types.InputTextMessageContent(
            message_text=(
                "✨ *دليل الاستخدام الذكي والسريع* ✨\n\n"
                "🎮 *لإرسال تحدي XO لأصدقائك:*\n"
                "اكتب يوزر البوت وبعده كلمة `XO`\n"
                f"• `@{bot_user} xo`\n"
                f"• `@{bot_user} اكس`\n"
                f"• `@{bot_user} او`\n\n"
                "🧮 *لحساب الشعبية (الفردية):*\n"
                "اكتب أي رقمين وسيفهمها البوت فوراً!\n"
                f"• `@{bot_user} انا 2M خصمي 1M`\n"
                f"• `@{bot_user} 50000 20000`\n\n"
                "⚔️ *لحساب معركة الفريق:*\n"
                "فقط أضف كلمة `فريق` مع الأرقام:\n"
                f"• `@{bot_user} فريق 150k 80k`\n"
                f"• `@{bot_user} فريق 80000 60000`\n\n"
                "👇 *انسخ أحد الأمثلة وجربها الآن في الشات!*"
            ),
            parse_mode="Markdown"
        ),
    ))
    try: bot.answer_inline_query(inline_query.id, results, cache_time=0, is_personal=True)
    except: pass

@bot.chosen_inline_handler(func=lambda c: True)
def on_chosen_inline(chosen):
    game_id = chosen.result_id
    im_id = chosen.inline_message_id
    print(f"[PvP] chosen_inline_result game_id={game_id} im_id={im_id}")
    if not im_id or game_id in ("invalid", "help"):
        return

    game = get_game(game_id)
    if not game or game.get("status") not in ("waiting", "posted"):
        return

    update_game(game_id, {
        "inline_message_id": im_id,
        "status": "posted",
    })
    render_inline_board(game_id)

    try:
        kb = types.InlineKeyboardMarkup(row_width=1)
        kb.add(
            types.InlineKeyboardButton(
                "❌ إلغاء التحدّي", callback_data=f"pvp:{game_id}:cancel"
            ),
            types.InlineKeyboardButton("🏠 القائمة", callback_data="back_main"),
        )
        bot.edit_message_text(
            "✅ تم إرسال التحدّي إلى المحادثة!\n\n"
            "🎯 افتح تلك المحادثة والعب من هناك.\n"
            f"⏳ إذا لم يبدأ الخصم خلال {CHALLENGE_TIMEOUT_SECONDS // 60} "
            "دقيقتين، سيُلغى التحدّي تلقائياً.",
            chat_id=game.get("x_chat_id"),
            message_id=game.get("x_msg_id"),
            reply_markup=kb,
        )
    except Exception as e:
        if "message is not modified" not in str(e):
            print(f"⚠️ update creator DM: {e}")


def _notify_creator_opponent_joined(game_id):
    game = get_game(game_id)
    if not game:
        return

    if game.get("target_id") or str(game.get("x_chat_id", "")).startswith("-"):
        return

    chat_id = game.get("x_chat_id")
    msg_id = game.get("x_msg_id")
    if not (chat_id and msg_id):
        return
    o_name = game.get("player_o_name", "لاعب")
    text = (
        f"🎮 *بدأت المباراة!*\n\n"
        f"❌ أنت  ⚔️  ⭕ {o_name}\n\n"
        "🎯 افتح المحادثة التي أرسلت إليها التحدّي والعب هناك.\n"
        "أنت تبدأ أولاً (❌)."
    )
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("🏠 القائمة", callback_data="back_main"))
    try:
        bot.edit_message_text(
            text, chat_id=chat_id, message_id=msg_id,
            reply_markup=kb, parse_mode="Markdown",
        )
    except Exception as e:
        if "message is not modified" not in str(e):
            print(f"⚠️ notify creator: {e}")


def render_inline_board(game_id):
    game = get_game(game_id)
    if not game:
        return
    im_id = game.get("inline_message_id")
    if not im_id:
        return

    status = game.get("status")
    x_name = game.get("player_x_name") or "بانتظار لاعب..."
    o_name = game.get("player_o_name") or "بانتظار لاعب..."

    header = f"🎮 *لعبة XO*\n❌ {x_name}  ⚔️  ⭕ {o_name}\n"

    if status in ("waiting", "posted"):
        missing = PLAYER_O if game.get("player_x_id") and not game.get("player_o_id") else PLAYER_X
        sym = "⭕" if missing == PLAYER_O else "❌"
        header += f"\n{sym} اضغط أي مربع للانضمام كـ {sym}!"
    elif status == "playing":
        turn = game.get("turn", PLAYER_X)
        sym = "❌" if turn == PLAYER_X else "⭕"
        nm = x_name if turn == PLAYER_X else o_name
        dl = game.get("turn_deadline")
        secs_txt = ""
        if dl is not None:
            try:
                if getattr(dl, "tzinfo", None) is None:
                    dl = dl.replace(tzinfo=timezone.utc)
                secs = int((dl - datetime.now(timezone.utc)).total_seconds())
                secs = max(0, secs)
                secs_txt = f"  ⏱️ {secs} ث"
            except Exception:
                pass
        header += f"\n⏳ الدور على {nm} ({sym}){secs_txt}"
        header += f"\n_لديك {MOVE_TIMEOUT_SECONDS} ثوانٍ لكل حركة وإلا خسرت!_"
    elif status == "finished":
        winner = game.get("winner")
        if winner == "draw":
            header += "\n🤝 تعادل!"
        elif winner in (PLAYER_X, PLAYER_O):
            wn = x_name if winner == PLAYER_X else o_name
            sym = "❌" if winner == PLAYER_X else "⭕"
            header += f"\n🏆 الفائز: {wn} ({sym})"
        if game.get("end_reason") == "timeout":
            header += "\n⌛ (خسر الخصم بانتهاء مهلة الحركة)"

    board = board_list(game.get("board", EMPTY * 9))
    disabled = (status == "finished")
    kb = board_kb(board, f"pvp:{game_id}", disabled=disabled)

    try:
        bot.edit_message_text(
            header,
            inline_message_id=im_id,
            reply_markup=kb,
            parse_mode="Markdown",
        )
    except Exception as e:
        if "message is not modified" not in str(e):
            print(f"⚠️ render_inline_board: {e}")


# ============================
# === انتهاء صلاحية التحدّيات ===
# ============================

def expire_game(game_id, reason):
    game = get_game(game_id)
    if not game:
        return

    if game.get("inline_message_id"):
        try:
            bot.edit_message_text(
                f"🎮 *تحدّي XO*\n\n{reason}",
                inline_message_id=game["inline_message_id"],
                parse_mode="Markdown",
                reply_markup=None  
            )
        except Exception as e:
            if "message is not modified" not in str(e):
                print(f"⚠️ expire inline: {e}")

    if game.get("x_chat_id") and game.get("x_msg_id"):
        chat_id = game["x_chat_id"]
        msg_id = game["x_msg_id"]
        is_group = str(chat_id).startswith("-")  
        
        try:
            if is_group:
                bot.edit_message_text(
                    f"{reason}",
                    chat_id=chat_id,
                    message_id=msg_id,
                    parse_mode="Markdown",
                    reply_markup=None  
                )
            else:
                bot.edit_message_text(
                    f"{reason}\n\nأرسل /menu للبدء من جديد.",
                    chat_id=chat_id,
                    message_id=msg_id,
                    reply_markup=main_menu_kb(),
                    parse_mode="Markdown",
                )
        except Exception as e:
            if "message is not modified" not in str(e):
                print(f"⚠️ expire DM/Group X: {e}")

    if game.get("o_chat_id") and game.get("o_msg_id"):
        chat_id = game["o_chat_id"]
        msg_id = game["o_msg_id"]
        is_group = str(chat_id).startswith("-")
        
        try:
            if is_group:
                bot.edit_message_text(
                    f"{reason}",
                    chat_id=chat_id,
                    message_id=msg_id,
                    parse_mode="Markdown",
                    reply_markup=None
                )
            else:
                bot.edit_message_text(
                    f"{reason}\n\nأرسل /menu للبدء من جديد.",
                    chat_id=chat_id,
                    message_id=msg_id,
                    reply_markup=main_menu_kb(),
                    parse_mode="Markdown",
                )
        except Exception as e:
            if "message is not modified" not in str(e):
                print(f"⚠️ expire DM/Group O: {e}")

    delete_game(game_id)


def move_timeout_checker():
    while True:
        try:
            pending = get_pending_games()
            now = datetime.now(timezone.utc)
            for g in pending:
                if g.get("status") != "playing":
                    continue
                dl = g.get("turn_deadline")
                if not dl:
                    continue
                try:
                    if getattr(dl, "tzinfo", None) is None:
                        dl = dl.replace(tzinfo=timezone.utc)
                    if (now - dl).total_seconds() <= 0:
                        continue
                except Exception:
                    continue
                turn = g.get("turn", PLAYER_X)
                winner = PLAYER_O if turn == PLAYER_X else PLAYER_X
                loser_name = (g.get("player_x_name") if turn == PLAYER_X
                              else g.get("player_o_name")) or "اللاعب"
                print(f"[move_timeout] game_id={g.get('id')} loser={turn} winner={winner}")
                try:
                    update_game(g["id"], {"end_reason": "timeout"})
                    finalize_pvp(g["id"], winner, resigned=False)
                except Exception as e:
                    print(f"⚠️ move_timeout finalize: {e}")
        except Exception as e:
            print(f"⚠️ move_timeout_checker: {e}")
        time_mod.sleep(2)


def expiration_checker():
    while True:
        try:
            pending = get_pending_games()
            now = datetime.now(timezone.utc)
            for g in pending:
                status = g.get("status")
                if status not in ("waiting", "posted"):
                    continue
                created = g.get("created_at")
                if not created:
                    continue
                try:
                    age = now - created
                except TypeError:
                    age = now.replace(tzinfo=None) - created
                if age.total_seconds() > CHALLENGE_TIMEOUT_SECONDS:
                    print(f"[expire] game_id={g.get('id')} age={age.total_seconds():.0f}s")
                    expire_game(
                        g["id"],
                        "⌛ *انتهت صلاحية التحدّي*\n\n"
                        "لم ينضم أي لاعب خلال دقيقتين.",
                    )
        except Exception as e:
            print(f"⚠️ expiration_checker: {e}")
        time_mod.sleep(15)


def weekly_reset_checker():
    while True:
        try:
            now = datetime.now(timezone.utc)
            target = last_scheduled_reset(now)
            meta = get_meta()
            last = meta.get("last_reset_at")
            if last is not None and getattr(last, "tzinfo", None) is None:
                last = last.replace(tzinfo=timezone.utc)
            if last is None or last < target:
                print(f"[weekly_reset] triggering target={target.isoformat()}")
                try:
                    top = get_leaderboard(25)
                    season_id = target.strftime("%G-W%V")
                    archive_season(season_id, target, top)
                    n = reset_all_points()
                    set_last_reset(target)
                    print(f"[weekly_reset] done — archived={len(top)} reset_users={n}")
                except Exception as e:
                    print(f"⚠️ weekly_reset execute: {e}")
        except Exception as e:
            print(f"⚠️ weekly_reset_checker: {e}")
        time_mod.sleep(300)  


# ============================
# === تحدي XO في مجموعة (Reply Challenge) ===
# ============================

GROUP_CHALLENGE_TRIGGERS = {
    "تحدي xo", "تحدّي xo", "تحديxo", "تحدي اكس او", "تحدّي اكس او",
    "challenge xo", "xo challenge", "xo",
}

GCHAL_SELF_RESPONSES = [
    "ياخي الوحدة صعبة ، بس مو لدرجة إنك تلعب مع نفسك😂 ",
    "لو فزت على نفسك! مين بنعطي النقاط؟ 🤔",
    "تتحدى نفسك! طيب وإذا خسرت بتزعل من نفسك؟ 😂",
    "ترى اللعبة اسمها XO مو XX لازم طرف ثاني يشاركك التحدي",
    "العب مع جدار ولا تلعب مع نفسك ارحم عقلك يا شيخ! 🤣",
    "ما عندنا نقاط للي يفوز على نفسه 😁",
    "وش جوك؟ تبيني أقسم الشاشة نصين عشان تلعب يمين ويسار😅",
    "صدقني لو فزت على نفسك ما راح أحسب لك ولا نقطة، تعب عالفاضي",
    "لا يمكنك تحدّي نفسك 🙂",
]

GCHAL_BOT_RESPONSES = [
    "يا حبيبي أنا الحكم والمشرف تبيني ألعب وأغشش نفسي؟ 😂",
    "أنا بوت مسالم وظيفتي أخدمكم مالي في المشاكل ",
    "البوتات ما تلعب . البوتات تجلد بس! عشان كذا ماني لاعب معك 😌",
    "ودي ألعب معك بس أخاف تفوز علي ويطردني المالك 🏃‍♂️",
    "أنا مشغول أجمع نقاطكم وأحسبها ما عندي وقت للعب",
    "المالك مانعني من اللعب يقول لا تتنمر على المستخدمين 😂",
    "تبيني ألعب؟ أخاف أهزمك وتروح تبلك البوت من القهر 🤣",
    "روح العب مع أخوياك أنا مستواي \"بوتات\" مو تحديات عادية ",
    "🤖 لا يمكن تحدّي البوتات.",
]


def _is_group_challenge_text(m):
    if m.chat.type not in ("group", "supergroup"):
        return False
    if not m.reply_to_message:
        return False
    txt = (m.text or "").strip().lower()
    return txt in GROUP_CHALLENGE_TRIGGERS


@bot.message_handler(func=_is_group_challenge_text, content_types=["text"])
def cmd_group_challenge(message):
    a = message.from_user
    
    if not FEATURES["xo_enabled"] and not is_admin(a.id):
        try:
            bot.reply_to(message, "🔒 لعبة XO متوقفة مؤقتاً في الوقت الحالي.")
        except Exception:
            pass
        return
    
    if not message.reply_to_message:
        return
        
    b = message.reply_to_message.from_user
    if not b:
        return

    if b.is_bot:
        try:
            bot.reply_to(message, random.choice(GCHAL_BOT_RESPONSES))
        except Exception:
            pass
        return
        
    if int(a.id) == int(b.id):
        try:
            bot.reply_to(message, random.choice(GCHAL_SELF_RESPONSES))
        except Exception:
            pass
        return
        
    try:
        banned, reason, until = is_banned(a.id)
        if banned:
            return
    except Exception:
        pass

    a_name = (a.first_name or "لاعب").replace("*", "").replace("_", "").replace("`", "").replace("[", "")
    b_name = (b.first_name or "لاعب").replace("*", "").replace("_", "").replace("`", "").replace("[", "")
    
    text = (
        f"🎮 تحدّي XO\n\n"
        f"❌⭕ {a_name} يتحدّى {b_name}!\n\n"
        f"اختر رمزك يا {a_name}:"
    )
    
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.row(
        types.InlineKeyboardButton("❌ ألعب X (أبدأ أولاً)", callback_data=f"gchal:pick:X:{a.id}:{b.id}"),
        types.InlineKeyboardButton("⭕ ألعب O", callback_data=f"gchal:pick:O:{a.id}:{b.id}")
    )
    kb.row(
        types.InlineKeyboardButton("❌ إلغاء التحدّي", callback_data=f"gchal:cancel:{a.id}")
    )
    
    try:
        bot.reply_to(message, text, reply_markup=kb)
    except Exception as e:
        print(f"⚠️ group_challenge send: {e}")
        try:
            bot.reply_to(message, f"❌ حدث خطأ داخلي يمنع إرسال التحدي: {e}")
        except:
            pass

def handle_group_challenge(call, data):
    parts = data.split(":")
    if len(parts) < 2:
        return
    action = parts[1]
    user_id = call.from_user.id

    if action == "cancel" and len(parts) >= 3:
        try:
            creator_id = int(parts[2])
        except Exception:
            return
        if user_id != creator_id:
            try:
                bot.answer_callback_query(call.id, "هذا التحدّي ليس لك")
            except Exception:
                pass
            return
        try:
            bot.edit_message_text(
                "❌ *أُلغي التحدّي.*",
                call.message.chat.id, call.message.message_id,
                parse_mode="Markdown",
            )
        except Exception:
            pass
        return

    if action == "pick" and len(parts) >= 5:
        symbol = parts[2].upper()
        try:
            creator_id = int(parts[3])
            target_id = int(parts[4])
        except Exception:
            return
        if symbol not in ("X", "O"):
            return
        if user_id != creator_id:
            try:
                bot.answer_callback_query(call.id, "فقط منشئ التحدّي يختار الرمز")
            except Exception:
                pass
            return

        creator_name = call.from_user.first_name or "لاعب"
        target_name = "لاعب"
        try:
            orig = call.message.reply_to_message
            if orig and orig.reply_to_message and orig.reply_to_message.from_user:
                target_name = orig.reply_to_message.from_user.first_name or "لاعب"
        except Exception:
            pass

        try:
            get_or_create_user(creator_id, creator_name,
                               (call.from_user.username or ""))
        except Exception:
            pass

        game_id = secrets.token_urlsafe(8)
        try:
            create_game_symbol(game_id, creator_id, creator_name, symbol)
        except Exception as e:
            print(f"⚠️ create_game_symbol: {e}")
            try:
                bot.answer_callback_query(call.id, "فشل إنشاء المباراة")
            except Exception:
                pass
            return

        chat_id = call.message.chat.id
        msg_id = call.message.message_id
        updates = {"target_id": target_id, "target_name": target_name}
        if symbol == "X":
            updates["x_chat_id"] = chat_id
            updates["x_msg_id"] = msg_id
        else:
            updates["o_chat_id"] = chat_id
            updates["o_msg_id"] = msg_id
        try:
            update_game(game_id, updates)
        except Exception as e:
            print(f"⚠️ update_game gchal: {e}")

        x_name = creator_name if symbol == "X" else target_name
        o_name = creator_name if symbol == "O" else target_name
        header = (
            "🎮 *تحدّي XO*\n"
            f"❌ {_md_escape(x_name)}  ⚔️  ⭕ {_md_escape(o_name)}\n\n"
            f"⏳ بانتظار *{_md_escape(target_name)}* — اضغط أي خانة للانضمام."
        )
        board = board_list("---------")
        kb = board_kb(board, f"pvp:{game_id}")
        try:
            bot.edit_message_text(
                header,
                chat_id, msg_id,
                reply_markup=kb, parse_mode="Markdown",
            )
        except Exception as e:
            print(f"⚠️ render group challenge board: {e}")
        try:
            bot.answer_callback_query(call.id, "تم اختيار رمزك ✅")
        except Exception:
            pass
        return


# ============================
# === رسائل غير متوقعة ===
# ============================

@bot.message_handler(func=lambda m: True, content_types=["text"])
@private_only
def fallback(message):
    uid = message.chat.id

    if not require_not_banned_msg(message):
        return

    # 🔐 معالجة رمز 2FA المعلّق للمالك
    if is_admin(uid):
        pending = get_pending_2fa(uid)
        if pending:
            txt = (message.text or "").strip()
            if txt.lower() in ("/cancel", "إلغاء"):
                cancel_2fa(uid)
                bot.send_message(uid, "❎ تم إلغاء طلب التحقق الثنائي.")
                return
            if verify_totp(txt):
                action = pending.get("action")
                consume_2fa(uid)
                _execute_2fa_action(uid, action)
            else:
                bot.send_message(
                    uid,
                    "❌ رمز غير صحيح. أرسل الرمز الصحيح أو /cancel للإلغاء.",
                )
            return

    # معالجة إدخال بحث المالك إن كان بانتظاره
    if is_admin(uid) and admin_search_waiting.get(uid):
        admin_search_waiting.pop(uid, None)
        query = (message.text or "").strip()
        results = search_users(query)
        _send_admin_search_results(uid, query, results)
        return

    # التقاط نصوص المساعدة من المالك
    if is_admin(uid) and uid in admin_help_state:
        state = admin_help_state[uid]
        txt = (message.text or "").strip()
        admin_help_state.pop(uid, None)
        try: bot.delete_message(uid, message.message_id)
        except: pass
        
        if state["action"] == "wait_title":
            tab_id = f"t_{int(time_mod.time())}"
            set_help_section(tab_id, txt, "يرجى تعديل هذا المحتوى..")
            bot.edit_message_text(
                f"✅ تم إنشاء القسم: {txt}", uid, state["msg_id"],
                reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("✏️ اكتب المحتوى الآن", callback_data=f"admin_help_settext_{tab_id}"))
            )
        elif state["action"] == "wait_content":
            tid = state["tab_id"]
            title = get_help_sections().get(tid, {}).get("title", "قسم")
            set_help_section(tid, title, txt)
            bot.edit_message_text(
                "✅ تم تحديث المحتوى بنجاح!", uid, state["msg_id"],
                reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🔙 رجوع", callback_data="admin_help_list"))
            )
        elif state["action"] == "wait_config":
            key = state["key"]
            val = txt if key != "daily_limit" else int(txt if txt.isdigit() else 150)
            set_bot_config(key, val)
            bot.edit_message_text(
                f"✅ تم تحديث الإعداد بنجاح!", uid, state["msg_id"], 
                reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🔙 رجوع للوحة", callback_data="admin_back"))
            )
        return

    # حارس اليوزر (ما عدا المالك)
    if not is_admin(uid) and not require_username(message):
        return
        
    sess = popcalc_sessions.get(uid)
    if sess:
        handle_popcalc_input(message, sess)
        return
        
    bot.send_message(
        uid,
        "استخدم القائمة 👇",
        reply_markup=start_menu_kb(),
    )


def _parse_popularity(text):
    t = (text or "").strip().lower().replace(",", "").replace("٬", "").replace(" ", "")
    ar2en = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
    t = t.translate(ar2en)
    mult = 1
    if t.endswith("k"):
        mult = 1_000
        t = t[:-1]
    elif t.endswith("m"):
        mult = 1_000_000
        t = t[:-1]
    try:
        val = float(t) * mult
    except Exception:
        return None
    if val < 0:
        return None
    return int(val)


def handle_popcalc_input(message, sess):
    uid = message.chat.id
    value = _parse_popularity(message.text)
    if value is None:
        bot.send_message(uid, "⚠️ أرسل رقماً صحيحاً فقط (مثال: `50000` أو `1.2M`)",
                         parse_mode="Markdown")
        return

    try:
        bot.delete_message(uid, message.message_id)
    except Exception:
        pass

    msg_id = sess.get("msg_id")
    mode = sess.get("mode", "pop")
    if mode == "team":
        title = "⚔️ *حاسبة معركة الفريق*"
        points_fn = team_points
        result_kb = teamcalc_result_kb()
        cancel_kb = teamcalc_cancel_kb()
    else:
        title = "🔥 *حاسبة المعركة الفردية*"
        points_fn = pop_points
        result_kb = popcalc_result_kb()
        cancel_kb = popcalc_cancel_kb()

    if sess["stage"] == "your_pop":
        sess["own_pop"] = value
        sess["own_pts"] = points_fn(value)
        sess["stage"] = "opp_pop"
        try:
            bot.edit_message_text(
                f"{title}\n\n"
                f"✅ شعبيتك: *{value:,}* ({sess['own_pts']} نقطة)\n\n"
                f"2️⃣ الآن أرسل شعبية الخصم كرقم:",
                uid, msg_id,
                reply_markup=cancel_kb, parse_mode="Markdown",
            )
        except Exception:
            pass
        return

    if sess["stage"] == "opp_pop":
        own_pts = sess["own_pts"]
        own_pop = sess["own_pop"]
        opp_pop = value
        opp_pts = points_fn(opp_pop)
        win_gain = own_pts + opp_pts // 2
        loss = own_pts // 2

        if opp_pop > own_pop:
            ratio = opp_pop / max(own_pop, 1)
            if ratio >= 5:
                verdict = (
                    f"⚠️ الخصم أعلى منك بـ {ratio:.0f}× مرة\n"
                    "فرصة الفوز ضعيفة لكن الربح كبير 🔥"
                )
            else:
                verdict = (
                    f"🔴 الخصم أعلى منك بـ {ratio:.1f}× مرة\n"
                    "المعركة صعبة لكن المكافأة جيدة عند الفوز"
                )
        elif opp_pop < own_pop:
            ratio = own_pop / max(opp_pop, 1)
            if ratio >= 5:
                verdict = (
                    f"🟢 أنت أعلى من الخصم بـ {ratio:.0f}× مرة\n"
                    "الفوز متوقع لكن الربح محدود — احذر الخسارة!"
                )
            else:
                verdict = (
                    f"🟡 أنت أعلى من الخصم بـ {ratio:.1f}× مرة\n"
                    "فرصتك جيدة للفوز 💪"
                )
        else:
            verdict = "⚖️ أنتما متعادلان في الشعبية — معركة عادلة!"

        text = (
            "📊 *نتيجة الحساب*\n\n"
            f"{{ شعبيتك: {own_pop:,} }} = *{own_pts}* نقطة\n"
            f"{{ شعبية الخصم: {opp_pop:,} }} = *{opp_pts}* نقطة\n\n"
            f"{verdict}\n\n"
            f"✅ فوز: *+{win_gain}* نقطة\n"
            f"❌ خسارة: *-{loss}* نقطة"
        )
        popcalc_sessions.pop(uid, None)
        try:
            bot.edit_message_text(
                text, uid, msg_id,
                reply_markup=result_kb, parse_mode="Markdown",
            )
        except Exception:
            bot.send_message(uid, text,
                             reply_markup=result_kb, parse_mode="Markdown")
        return


# ============================
# === التشغيل ===
# ============================

if __name__ == "__main__":
    print("🎮 بوت لعبة XO يعمل الآن...")
    try:
        bot.remove_webhook()
        print("✅ تم حذف الـ webhook (إن وجد)")
    except Exception as e:
        print(f"⚠️ تعذر حذف الـ webhook: {e}")

    try:
        me = bot.get_me()
        print(f"🤖 Bot username: @{me.username}  |  id: {me.id}  |  name: {me.first_name}")
        _BOT_USERNAME_CACHE["value"] = me.username or ""
    except Exception as e:
        print(f"⚠️ تعذر جلب معلومات البوت: {e}")

    load_flags()
    print(f"🏁 FEATURES: {FEATURES}")

    setup_bot_commands()

    threading.Thread(target=expiration_checker, daemon=True).start()
    print(f"⏳ فاحص انتهاء الصلاحية يعمل (مدة: {CHALLENGE_TIMEOUT_SECONDS}s)")

    threading.Thread(target=move_timeout_checker, daemon=True).start()
    print(f"⏱️ فاحص مهلة الحركة يعمل (مدة: {MOVE_TIMEOUT_SECONDS}s لكل حركة)")

    threading.Thread(target=quick_match_checker, daemon=True).start()
    print(f"⚡ فاحص Quick Match يعمل (مهلة البحث: {QUICK_MATCH_TIMEOUT_SECONDS}s)")

    try:
        backfill_points()
    except Exception as e:
        print(f"⚠️ backfill_points at startup: {e}")

    try:
        _meta = get_meta()
        if not _meta.get("last_reset_at"):
            _target = last_scheduled_reset(datetime.now(timezone.utc))
            set_last_reset(_target)
            print(f"🗓️ تهيئة last_reset_at = {_target.isoformat()}")
    except Exception as e:
        print(f"⚠️ seed last_reset_at: {e}")

    threading.Thread(target=weekly_reset_checker, daemon=True).start()
    print("🗓️ مجدول التصفير الأسبوعي يعمل (كل جمعة 00:00 بتوقيت الرياض)")

    time_mod.sleep(5)

    bot.infinity_polling(
        timeout=30,
        long_polling_timeout=20,
        restart_on_change=False,
        allowed_updates=[
            "message", "callback_query",
            "inline_query", "chosen_inline_result",
        ],
    )