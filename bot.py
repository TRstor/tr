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
import telebot
from telebot import types

from config import BOT_TOKEN
from firebase_utils import (
    get_or_create_user, record_result, get_user_stats, get_leaderboard,
    create_game, get_game, update_game, delete_game,
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
    uid = call.message.chat.id
    mid = call.message.message_id
    data = call.data or ""

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
    print(f"[PvP] username={username!r} game_id={game_id}")

    text = (
        "✅ *تم إنشاء التحدّي!*\n\n"
        f"📋 معرّف التحدّي:\n`{game_id}`\n\n"
        "👥 لمشاركة التحدّي مع صديقك:\n"
        "1️⃣ اضغط زر *\"📤 مشاركة التحدّي\"* بالأسفل لإرساله في أي دردشة.\n"
        "2️⃣ أو اطلب منه إرسال /start للبوت ثم:\n"
        f"   `/join {game_id}`\n\n"
        "⏳ بانتظار انضمام اللاعب الثاني..."
    )
    kb = types.InlineKeyboardMarkup(row_width=1)

    if username:
        share_text = (
            f"🎮 تحدّيني في لعبة XO!\n"
            f"https://t.me/{username}?start=join_{game_id}"
        )
        # زر يفتح نافذة اختيار دردشة لمشاركة نص الدعوة
        kb.add(types.InlineKeyboardButton(
            "📤 مشاركة التحدّي", switch_inline_query=share_text
        ))
        # زر رابط مباشر (يفتح البوت مباشرةً إن ضغطه صاحب التحدّي للتجربة)
        kb.add(types.InlineKeyboardButton(
            "🔗 فتح الرابط مباشرةً",
            url=f"https://t.me/{username}?start=join_{game_id}",
        ))

    kb.add(
        types.InlineKeyboardButton("❌ إلغاء التحدّي", callback_data=f"pvp:{game_id}:cancel"),
        types.InlineKeyboardButton("🏠 القائمة", callback_data="back_main"),
    )
    sent = bot.edit_message_text(text, uid, mid, reply_markup=kb, parse_mode="Markdown",
                                 disable_web_page_preview=True)
    # حفظ msg_id للاعب X (لاستبدال الرسالة لاحقاً عند بدء اللعبة)
    update_game(game_id, {"x_msg_id": sent.message_id})


def handle_pvp_action(call, data):
    # الصيغة: pvp:<game_id>:<action>[:arg]
    uid = call.message.chat.id
    mid = call.message.message_id

    parts = data.split(":")
    if len(parts) < 3:
        bot.answer_callback_query(call.id)
        return

    game_id = parts[1]
    action = parts[2]

    game = get_game(game_id)
    if not game:
        bot.answer_callback_query(call.id, "المباراة انتهت أو حُذفت")
        try:
            bot.edit_message_text("🏁 انتهت المباراة.", uid, mid, reply_markup=main_menu_kb())
        except Exception:
            pass
        return

    # التحقق أن المستخدم طرف في المباراة
    if uid not in (game.get("player_x_id"), game.get("player_o_id")):
        bot.answer_callback_query(call.id, "لست طرفاً في هذه المباراة")
        return

    if action == "noop":
        bot.answer_callback_query(call.id)
        return

    if action == "cancel":
        # فقط منشئ التحدّي يقدر يلغيه (وقبل انضمام الثاني)
        if uid != game.get("player_x_id"):
            bot.answer_callback_query(call.id, "لا يمكنك إلغاء هذا التحدّي")
            return
        if game.get("status") != "waiting":
            bot.answer_callback_query(call.id, "المباراة بدأت بالفعل")
            return
        delete_game(game_id)
        bot.edit_message_text("❌ تم إلغاء التحدّي.", uid, mid, reply_markup=main_menu_kb())
        return

    if action == "resign":
        if game.get("status") != "playing":
            bot.answer_callback_query(call.id)
            return
        # المستسلم يخسر
        if uid == game.get("player_x_id"):
            winner = PLAYER_O
        else:
            winner = PLAYER_X
        finalize_pvp(game_id, winner, resigned=True)
        return

    if action == "move":
        if game.get("status") != "playing":
            bot.answer_callback_query(call.id, "المباراة غير نشطة")
            return

        try:
            pos = int(parts[3])
        except (IndexError, ValueError):
            bot.answer_callback_query(call.id)
            return

        turn = game.get("turn", PLAYER_X)
        expected_uid = game.get("player_x_id") if turn == PLAYER_X else game.get("player_o_id")
        if uid != expected_uid:
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

        bot.answer_callback_query(call.id)
        return


def refresh_pvp_messages(game_id):
    """تحديث رسالتي اللاعبين X و O باللوحة الجديدة"""
    game = get_game(game_id)
    if not game:
        return
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

    # رسالة نهاية للاعبين
    board = board_list(game["board"])
    kb_end = types.InlineKeyboardMarkup(row_width=1)
    kb_end.add(
        types.InlineKeyboardButton("👥 تحدٍّ جديد", callback_data="menu_pvp"),
        types.InlineKeyboardButton("🏠 القائمة", callback_data="back_main"),
    )
    # لوحة نهائية معطّلة
    final_board_kb = board_kb(board, f"pvp:{game_id}", disabled=True)
    # ندمج لوحة اللعب المعطّلة مع أزرار النهاية
    for row in kb_end.keyboard:
        final_board_kb.keyboard.append(row)

    for player_key, chat_key, msg_key in [
        ("player_x_id", "x_chat_id", "x_msg_id"),
        ("player_o_id", "o_chat_id", "o_msg_id"),
    ]:
        chat_id = game.get(chat_key)
        msg_id = game.get(msg_key)
        viewer = game.get(player_key)
        if not (chat_id and msg_id and viewer):
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

    # تأخير بسيط للسماح لأي نسخة سابقة بالانتهاء (مفيد أثناء redeploy على Render)
    import time as _t
    _t.sleep(5)

    # infinity_polling يعيد المحاولة تلقائياً عند الأخطاء مع restart_on_change=False
    bot.infinity_polling(
        timeout=30,
        long_polling_timeout=20,
        restart_on_change=False,
    )
