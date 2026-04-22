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
from datetime import datetime, timezone, timedelta

import telebot
from telebot import types

from config import BOT_TOKEN
from firebase_utils import (
    get_or_create_user, record_result, get_user_stats, get_leaderboard,
    create_game, create_game_symbol, get_game, update_game, delete_game,
    get_pending_games,
)

if not BOT_TOKEN:
    print("❌ يرجى تعيين BOT_TOKEN في متغيرات البيئة")
    raise SystemExit(1)

bot = telebot.TeleBot(BOT_TOKEN, parse_mode=None)

# اسم البوت — يُجلب بشكل كسول أول مرة فقط
_BOT_USERNAME_CACHE = {"value": None}


def get_bot_username():
    """جلب اسم البوت مع تخزين النتيجة."""
    if _BOT_USERNAME_CACHE["value"] is not None:
        return _BOT_USERNAME_CACHE["value"]
    try:
        _BOT_USERNAME_CACHE["value"] = bot.get_me().username or ""
    except Exception as e:
        print(f"⚠️ تعذر جلب اسم البوت: {e}")
        _BOT_USERNAME_CACHE["value"] = ""
    return _BOT_USERNAME_CACHE["value"]

# مباريات ضد البوت (بالذاكرة فقط)
# {user_id: {"board": list[9], "difficulty": "easy"|"hard", "msg_id": int}}
bot_games = {}

EMPTY = "-"
PLAYER_X = "X"
PLAYER_O = "O"

# إذا لم تبدأ المباراة خلال هذه المدة، تُلغى تلقائياً
CHALLENGE_TIMEOUT_SECONDS = 120  # دقيقتان

WIN_LINES = [
    (0, 1, 2), (3, 4, 5), (6, 7, 8),  # صفوف
    (0, 3, 6), (1, 4, 7), (2, 5, 8),  # أعمدة
    (0, 4, 8), (2, 4, 6),             # أقطار
]


# ============================
# === منطق اللعبة ===
# ============================

def check_winner(board):
    """يرجع 'X' أو 'O' إذا فاز، 'draw' إذا تعادل، None إذا لم تنته اللعبة"""
    for a, b, c in WIN_LINES:
        if board[a] != EMPTY and board[a] == board[b] == board[c]:
            return board[a]
    if EMPTY not in board:
        return "draw"
    return None


def available_moves(board):
    return [i for i, v in enumerate(board) if v == EMPTY]


def minimax(board, is_maximizing, ai_symbol, human_symbol):
    """خوارزمية Minimax — البوت هو المحاول تعظيم"""
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
    """أفضل حركة للبوت بنمط Minimax"""
    best_score = -2
    best = None
    moves = available_moves(board)
    random.shuffle(moves)  # لتنوّع الحركات عند تساوي النقاط
    for m in moves:
        board[m] = ai_symbol
        score = minimax(board, False, ai_symbol, human_symbol)
        board[m] = EMPTY
        if score > best_score:
            best_score = score
            best = m
    return best


def best_move_easy(board):
    """حركة عشوائية"""
    moves = available_moves(board)
    return random.choice(moves) if moves else None


# ============================
# === الواجهات (لوحات الأزرار) ===
# ============================

