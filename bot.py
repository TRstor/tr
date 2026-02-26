#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
بوت تيليجرام - إدارة العمليات والاشتراكات
"""

import telebot
from telebot import types
import threading
import time
from datetime import datetime, timedelta
from config import BOT_TOKEN, ADMIN_ID
from firebase_utils import (
    add_operation, get_operations, get_operation_by_id, delete_operation,
    add_email, get_emails, get_email_by_id, delete_email, update_email,
    add_client, get_clients, get_client_by_id, delete_client,
    update_client, count_clients, search_clients_by_name, get_all_clients_with_emails,
    get_user, create_user, set_user_password, verify_user_password
)

# === تهيئة البوت ===
if not BOT_TOKEN:
    print("❌ يرجى تعيين BOT_TOKEN في متغيرات البيئة")
    exit(1)

bot = telebot.TeleBot(BOT_TOKEN)

# حالات المستخدمين (لتتبع المحادثة)
user_states = {}

# المستخدمون المسجلون دخول (مع وقت انتهاء الجلسة)
authenticated_users = {}  # {user_id: expiry_time}

# مدة الجلسة بالدقائق
SESSION_TIMEOUT_MINUTES = 3

def login_user(uid):
    """تسجيل دخول المستخدم مع وقت انتهاء"""
    authenticated_users[uid] = datetime.now() + timedelta(minutes=SESSION_TIMEOUT_MINUTES)

def is_authenticated(uid):
    """التحقق من تسجيل الدخول وصلاحية الجلسة"""
    if uid not in authenticated_users:
        return False
    if datetime.now() > authenticated_users[uid]:
        del authenticated_users[uid]
        return False
    # تجديد الجلسة عند كل تفاعل
    authenticated_users[uid] = datetime.now() + timedelta(minutes=SESSION_TIMEOUT_MINUTES)
    return True

# دالة مساعدة لتهريب رموز Markdown
def escape_md(text):
    """تهريب الرموز الخاصة لتجنب خطأ Markdown"""
    if not text:
        return text
    for char in ['_', '*', '`', '[']:
        text = str(text).replace(char, f'\\{char}')
    return text

# ============================
# === القائمة الرئيسية ===
# ============================

def main_menu():
    """إنشاء القائمة الرئيسية"""
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(
        types.InlineKeyboardButton("📋 إدارة العمليات", callback_data="menu_operations"),
        types.InlineKeyboardButton("📧 إدارة الاشتراكات", callback_data="menu_subscriptions"),
        types.InlineKeyboardButton("⚙️ الإعدادات", callback_data="menu_settings")
    )
    return kb

def operations_menu():
    """قائمة العمليات"""
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(
        types.InlineKeyboardButton("➕ إنشاء عملية جديدة", callback_data="op_create"),
        types.InlineKeyboardButton("📄 عرض العمليات", callback_data="op_list"),
        types.InlineKeyboardButton("🔙 رجوع", callback_data="back_main")
    )
    return kb

def subscriptions_menu():
    """قائمة الاشتراكات"""
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(
        types.InlineKeyboardButton("➕ إضافة إيميل جديد", callback_data="email_create"),
        types.InlineKeyboardButton("📋 عرض الإيميلات", callback_data="email_list"),
        types.InlineKeyboardButton("� البحث عن عميل", callback_data="client_search"),
        types.InlineKeyboardButton("�🔙 رجوع", callback_data="back_main")
    )
    return kb

# ============================
# === أوامر البوت ===
# ============================

@bot.message_handler(commands=['start'])
def cmd_start(message):
    """رسالة البداية مع نظام تسجيل الدخول"""
    uid = message.chat.id
    user_states.pop(uid, None)
    
    user = get_user(uid)
    
    if user is None:
        # مستخدم جديد - إنشاء حساب
        name = message.from_user.first_name or ""
        create_user(uid, name)
        login_user(uid)
        
        text = (
            f"أهلاً وسهلاً {escape_md(name)}! 👋\n\n"
            "تم إنشاء حسابك بنجاح ✅\n\n"
            "🔐 يمكنك تعيين كلمة مرور لحماية حسابك من الإعدادات.\n\n"
            "اختر القسم المطلوب:"
        )
        bot.send_message(uid, text, reply_markup=main_menu(), parse_mode="Markdown")
    
    elif user.get("password"):
        # لديه كلمة مرور - طلب إدخالها
        if uid in authenticated_users and is_authenticated(uid):
            # مسجل دخول بالفعل
            text = (
                f"مرحباً بك مجدداً! 👋\n\n"
                "اختر القسم المطلوب:"
            )
            bot.send_message(uid, text, reply_markup=main_menu())
        else:
            # يحتاج تسجيل دخول
            user_states[uid] = {"action": "login_password"}
            bot.send_message(uid, "🔒 أدخل *كلمة المرور* للدخول:", parse_mode="Markdown")
    
    else:
        # لديه حساب بدون كلمة مرور - دخول مباشر
        login_user(uid)
        name = user.get("name", "")
        text = (
            f"أهلاً وسهلاً {escape_md(name)}! 👋\n\n"
            "اختر القسم المطلوب:"
        )
        bot.send_message(uid, text, reply_markup=main_menu(), parse_mode="Markdown")

@bot.message_handler(commands=['help'])
def cmd_help(message):
    """رسالة المساعدة"""
    text = (
        "📖 *دليل الاستخدام:*\n\n"
        "*📋 إدارة العمليات:*\n"
        "• إنشاء عمليات جديدة وحفظها\n"
        "• عرض جميع العمليات المسجلة\n"
        "• حذف أي عملية منتهية\n\n"
        "*📧 إدارة الاشتراكات:*\n"
        "• إضافة إيميلات أساسية\n"
        "• إضافة عملاء تحت كل إيميل (4-5 عملاء)\n"
        "• تسجيل بيانات كل عميل:\n"
        "  - الاسم\n"
        "  - الرقم\n"
        "  - تاريخ البداية\n"
        "  - تاريخ الانتهاء\n\n"
        "للبدء أرسل /start"
    )
    bot.send_message(message.chat.id, text, parse_mode="Markdown")

# ============================
# === معالجة الأزرار ===
# ============================

@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    uid = call.message.chat.id
    mid = call.message.message_id
    data = call.data

    try:
        _handle_callback_data(call, uid, mid, data)
    except Exception as e:
        error_msg = str(e)
        if "message is not modified" in error_msg:
            pass  # تجاهل هذا الخطأ
        else:
            print(f"❌ خطأ: {error_msg}")
            bot.answer_callback_query(call.id, "❌ حدث خطأ، حاول مرة أخرى")

def _handle_callback_data(call, uid, mid, data):

    # === القوائم الرئيسية ===
    if data == "back_main":
        user_states.pop(uid, None)
        bot.edit_message_text("اختر القسم المطلوب:", uid, mid, reply_markup=main_menu())

    elif data == "menu_subscriptions":
        if not is_authenticated(uid):
            bot.answer_callback_query(call.id, "🔒 انتهت الجلسة، أرسل /start")
            return
        bot.edit_message_text("📧 *إدارة الاشتراكات*\n\nاختر الإجراء:", uid, mid,
                              reply_markup=subscriptions_menu(), parse_mode="Markdown")

    elif data == "menu_operations":
        if not is_authenticated(uid):
            bot.answer_callback_query(call.id, "🔒 انتهت الجلسة، أرسل /start")
            return
        bot.edit_message_text("📋 *إدارة العمليات*\n\nاختر الإجراء:", uid, mid,
                              reply_markup=operations_menu(), parse_mode="Markdown")

    # === الإعدادات ===
    elif data == "menu_settings":
        if not is_authenticated(uid):
            bot.answer_callback_query(call.id, "🔒 انتهت الجلسة، أرسل /start")
            return
        user = get_user(uid)
        has_password = bool(user and user.get("password"))
        
        kb = types.InlineKeyboardMarkup(row_width=1)
        if has_password:
            kb.add(
                types.InlineKeyboardButton("🔑 تغيير كلمة المرور", callback_data="settings_change_pass"),
                types.InlineKeyboardButton("🗑 حذف كلمة المرور", callback_data="settings_remove_pass"),
            )
        else:
            kb.add(
                types.InlineKeyboardButton("🔐 تعيين كلمة مرور", callback_data="settings_set_pass"),
            )
        kb.add(types.InlineKeyboardButton("🔙 رجوع", callback_data="back_main"))
        
        status = "✅ محمي بكلمة مرور" if has_password else "⚠️ بدون كلمة مرور"
        bot.edit_message_text(f"⚙️ *الإعدادات*\n\n🔒 حالة الحساب: {status}", uid, mid,
                              reply_markup=kb, parse_mode="Markdown")

    elif data == "settings_set_pass":
        user_states[uid] = {"action": "set_password"}
        bot.edit_message_text("🔐 أرسل *كلمة المرور* الجديدة:", uid, mid, parse_mode="Markdown")

    elif data == "settings_change_pass":
        user_states[uid] = {"action": "change_password_old"}
        bot.edit_message_text("🔑 أرسل *كلمة المرور الحالية* أولاً:", uid, mid, parse_mode="Markdown")

    elif data == "settings_remove_pass":
        set_user_password(uid, "")
        bot.answer_callback_query(call.id, "✅ تم حذف كلمة المرور")
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("🔙 رجوع", callback_data="menu_settings"))
        bot.edit_message_text("✅ تم حذف كلمة المرور بنجاح.\n\nالآن يمكن الدخول بدون كلمة مرور.",
                              uid, mid, reply_markup=kb)

    # ============================
    # === العمليات ===
    # ============================
    elif data == "op_create":
        user_states[uid] = {"action": "op_create_title"}
        bot.edit_message_text("📝 أرسل *عنوان العملية*:", uid, mid, parse_mode="Markdown")

    elif data == "op_list":
        ops = get_operations(uid)
        if not ops:
            kb = types.InlineKeyboardMarkup()
            kb.add(types.InlineKeyboardButton("🔙 رجوع", callback_data="menu_operations"))
            bot.edit_message_text("📭 لا توجد عمليات مسجلة.", uid, mid, reply_markup=kb)
            return

        kb = types.InlineKeyboardMarkup(row_width=1)
        for op in ops:
            title = op.get("title", "بدون عنوان")
            kb.add(types.InlineKeyboardButton(f"📌 {title}", callback_data=f"op_view_{op['id']}"))
        kb.add(types.InlineKeyboardButton("🔙 رجوع", callback_data="menu_operations"))
        bot.edit_message_text("📋 *عملياتك:*\n\nاختر عملية لعرض تفاصيلها:", uid, mid,
                              reply_markup=kb, parse_mode="Markdown")

    elif data.startswith("op_view_"):
        op_id = data.replace("op_view_", "")
        op = get_operation_by_id(op_id)
        if not op:
            bot.answer_callback_query(call.id, "❌ العملية غير موجودة")
            return
        
        text = f"📌 *{op.get('title', '')}*\n\n"
        if op.get("details"):
            text += f"📝 التفاصيل:\n{op['details']}\n"
        
        kb = types.InlineKeyboardMarkup(row_width=2)
        kb.add(
            types.InlineKeyboardButton("🗑 حذف", callback_data=f"op_delete_{op_id}"),
            types.InlineKeyboardButton("🔙 رجوع", callback_data="op_list")
        )
        bot.edit_message_text(text, uid, mid, reply_markup=kb, parse_mode="Markdown")

    elif data.startswith("op_delete_"):
        op_id = data.replace("op_delete_", "")
        delete_operation(op_id)
        bot.answer_callback_query(call.id, "✅ تم حذف العملية")
        # عرض القائمة بعد الحذف
        ops = get_operations(uid)
        if not ops:
            kb = types.InlineKeyboardMarkup()
            kb.add(types.InlineKeyboardButton("🔙 رجوع", callback_data="menu_operations"))
            bot.edit_message_text("📭 لا توجد عمليات مسجلة.", uid, mid, reply_markup=kb)
        else:
            kb = types.InlineKeyboardMarkup(row_width=1)
            for op in ops:
                title = op.get("title", "بدون عنوان")
                kb.add(types.InlineKeyboardButton(f"📌 {title}", callback_data=f"op_view_{op['id']}"))
            kb.add(types.InlineKeyboardButton("🔙 رجوع", callback_data="menu_operations"))
            bot.edit_message_text("📋 *عملياتك:*\n\nاختر عملية لعرض تفاصيلها:", uid, mid,
                                  reply_markup=kb, parse_mode="Markdown")

    # ============================
    # === الاشتراكات (الإيميلات) ===
    # ============================
    elif data == "email_create":
        user_states[uid] = {"action": "email_type"}
        bot.edit_message_text("📌 أرسل *نوع الاشتراك* (مثال: نتفلكس، شاهد، سبوتيفاي...):", uid, mid,
                              parse_mode="Markdown")

    elif data == "email_list":
        emails = get_emails(uid)
        if not emails:
            kb = types.InlineKeyboardMarkup()
            kb.add(types.InlineKeyboardButton("🔙 رجوع", callback_data="menu_subscriptions"))
            bot.edit_message_text("📭 لا توجد إيميلات مسجلة.", uid, mid, reply_markup=kb)
            return

        kb = types.InlineKeyboardMarkup(row_width=1)
        for em in emails:
            sub_type = em.get("subscription_type", "")
            email_text = em.get("email", "بدون إيميل")
            clients_count = count_clients(em["id"])
            # عرض نوع الاشتراك مع الإيميل
            if sub_type:
                btn_text = f"📌 {sub_type} | {email_text} ({clients_count})"
            else:
                btn_text = f"📧 {email_text} ({clients_count} عملاء)"
            kb.add(types.InlineKeyboardButton(btn_text, callback_data=f"email_view_{em['id']}"))
        kb.add(types.InlineKeyboardButton("🔙 رجوع", callback_data="menu_subscriptions"))
        bot.edit_message_text("📧 *الإيميلات المسجلة:*\n\nاختر إيميل لإدارة عملائه:",
                              uid, mid, reply_markup=kb, parse_mode="Markdown")

    elif data.startswith("email_view_"):
        email_id = data.replace("email_view_", "")
        email_data = get_email_by_id(email_id)
        if not email_data:
            bot.answer_callback_query(call.id, "❌ الإيميل غير موجود")
            return
        
        clients = get_clients(email_id)
        sub_type = email_data.get("subscription_type", "")
        # عرض نوع الاشتراك إن وجد
        if sub_type:
            text = f"📌 *{escape_md(sub_type)}*\n"
            text += f"📧 {escape_md(email_data.get('email', ''))}\n"
        else:
            text = f"📧 *{escape_md(email_data.get('email', ''))}*\n"
        text += f"👥 عدد العملاء: {len(clients)}\n\n"

        if clients:
            for i, c in enumerate(clients, 1):
                text += f"*{i}. {escape_md(c.get('name', 'بدون اسم'))}*\n"
                text += f"   📱 {escape_md(c.get('phone', '-'))}\n"
                text += f"   📅 من: {c.get('start_date', '-')}\n"
                text += f"   📅 إلى: {c.get('end_date', '-')}\n\n"

        kb = types.InlineKeyboardMarkup(row_width=1)
        kb.add(
            types.InlineKeyboardButton("➕ إضافة عميل", callback_data=f"client_add_{email_id}"),
        )
        # أزرار التعديل
        kb.add(
            types.InlineKeyboardButton("✏️ تعديل نوع الاشتراك", callback_data=f"email_edit_type_{email_id}"),
            types.InlineKeyboardButton("✏️ تعديل الإيميل", callback_data=f"email_edit_addr_{email_id}"),
        )
        # أزرار حذف العملاء
        if clients:
            for c in clients:
                kb.add(types.InlineKeyboardButton(
                    f"🗑 حذف {c.get('name', 'عميل')}",
                    callback_data=f"client_del_{email_id}_{c['id']}"
                ))
        kb.add(
            types.InlineKeyboardButton("🗑 حذف الإيميل بالكامل", callback_data=f"email_delete_{email_id}"),
            types.InlineKeyboardButton("🔙 رجوع", callback_data="email_list")
        )
        bot.edit_message_text(text, uid, mid, reply_markup=kb, parse_mode="Markdown")

    elif data.startswith("email_delete_"):
        email_id = data.replace("email_delete_", "")
        delete_email(email_id)
        bot.answer_callback_query(call.id, "✅ تم حذف الإيميل وجميع عملائه")
        # إعادة عرض القائمة
        emails = get_emails(uid)
        if not emails:
            kb = types.InlineKeyboardMarkup()
            kb.add(types.InlineKeyboardButton("🔙 رجوع", callback_data="menu_subscriptions"))
            bot.edit_message_text("📭 لا توجد إيميلات مسجلة.", uid, mid, reply_markup=kb)
        else:
            kb = types.InlineKeyboardMarkup(row_width=1)
            for em in emails:
                email_text = em.get("email", "بدون إيميل")
                clients_count = count_clients(em["id"])
                kb.add(types.InlineKeyboardButton(
                    f"📧 {email_text} ({clients_count} عملاء)",
                    callback_data=f"email_view_{em['id']}"
                ))
            kb.add(types.InlineKeyboardButton("🔙 رجوع", callback_data="menu_subscriptions"))
            bot.edit_message_text("📧 *الإيميلات المسجلة:*", uid, mid,
                                  reply_markup=kb, parse_mode="Markdown")

    # === العملاء ===
    elif data.startswith("client_add_"):
        email_id = data.replace("client_add_", "")
        # التحقق من عدد العملاء
        email_data = get_email_by_id(email_id)
        if not email_data:
            bot.answer_callback_query(call.id, "❌ الإيميل غير موجود")
            return
        
        current_count = count_clients(email_id)
        max_clients = email_data.get("max_clients", 5)
        if current_count >= max_clients:
            bot.answer_callback_query(call.id, f"❌ الحد الأقصى {max_clients} عملاء لكل إيميل")
            return

        user_states[uid] = {"action": "client_name", "email_id": email_id}
        bot.edit_message_text("👤 أرسل *اسم العميل*:", uid, mid, parse_mode="Markdown")

    elif data.startswith("client_del_"):
        parts = data.replace("client_del_", "").split("_", 1)
        if len(parts) == 2:
            email_id, client_id = parts
            delete_client(email_id, client_id)
            bot.answer_callback_query(call.id, "✅ تم حذف العميل")
            # إعادة عرض الإيميل
            email_data = get_email_by_id(email_id)
            if email_data:
                clients = get_clients(email_id)
                sub_type = email_data.get("subscription_type", "")
                if sub_type:
                    text = f"📌 *{escape_md(sub_type)}*\n"
                    text += f"📧 {escape_md(email_data.get('email', ''))}\n"
                else:
                    text = f"📧 *{escape_md(email_data.get('email', ''))}*\n"
                text += f"👥 عدد العملاء: {len(clients)}\n\n"
                if clients:
                    for i, c in enumerate(clients, 1):
                        text += f"*{i}. {escape_md(c.get('name', 'بدون اسم'))}*\n"
                        text += f"   📱 {escape_md(c.get('phone', '-'))}\n"
                        text += f"   📅 من: {c.get('start_date', '-')}\n"
                        text += f"   📅 إلى: {c.get('end_date', '-')}\n\n"
                
                kb = types.InlineKeyboardMarkup(row_width=1)
                kb.add(types.InlineKeyboardButton("➕ إضافة عميل", callback_data=f"client_add_{email_id}"))
                if clients:
                    for c in clients:
                        kb.add(types.InlineKeyboardButton(
                            f"🗑 حذف {c.get('name', 'عميل')}",
                            callback_data=f"client_del_{email_id}_{c['id']}"
                        ))
                kb.add(
                    types.InlineKeyboardButton("🗑 حذف الإيميل بالكامل", callback_data=f"email_delete_{email_id}"),
                    types.InlineKeyboardButton("🔙 رجوع", callback_data="email_list")
                )
                bot.edit_message_text(text, uid, mid, reply_markup=kb, parse_mode="Markdown")

    # === تعديل نوع الاشتراك ===
    elif data.startswith("email_edit_type_"):
        email_id = data.replace("email_edit_type_", "")
        user_states[uid] = {"action": "edit_email_type", "email_id": email_id}
        bot.edit_message_text("✏️ أرسل *نوع الاشتراك الجديد*:", uid, mid, parse_mode="Markdown")

    # === تعديل الإيميل ===
    elif data.startswith("email_edit_addr_"):
        email_id = data.replace("email_edit_addr_", "")
        user_states[uid] = {"action": "edit_email_addr", "email_id": email_id}
        bot.edit_message_text("✏️ أرسل *الإيميل الجديد*:", uid, mid, parse_mode="Markdown")

    # === البحث عن عميل ===
    elif data == "client_search":
        user_states[uid] = {"action": "client_search"}
        bot.edit_message_text("🔍 أرسل *اسم العميل* للبحث عنه:", uid, mid, parse_mode="Markdown")

    bot.answer_callback_query(call.id)

# ============================
# === معالجة الرسائل النصية ===
# ============================

@bot.message_handler(func=lambda message: message.chat.id in user_states)
def handle_text_input(message):
    uid = message.chat.id
    state = user_states.get(uid, {})
    action = state.get("action", "")
    text = message.text.strip()

    # === تسجيل الدخول بكلمة المرور ===
    if action == "login_password":
        if verify_user_password(uid, text):
            login_user(uid)
            user_states.pop(uid, None)
            bot.send_message(uid, "✅ تم تسجيل الدخول بنجاح!\n\nاختر القسم المطلوب:",
                             reply_markup=main_menu())
        else:
            bot.send_message(uid, "❌ كلمة المرور غير صحيحة. حاول مرة أخرى.\n\n🔑 أرسل كلمة المرور:")

    # === تعيين كلمة مرور جديدة ===
    elif action == "set_password":
        set_user_password(uid, text)
        user_states.pop(uid, None)
        
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("🔙 رجوع", callback_data="menu_settings"))
        bot.send_message(uid, "✅ تم تعيين كلمة المرور بنجاح!\n\n🔐 سيتم طلبها عند كل دخول جديد.",
                         reply_markup=kb)

    # === تغيير كلمة المرور - إدخال القديمة ===
    elif action == "change_password_old":
        if verify_user_password(uid, text):
            user_states[uid] = {"action": "change_password_new"}
            bot.send_message(uid, "✅ صحيح!\n\n🔑 أرسل *كلمة المرور الجديدة*:", parse_mode="Markdown")
        else:
            bot.send_message(uid, "❌ كلمة المرور غير صحيحة. حاول مرة أخرى.\n\n🔑 أرسل كلمة المرور الحالية:")

    # === تغيير كلمة المرور - إدخال الجديدة ===
    elif action == "change_password_new":
        set_user_password(uid, text)
        user_states.pop(uid, None)
        
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("🔙 رجوع", callback_data="menu_settings"))
        bot.send_message(uid, "✅ تم تغيير كلمة المرور بنجاح!", reply_markup=kb)

    # === إنشاء عملية - العنوان ===
    elif action == "op_create_title":
        user_states[uid] = {"action": "op_create_details", "title": text}
        bot.send_message(uid, "📝 أرسل *تفاصيل العملية* (أو أرسل - للتخطي):",
                         parse_mode="Markdown")

    # === إنشاء عملية - التفاصيل ===
    elif action == "op_create_details":
        title = state.get("title", "")
        details = "" if text == "-" else text
        op_id = add_operation(uid, title, details)
        user_states.pop(uid, None)

        kb = types.InlineKeyboardMarkup(row_width=1)
        kb.add(
            types.InlineKeyboardButton("📋 عرض العمليات", callback_data="op_list"),
            types.InlineKeyboardButton("➕ عملية جديدة", callback_data="op_create"),
            types.InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="back_main")
        )
        bot.send_message(uid, f"✅ تم إنشاء العملية بنجاح!\n\n📌 *{title}*",
                         reply_markup=kb, parse_mode="Markdown")

    # === إنشاء إيميل - نوع الاشتراك ===
    elif action == "email_type":
        user_states[uid] = {"action": "email_create", "subscription_type": text}
        bot.send_message(uid, "📧 أرسل *الإيميل الأساسي*:", parse_mode="Markdown")

    # === إنشاء إيميل - الإيميل ===
    elif action == "email_create":
        subscription_type = state.get("subscription_type", "")
        email_id = add_email(uid, text, subscription_type)
        user_states.pop(uid, None)

        kb = types.InlineKeyboardMarkup(row_width=1)
        kb.add(
            types.InlineKeyboardButton("📋 عرض الإيميلات", callback_data="email_list"),
            types.InlineKeyboardButton("➕ إيميل جديد", callback_data="email_create"),
            types.InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="back_main")
        )
        bot.send_message(uid, f"✅ تم إضافة الإيميل بنجاح!\n\n📌 *{escape_md(subscription_type)}*\n📧 {escape_md(text)}",
                         reply_markup=kb, parse_mode="Markdown")

    # === إضافة عميل - الاسم ===
    elif action == "client_name":
        user_states[uid]["action"] = "client_phone"
        user_states[uid]["client_name"] = text
        bot.send_message(uid, "📱 أرسل *رقم الجوال* أو *يوزرنيم التيليجرام*:", parse_mode="Markdown")

    # === إضافة عميل - الرقم ===
    elif action == "client_phone":
        user_states[uid]["action"] = "client_start_date"
        user_states[uid]["client_phone"] = text
        bot.send_message(uid, "📅 أرسل *تاريخ بداية الاشتراك* (مثال: 2026-02-19):",
                         parse_mode="Markdown")

    # === إضافة عميل - تاريخ البداية ===
    elif action == "client_start_date":
        user_states[uid]["action"] = "client_end_date"
        user_states[uid]["start_date"] = text
        bot.send_message(uid, "📅 أرسل *تاريخ انتهاء الاشتراك* (مثال: 2026-03-19):",
                         parse_mode="Markdown")

    # === إضافة عميل - تاريخ الانتهاء ===
    elif action == "client_end_date":
        email_id = state.get("email_id")
        name = state.get("client_name", "")
        phone = state.get("client_phone", "")
        start_date = state.get("start_date", "")
        end_date = text

        try:
            add_client(email_id, name, phone, start_date, end_date)
            user_states.pop(uid, None)

            kb = types.InlineKeyboardMarkup(row_width=1)
            kb.add(
                types.InlineKeyboardButton("👁 عرض الإيميل", callback_data=f"email_view_{email_id}"),
                types.InlineKeyboardButton("➕ إضافة عميل آخر", callback_data=f"client_add_{email_id}"),
                types.InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="back_main")
            )
            bot.send_message(
                uid,
                f"✅ تم إضافة العميل بنجاح!\n\n"
                f"👤 *{escape_md(name)}*\n"
                f"📱 {escape_md(phone)}\n"
                f"📅 من: {start_date}\n"
                f"📅 إلى: {end_date}",
                reply_markup=kb,
                parse_mode="Markdown"
            )
        except Exception as e:
            print(f"❌ خطأ في إضافة العميل: {e}")
            user_states.pop(uid, None)
            bot.send_message(uid, "❌ حدث خطأ أثناء إضافة العميل. حاول مرة أخرى.", reply_markup=main_menu())

    # === تعديل نوع الاشتراك ===
    elif action == "edit_email_type":
        email_id = state.get("email_id")
        try:
            update_email(email_id, {"subscription_type": text})
            user_states.pop(uid, None)
            
            kb = types.InlineKeyboardMarkup(row_width=1)
            kb.add(
                types.InlineKeyboardButton("👁 عرض الإيميل", callback_data=f"email_view_{email_id}"),
                types.InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="back_main")
            )
            bot.send_message(uid, f"✅ تم تعديل نوع الاشتراك إلى:\n📌 *{escape_md(text)}*",
                             reply_markup=kb, parse_mode="Markdown")
        except Exception as e:
            print(f"❌ خطأ في تعديل نوع الاشتراك: {e}")
            user_states.pop(uid, None)
            bot.send_message(uid, "❌ حدث خطأ. حاول مرة أخرى.", reply_markup=main_menu())

    # === تعديل الإيميل ===
    elif action == "edit_email_addr":
        email_id = state.get("email_id")
        try:
            update_email(email_id, {"email": text})
            user_states.pop(uid, None)
            
            kb = types.InlineKeyboardMarkup(row_width=1)
            kb.add(
                types.InlineKeyboardButton("👁 عرض الإيميل", callback_data=f"email_view_{email_id}"),
                types.InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="back_main")
            )
            bot.send_message(uid, f"✅ تم تعديل الإيميل إلى:\n📧 *{escape_md(text)}*",
                             reply_markup=kb, parse_mode="Markdown")
        except Exception as e:
            print(f"❌ خطأ في تعديل الإيميل: {e}")
            user_states.pop(uid, None)
            bot.send_message(uid, "❌ حدث خطأ. حاول مرة أخرى.", reply_markup=main_menu())

    # === البحث عن عميل ===
    elif action == "client_search":
        user_states.pop(uid, None)
        results = search_clients_by_name(uid, text)
        
        if not results:
            kb = types.InlineKeyboardMarkup()
            kb.add(types.InlineKeyboardButton("🔙 رجوع", callback_data="menu_subscriptions"))
            bot.send_message(uid, f"❌ لم يتم العثور على عملاء بالاسم *{escape_md(text)}*",
                             reply_markup=kb, parse_mode="Markdown")
            return
        
        response = f"🔍 *نتائج البحث عن:* {escape_md(text)}\n\n"
        kb = types.InlineKeyboardMarkup(row_width=1)
        
        for i, r in enumerate(results, 1):
            sub_type = r.get("subscription_type", "")
            email = r.get("email", "")
            name = r.get("name", "")
            phone = r.get("phone", "-")
            start_date = r.get("start_date", "-")
            end_date = r.get("end_date", "-")
            
            response += f"*{i}. {escape_md(name)}*\n"
            if sub_type:
                response += f"   📌 {escape_md(sub_type)}\n"
            response += f"   📧 {escape_md(email)}\n"
            response += f"   📱 {escape_md(phone)}\n"
            response += f"   📅 من: {start_date} إلى: {end_date}\n\n"
            
            # زر للانتقال للإيميل
            kb.add(types.InlineKeyboardButton(
                f"👁 عرض {escape_md(sub_type) if sub_type else escape_md(email)}",
                callback_data=f"email_view_{r['email_id']}"
            ))
        
        kb.add(types.InlineKeyboardButton("🔍 بحث جديد", callback_data="client_search"))
        kb.add(types.InlineKeyboardButton("🔙 رجوع", callback_data="menu_subscriptions"))
        
        bot.send_message(uid, response, reply_markup=kb, parse_mode="Markdown")

    else:
        user_states.pop(uid, None)
        bot.send_message(uid, "اختر أمراً من القائمة 👇", reply_markup=main_menu())

# === أي رسالة أخرى ===
@bot.message_handler(func=lambda message: True)
def handle_other(message):
    bot.send_message(message.chat.id, "اختر أمراً من القائمة 👇", reply_markup=main_menu())

# ============================
# === نظام التنبيهات ===
# ============================

def check_expiring_subscriptions():
    """التحقق من الاشتراكات المنتهية وإرسال تنبيهات"""
    while True:
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            
            # جلب جميع العملاء للأدمن
            if ADMIN_ID:
                all_clients = get_all_clients_with_emails(int(ADMIN_ID))
                
                expiring_today = []
                for client in all_clients:
                    end_date = client.get("end_date", "")
                    if end_date == today:
                        expiring_today.append(client)
                
                # إرسال تنبيه إذا وجدت اشتراكات منتهية اليوم
                if expiring_today:
                    text = "⚠️ *تنبيه: اشتراكات تنتهي اليوم!*\n\n"
                    for c in expiring_today:
                        sub_type = c.get("subscription_type", "")
                        email = c.get("email", "")
                        name = c.get("name", "")
                        phone = c.get("phone", "-")
                        
                        text += f"👤 *{escape_md(name)}*\n"
                        if sub_type:
                            text += f"📌 {escape_md(sub_type)}\n"
                        text += f"📧 {escape_md(email)}\n"
                        text += f"📱 {escape_md(phone)}\n"
                        text += f"📅 ينتهي: {c.get('end_date', '-')}\n\n"
                    
                    try:
                        bot.send_message(int(ADMIN_ID), text, parse_mode="Markdown")
                        print(f"✅ تم إرسال تنبيه بـ {len(expiring_today)} اشتراكات منتهية")
                    except Exception as e:
                        print(f"❌ خطأ في إرسال التنبيه: {e}")
        
        except Exception as e:
            print(f"❌ خطأ في فحص الاشتراكات: {e}")
        
        # الانتظار 6 ساعات قبل الفحص التالي (يمكن تعديله)
        time.sleep(6 * 60 * 60)

# ============================
# === تشغيل البوت ===
# ============================

if __name__ == "__main__":
    print("🤖 البوت يعمل الآن...")
    
    # تشغيل نظام التنبيهات في thread منفصل
    notification_thread = threading.Thread(target=check_expiring_subscriptions, daemon=True)
    notification_thread.start()
    print("🔔 نظام التنبيهات يعمل...")
    
    bot.infinity_polling()
