# -*- coding: utf-8 -*-
"""
حزمة المسارات - Routes Package
تحتوي على جميع مسارات التطبيق مقسمة حسب الوظيفة
"""

from routes.cart import cart_bp, init_cart
from routes.wallet import wallet_bp, init_wallet
from routes.admin import admin_bp, init_admin

__all__ = [
    'cart_bp', 'init_cart',
    'wallet_bp', 'init_wallet', 
    'admin_bp', 'init_admin'
]