def main_menu_kb():
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(
        types.InlineKeyboardButton("🤖 لعب ضد البوت", callback_data="menu_bot"),
        types.InlineKeyboardButton("👥 لعب ضد صديق", callback_data="menu_pvp"),
        types.InlineKeyboardButton("📊 إحصائياتي", callback_data="menu_stats"),
        types.InlineKeyboardButton("🏆 لوحة الشرف", callback_data="menu_leaderboard"),
        types.InlineKeyboardButton("ℹ️ كيف تلعب", callback_data="menu_help"),
    )
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
    """
    prefix: 'bot' للحركات ضد البوت، 'pvp_{game_id}' للـ PvP
    """
    kb = types.InlineKeyboardMarkup(row_width=3)
    row = []
    for i, cell in enumerate(board):
        if cell == PLAYER_X:
            label = "❌"
        elif cell == PLAYER_O:
            label = "⭕"
        else:
            label = "·"
        # إذا انتهت اللعبة أو الخانة مأخوذة، نجعل الـ callback نفس الشيء لكن نتجاهله
        if disabled or cell != EMPTY:
            cb = f"{prefix}:noop"
        else:
            cb = f"{prefix}:move:{i}"
        row.append(types.InlineKeyboardButton(label, callback_data=cb))
        if len(row) == 3:
            kb.row(*row)
            row = []
    # أزرار تحكّم
    kb.row(
        types.InlineKeyboardButton("🔄 استسلام/إنهاء", callback_data=f"{prefix}:resign"),
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


def fmt_pvp_game(game, viewer_id):
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

    # playing
    your_turn = (
        (turn == PLAYER_X and viewer_id == game.get("player_x_id")) or
        (turn == PLAYER_O and viewer_id == game.get("player_o_id"))
    )
    turn_symbol = "❌" if turn == PLAYER_X else "⭕"
    turn_name = x_name if turn == PLAYER_X else o_name
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
def cmd_start(message):
    uid = message.chat.id
    name = message.from_user.first_name or "لاعب"
    get_or_create_user(uid, name)

    # التعامل مع رابط الانضمام لتحدٍّ: /start join_GAMEID
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) == 2 and parts[1].startswith("join_"):
        game_id = parts[1][len("join_"):]
        handle_join_game(uid, name, game_id)
        return

    text = (
        f"أهلاً {name}! 👋\n"
        "مرحباً في بوت *لعبة XO* 🎮\n\n"
        "اختر من القائمة:"
    )
    bot.send_message(uid, text, reply_markup=main_menu_kb(), parse_mode="Markdown")


@bot.message_handler(commands=["help"])
def cmd_help(message):
    bot.send_message(message.chat.id, help_text(), parse_mode="Markdown")


@bot.message_handler(commands=["menu"])
def cmd_menu(message):
    bot.send_message(message.chat.id, "القائمة الرئيسية:", reply_markup=main_menu_kb())


@bot.message_handler(commands=["join"])
def cmd_join(message):
    """الانضمام يدوياً لتحدٍّ عبر المعرّف: /join <game_id>"""
    uid = message.chat.id
    name = message.from_user.first_name or "لاعب"
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        bot.send_message(
            uid,
            "❌ استخدم الصيغة: `/join CODE`\n(استبدل CODE بمعرّف التحدّي الذي أعطاك إياه صديقك)",
            parse_mode="Markdown",
        )
        return
    game_id = parts[1].strip()
    handle_join_game(uid, name, game_id)


def help_text():
    return (
        "ℹ️ *كيف تلعب XO:*\n\n"
        "- اللوحة 3×3، أول لاعب يكمل ثلاث خانات متتالية (أفقي/عمودي/قطري) يفوز.\n"
        "- أنت دائماً ❌ وتبدأ أولاً ضد البوت.\n"
        "- في التحدّي بين الأصدقاء: منشئ التحدّي ❌ والمنضم ⭕.\n\n"
        "*الأوامر:*\n"
        "/start - البداية\n"
        "/menu - القائمة الرئيسية\n"
        "/help - هذه الرسالة"
    )


# ============================
# === انضمام لتحدي PvP ===
# ============================

def handle_join_game(uid, name, game_id):
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

    get_or_create_user(uid, name)

    # إرسال رسالة اللوحة للاعب O
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

    # تحديث رسالة اللاعب X ليعرف أن الخصم انضمّ
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
        _dispatch(call)
    except Exception as e:
        msg = str(e)
        if "message is not modified" in msg:
            bot.answer_callback_query(call.id)
            return
        print(f"❌ خطأ: {e}")
        try:
            bot.answer_callback_query(call.id, "❌ حدث خطأ، حاول مرة أخرى")
        except Exception:
            pass


