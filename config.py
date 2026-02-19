#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
إعدادات البوت - Render
"""

import os

# === إعدادات البوت ===
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))

# === إعدادات Firebase (من متغيرات البيئة في Render) ===
FIREBASE_CREDENTIALS = os.environ.get("FIREBASE_CREDENTIALS")