def _dispatch(call):
    data = call.data or ""

    # Callbacks القادمة من رسائل Inline (ليس لها call.message) تخص PvP فقط
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

    # === قوائم عامة ===
    if data == "back_main":
        # عند الرجوع من لعبة جارية ضد البوت، نحذفها
        bot_games.pop(uid, None)
        bot.edit_message_text("القائمة الرئيسية:", uid, mid, reply_markup=main_menu_kb())
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

    if data == "menu_stats":
        user = get_user_stats(uid) or get_or_create_user(uid, call.from_user.first_name or "")
        text = render_stats(user)
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("🔙 رجوع", callback_data="back_main"))
        bot.edit_message_text(text, uid, mid, reply_markup=kb, parse_mode="Markdown")
        return

    if data == "menu_leaderboard":
        board = get_leaderboard(10)
        text = render_leaderboard(board, uid)
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("🔙 رجوع", callback_data="back_main"))
        bot.edit_message_text(text, uid, mid, reply_markup=kb, parse_mode="Markdown")
        return

    if data == "menu_help":
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("🔙 رجوع", callback_data="back_main"))
        bot.edit_message_text(help_text(), uid, mid, reply_markup=kb, parse_mode="Markdown")
        return

    # === بدء لعبة ضد البوت ===
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

    # === حركات ضد البوت ===
    if data.startswith("bot:"):
        handle_bot_move(call, data)
        return

    # === PvP: إنشاء تحدٍّ ===
    if data == "pvp_create":
        handle_pvp_create(call)
        return

    # === حركات PvP ===
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

    # أجزاء الـ callback: bot:move:<pos> | bot:noop | bot:resign
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

    # حركة اللاعب
    board[pos] = PLAYER_X
    result = check_winner(board)
    if result:
        finish_bot_game(uid, mid, game, board, result)
        return

    # حركة البوت
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

    # استمرار اللعبة
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
# === PvP ===
# ============================

def handle_pvp_create(call):
    uid = call.message.chat.id
    mid = call.message.message_id
    name = call.from_user.first_name or "لاعب"
    get_or_create_user(uid, name)

    game_id = secrets.token_urlsafe(8)
    create_game(game_id, uid, name, uid)

    username = get_bot_username()
    print(f"[PvP] created game_id={game_id} by uid={uid} username={username!r}")

    text = (
        "✅ *تم إنشاء تحدٍّ جديد!*\n\n"
        "📤 اضغط زر *\"مشاركة التحدّي\"* ثم اختر محادثة صديقك — "
        "ستُنشر لوحة اللعبة هناك وتلعبان في تلك المحادثة مباشرةً.\n\n"
        f"⏳ إذا لم يبدأ التحدّي خلال {CHALLENGE_TIMEOUT_SECONDS // 60} دقيقتين، "
        "سيُلغى تلقائياً."
    )
    kb = types.InlineKeyboardMarkup(row_width=1)

    # الزر الأساسي: switch_inline_query مع game_id كـ query
    # عند ضغطه، يفتح تيليجرام قائمة المحادثات ويضع @bot <game_id> في الإدخال.
    # ثم inline_handler يعرض بطاقة للإرسال، وعند إرسالها تُنشر رسالة اللعبة.
    kb.add(types.InlineKeyboardButton(
        "📤 مشاركة التحدّي في محادثة",
        switch_inline_query=game_id,
    ))
    kb.add(
        types.InlineKeyboardButton("❌ إلغاء التحدّي", callback_data=f"pvp:{game_id}:cancel"),
        types.InlineKeyboardButton("🏠 القائمة", callback_data="back_main"),
    )
    sent = bot.edit_message_text(
        text, uid, mid, reply_markup=kb,
        parse_mode="Markdown", disable_web_page_preview=True,
    )
    update_game(game_id, {"x_msg_id": sent.message_id})


def handle_pvp_action(call, data):
    # الصيغة: pvp:<game_id>:<action>[:arg]
    parts = data.split(":")
    if len(parts) < 3:
        try: bot.answer_callback_query(call.id)
        except Exception: pass
        return

    game_id = parts[1]
    action = parts[2]
    user_id = call.from_user.id
    user_name = call.from_user.first_name or "لاعب"
    is_inline = call.message is None

    # إذا وصلنا callback من رسالة inline، نحفظ inline_message_id لأول مرة
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
        try: bot.answer_callback_query(call.id)
        except Exception: pass
        return

    # --- إلغاء التحدّي ---
    if action == "cancel":
        creator_id = game.get("player_x_id") or game.get("player_o_id")
        if user_id != creator_id:
            bot.answer_callback_query(call.id, "لا يمكنك إلغاء هذا التحدّي")
            return
        if status not in ("waiting", "posted"):
            bot.answer_callback_query(call.id, "المباراة بدأت بالفعل")
            return
        expire_game(game_id, "❌ *ألغى المنشئ التحدّي.*")
        try: bot.answer_callback_query(call.id, "تم الإلغاء")
        except Exception: pass
        return

    # --- استسلام ---
    if action == "resign":
        if status != "playing":
            bot.answer_callback_query(call.id)
            return
        if user_id not in (game.get("player_x_id"), game.get("player_o_id")):
            bot.answer_callback_query(call.id, "لست طرفاً في هذه المباراة")
            return
        winner = PLAYER_O if user_id == game.get("player_x_id") else PLAYER_X
        finalize_pvp(game_id, winner, resigned=True)
        try: bot.answer_callback_query(call.id)
        except Exception: pass
        return

    # --- انضمام تلقائي: أول نقرة من مستخدم ليس هو المنشئ ---
    if action == "move" and status in ("waiting", "posted"):
        creator_id = game.get("player_x_id") or game.get("player_o_id")
        creator_symbol = PLAYER_X if game.get("player_x_id") else PLAYER_O

        if user_id == creator_id:
            bot.answer_callback_query(call.id, "⏳ انتظر انضمام الخصم")
            return

        # المنضمّ يأخذ الرمز المعاكس
        get_or_create_user(user_id, user_name)
        updates = {"status": "playing", "turn": PLAYER_X}
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

    # --- حركة عادية ---
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

        update_game(game_id, {
            "board": board_str(board),
            "turn": next_turn,
        })

        if result:
            finalize_pvp(game_id, result)
        else:
            refresh_pvp_messages(game_id)

        try: bot.answer_callback_query(call.id)
        except Exception: pass
        return


def refresh_pvp_messages(game_id):
    """تحديث كل الرسائل المرتبطة بالمباراة (inline + DM للاعبَين إن وُجد)."""
    game = get_game(game_id)
    if not game:
        return

    # 1) الرسالة الـ inline (الأهم الآن)
    if game.get("inline_message_id"):
        render_inline_board(game_id)

    # 2) رسائل DM للاعبين (نمط /join القديم)
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
        # إذا المنشئ شارك التحدّي عبر inline، رسالة X في DM ما عادت لوحة لعب — نتجاهلها
        if player_key == "player_x_id" and game.get("inline_message_id"):
            continue
        try:
            bot.edit_message_text(
                fmt_pvp_game(game, viewer),
                chat_id=chat_id, message_id=msg_id,
                reply_markup=kb, parse_mode="Markdown",
            )
        except Exception as e:
            if "message is not modified" not in str(e):
                print(f"⚠️ فشل تحديث رسالة {player_key}: {e}")


def finalize_pvp(game_id, winner, resigned=False):
    """إنهاء مباراة PvP وتحديث الإحصائيات ورسائل اللاعبين"""
    game = get_game(game_id)
    if not game:
        return

    update_game(game_id, {"status": "finished", "winner": winner})
    game = get_game(game_id)

    px = game.get("player_x_id")
    po = game.get("player_o_id")

    # تحديث الإحصائيات
    if winner == "draw":
        if px: record_result(px, "pvp", "draw")
        if po: record_result(po, "pvp", "draw")
    elif winner == PLAYER_X:
        if px: record_result(px, "pvp", "win")
        if po: record_result(po, "pvp", "loss")
    elif winner == PLAYER_O:
        if po: record_result(po, "pvp", "win")
        if px: record_result(px, "pvp", "loss")

    # 1) الرسالة الـ inline
    if game.get("inline_message_id"):
        render_inline_board(game_id)

    # 2) رسائل DM للاعبين (نمط /join القديم)
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
        # إذا المباراة كانت inline، تجاهل رسالة X في DM
        if player_key == "player_x_id" and game.get("inline_message_id"):
            continue

        suffix = ""
        if resigned:
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
                fmt_pvp_game(game, viewer) + suffix,
                chat_id=chat_id, message_id=msg_id,
                reply_markup=final_board_kb, parse_mode="Markdown",
            )
        except Exception as e:
            if "message is not modified" not in str(e):
                print(f"⚠️ فشل تحديث رسالة النهاية {player_key}: {e}")


# ============================
# === عرض الإحصائيات ولوحة الشرف ===
# ============================

def render_stats(user):
    total_games = user.get("wins", 0) + user.get("losses", 0) + user.get("draws", 0)
    wr = (user.get("wins", 0) / total_games * 100) if total_games else 0
    return (
        f"📊 *إحصائيات {user.get('name','لاعب')}*\n\n"
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


def render_leaderboard(users, viewer_id):
    if not users:
        return "🏆 *لوحة الشرف*\n\nلا توجد بيانات بعد. كن أول الفائزين!"
    lines = ["🏆 *لوحة الشرف - أعلى 10*\n"]
    medals = ["🥇", "🥈", "🥉"]
    for i, u in enumerate(users):
        prefix = medals[i] if i < 3 else f"{i+1}."
        me = " 👈 أنت" if str(u.get("user_id")) == str(viewer_id) else ""
        lines.append(
            f"{prefix} {u.get('name','لاعب')} — "
            f"فوز {u.get('wins',0)} / خسارة {u.get('losses',0)} / تعادل {u.get('draws',0)}{me}"
        )
    return "\n".join(lines)


# ============================
# === Inline Mode (اللعب في محادثة صديقك) ===
# ============================

@bot.inline_handler(func=lambda q: True)
def on_inline_query(inline_query):
    """
    حالتان:
      (أ) الاستعلام = game_id موجود أنشأه المستخدم من DM (زر مشاركة التحدّي)
          → نرجع بطاقة واحدة بلوحة اللعب.
      (ب) الاستعلام فاضي أو 'xo' → نرجع بطاقتين: "ألعب كـ ❌" و "ألعب كـ ⭕".
          عند اختيار المستخدم واحدة، نُنشئ مباراة جديدة ونعلنها فوراً في المحادثة.
    """
    uid = inline_query.from_user.id
    name = inline_query.from_user.first_name or "لاعب"
    q = (inline_query.query or "").strip()

    results = []

    # --- (أ) استعلام بمعرّف مباراة قائمة ---
    if q:
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
            try:
                bot.answer_inline_query(
                    inline_query.id, results, cache_time=0, is_personal=True,
                )
            except Exception as e:
                print(f"⚠️ answer_inline_query (existing): {e}")
            return

    # --- (ب) استعلام فاضي أو "xo" → اعرض بطاقتي اختيار الرمز ---
    q_lower = q.lower()
    if q == "" or "xo" in q_lower or "اكس" in q or "او" in q:
        get_or_create_user(uid, name)

        # ننشئ مباراتين جاهزتين (واحدة لكل خيار). غير المستعملة ستُلغى تلقائياً.
        gid_x = secrets.token_urlsafe(8)
        gid_o = secrets.token_urlsafe(8)
        create_game_symbol(gid_x, uid, name, "X")
        create_game_symbol(gid_o, uid, name, "O")

        # بطاقة: ألعب كـ ❌
        text_x = (
            f"🎮 *لعبة XO*\n❌ {name}  ⚔️  ⭕ بانتظار لاعب...\n\n"
            "⭕ اضغط أي مربع للانضمام كـ ⭕!"
        )
        kb_x = board_kb([EMPTY] * 9, f"pvp:{gid_x}")

        # بطاقة: ألعب كـ ⭕
        text_o = (
            f"🎮 *لعبة XO*\n❌ بانتظار لاعب...  ⚔️  ⭕ {name}\n\n"
            "❌ اضغط أي مربع للانضمام كـ ❌ (وتبدأ أولاً)!"
        )
        kb_o = board_kb([EMPTY] * 9, f"pvp:{gid_o}")

        results.append(types.InlineQueryResultArticle(
            id=gid_x,
            title="❌ ألعب كـ X (أبدأ أولاً)",
            description="إرسال تحدّي وأنت تلعب بالـ ❌",
            input_message_content=types.InputTextMessageContent(
                message_text=text_x, parse_mode="Markdown",
            ),
            reply_markup=kb_x,
        ))
        results.append(types.InlineQueryResultArticle(
            id=gid_o,
            title="⭕ ألعب كـ O",
            description="إرسال تحدّي وأنت تلعب بالـ ⭕ (الخصم يبدأ)",
            input_message_content=types.InputTextMessageContent(
                message_text=text_o, parse_mode="Markdown",
            ),
            reply_markup=kb_o,
        ))

        try:
            bot.answer_inline_query(
                inline_query.id, results, cache_time=0, is_personal=True,
            )
        except Exception as e:
            print(f"⚠️ answer_inline_query (choice): {e}")
        return

    # --- حالة مجهولة: رسالة إرشادية ---
    results.append(types.InlineQueryResultArticle(
        id="help",
        title="اكتب XO لبدء تحدٍّ",
        description="اكتب 'XO' بعد اسم البوت لعرض خياري ❌ و ⭕",
        input_message_content=types.InputTextMessageContent(
            message_text="اكتب XO بعد اسم البوت لبدء تحدٍّ.",
        ),
    ))
    try:
        bot.answer_inline_query(
            inline_query.id, results, cache_time=0, is_personal=True,
        )
    except Exception as e:
        print(f"⚠️ answer_inline_query (help): {e}")


@bot.chosen_inline_handler(func=lambda c: True)
def on_chosen_inline(chosen):
    """
    يُستدعى بعد أن يرسل المستخدم البطاقة فعلياً في محادثة.
    الآن نعرف inline_message_id ونستبدل رسالة المعاينة بلوحة اللعب.
    ⚠️ يحتاج تفعيل Inline feedback في BotFather (/setinlinefeedback = 100%).
    """
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

    # تحديث رسالة المنشئ في محادثة البوت
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
    """تحديث رسالة المنشئ في DM لإبلاغه بأن الخصم انضمّ وبدأت اللعبة."""
    game = get_game(game_id)
    if not game:
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
    kb.add(
        types.InlineKeyboardButton("🏳️ استسلام", callback_data=f"pvp:{game_id}:resign"),
        types.InlineKeyboardButton("🏠 القائمة", callback_data="back_main"),
    )
    try:
        bot.edit_message_text(
            text, chat_id=chat_id, message_id=msg_id,
            reply_markup=kb, parse_mode="Markdown",
        )
    except Exception as e:
        if "message is not modified" not in str(e):
            print(f"⚠️ notify creator: {e}")


def render_inline_board(game_id):
    """رسم لوحة اللعبة في الرسالة الـ inline المنشورة في محادثة صديقك."""
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
        header += f"\n⏳ الدور على {nm} ({sym})"
    elif status == "finished":
        winner = game.get("winner")
        if winner == "draw":
            header += "\n🤝 تعادل!"
        elif winner in (PLAYER_X, PLAYER_O):
            wn = x_name if winner == PLAYER_X else o_name
            sym = "❌" if winner == PLAYER_X else "⭕"
            header += f"\n🏆 الفائز: {wn} ({sym})"

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
    """إنهاء تحدٍّ قسرياً (انتهاء مهلة / إلغاء / إلخ) وإبلاغ الأطراف."""
    game = get_game(game_id)
    if not game:
        return

    # تحديث الرسالة المنشورة في محادثة الصديق (إن وُجدت)
    if game.get("inline_message_id"):
        try:
            bot.edit_message_text(
                f"🎮 *تحدّي XO*\n\n{reason}",
                inline_message_id=game["inline_message_id"],
                parse_mode="Markdown",
            )
        except Exception as e:
            if "message is not modified" not in str(e):
                print(f"⚠️ expire inline: {e}")

    # تحديث رسالة المنشئ في DM
    if game.get("x_chat_id") and game.get("x_msg_id"):
        try:
            bot.edit_message_text(
                f"{reason}\n\nأرسل /menu للبدء من جديد.",
                chat_id=game["x_chat_id"],
                message_id=game["x_msg_id"],
                reply_markup=main_menu_kb(),
            )
        except Exception as e:
            if "message is not modified" not in str(e):
                print(f"⚠️ expire DM X: {e}")

    # تحديث رسالة اللاعب O (إن وُجدت بنمط /join القديم)
    if game.get("o_chat_id") and game.get("o_msg_id"):
        try:
            bot.edit_message_text(
                f"{reason}",
                chat_id=game["o_chat_id"],
                message_id=game["o_msg_id"],
                reply_markup=main_menu_kb(),
            )
        except Exception as e:
            if "message is not modified" not in str(e):
                print(f"⚠️ expire DM O: {e}")

    delete_game(game_id)


def expiration_checker():
    """ثريد خلفي يفحص التحدّيات منتهية الصلاحية كل 15 ثانية."""
    while True:
        try:
            pending = get_pending_games()
            now = datetime.now(timezone.utc)
            for g in pending:
                status = g.get("status")
                # نحذف فقط التحدّيات التي لم تبدأ
                if status not in ("waiting", "posted"):
                    continue
                created = g.get("created_at")
                if not created:
                    continue
                try:
                    age = now - created
                except TypeError:
                    # created بدون tz
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


# ============================
# === رسائل غير متوقعة ===
# ============================

@bot.message_handler(func=lambda m: True, content_types=["text"])
def fallback(message):
    bot.send_message(
        message.chat.id,
        "استخدم القائمة 👇",
        reply_markup=main_menu_kb(),
    )


# ============================
# === التشغيل ===
# ============================

if __name__ == "__main__":
    print("🎮 بوت لعبة XO يعمل الآن...")
    # حذف أي webhook قديم لتفادي خطأ 409 Conflict عند استخدام getUpdates
    try:
        bot.remove_webhook()
        print("✅ تم حذف الـ webhook (إن وجد)")
    except Exception as e:
        print(f"⚠️ تعذر حذف الـ webhook: {e}")

    # محاولة جلب اسم البوت مبكراً (للتشخيص)
    try:
        me = bot.get_me()
        print(f"🤖 Bot username: @{me.username}  |  id: {me.id}  |  name: {me.first_name}")
        _BOT_USERNAME_CACHE["value"] = me.username or ""
    except Exception as e:
        print(f"⚠️ تعذر جلب معلومات البوت: {e}")

    # ثريد فحص انتهاء صلاحية التحدّيات
    threading.Thread(target=expiration_checker, daemon=True).start()
    print(f"⏳ فاحص انتهاء الصلاحية يعمل (مدة: {CHALLENGE_TIMEOUT_SECONDS}s)")

    # تأخير بسيط للسماح لأي نسخة سابقة بالانتهاء (مفيد أثناء redeploy على Render)
    time_mod.sleep(5)

    # infinity_polling مع تفعيل تلقّي تحديثات inline و chosen_inline_result
    bot.infinity_polling(
        timeout=30,
        long_polling_timeout=20,
        restart_on_change=False,
        allowed_updates=[
            "message", "callback_query",
            "inline_query", "chosen_inline_result",
        ],
    )
