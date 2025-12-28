# ============================================
# 🛒 نظام سلة التسوق
# ============================================

from flask import Blueprint, request, jsonify, session, redirect, render_template
from datetime import datetime, timedelta
import random

from extensions import db, FIREBASE_AVAILABLE
from firebase_utils import get_user_cart, save_user_cart, clear_user_cart, get_balance
from google.cloud import firestore

# إنشاء Blueprint
cart_bp = Blueprint('cart', __name__)

# سيتم تعيينها من app.py
bot = None
ADMIN_ID = None
limiter = None


def init_cart(app_bot, admin_id, app_limiter):
    """تهيئة متغيرات السلة"""
    global bot, ADMIN_ID, limiter
    bot = app_bot
    ADMIN_ID = admin_id
    limiter = app_limiter


@cart_bp.route('/cart')
def cart_page():
    """صفحة سلة التسوق"""
    user_id = session.get('user_id')
    if not user_id:
        return redirect('/')
    
    balance = get_balance(user_id)
    return render_template('cart.html', user_id=user_id, balance=balance)


@cart_bp.route('/api/cart/add', methods=['POST'])
def api_cart_add():
    """إضافة منتج للسلة"""
    try:
        data = request.json
        user_id = str(data.get('user_id'))
        product_id = data.get('product_id')
        buyer_details = data.get('buyer_details', '')
        
        if not user_id or not product_id:
            return jsonify({'status': 'error', 'message': 'بيانات ناقصة'})
        
        # التحقق من المنتج
        product_doc = db.collection('products').document(product_id).get()
        if not product_doc.exists:
            return jsonify({'status': 'error', 'message': 'المنتج غير موجود'})
        
        product = product_doc.to_dict()
        
        # منع إضافة منتج مباع
        if product.get('sold', False):
            return jsonify({'status': 'error', 'message': 'عذراً، هذا المنتج تم بيعه! 🚫'})
        
        cart = get_user_cart(user_id) or {}
        now = datetime.utcnow()
        
        # التحقق من انتهاء السلة
        if cart.get('expires_at'):
            expires = cart['expires_at']
            if isinstance(expires, str):
                expires = datetime.fromisoformat(expires.replace('Z', ''))
            if expires < now:
                cart = {}
        
        # إنشاء سلة جديدة أو تحديث
        if not cart.get('items'):
            cart = {
                'items': [],
                'created_at': now.isoformat(),
                'expires_at': (now + timedelta(hours=3)).isoformat(),
                'status': 'active'
            }
        
        # التحقق من عدم وجود المنتج في السلة
        existing_ids = [item['product_id'] for item in cart.get('items', [])]
        if product_id in existing_ids:
            return jsonify({'status': 'error', 'message': 'المنتج موجود في السلة بالفعل!'})
        
        # إضافة المنتج
        cart_item = {
            'product_id': product_id,
            'name': product.get('item_name', 'منتج'),
            'price': float(product.get('price', 0)),
            'category': product.get('category', ''),
            'image_url': product.get('image_url', ''),
            'delivery_type': product.get('delivery_type', 'instant'),
            'buyer_instructions': product.get('buyer_instructions', ''),
            'buyer_details': buyer_details,
            'added_at': now.isoformat()
        }
        cart['items'].append(cart_item)
        cart['updated_at'] = now.isoformat()
        
        # حفظ في Firebase
        save_user_cart(user_id, cart)
        
        # تحديث إحصائيات المنتج
        try:
            stats_ref = db.collection('cart_stats').document(product_id)
            stats_doc = stats_ref.get()
            if stats_doc.exists:
                stats_ref.update({'add_to_cart_count': firestore.Increment(1)})
            else:
                stats_ref.set({'product_id': product_id, 'add_to_cart_count': 1, 'purchase_count': 0})
        except:
            pass
        
        return jsonify({
            'status': 'success',
            'message': 'تمت الإضافة للسلة! 🛒',
            'cart_count': len(cart['items'])
        })
        
    except Exception as e:
        print(f"❌ خطأ في إضافة للسلة: {e}")
        return jsonify({'status': 'error', 'message': 'حدث خطأ'})


@cart_bp.route('/api/cart/get')
def api_cart_get():
    """جلب محتويات السلة"""
    try:
        user_id = request.args.get('user_id')
        if not user_id:
            return jsonify({'status': 'error', 'message': 'معرف المستخدم مطلوب'})
        
        cart = get_user_cart(str(user_id)) or {}
        
        if not cart or not cart.get('items'):
            return jsonify({'status': 'empty', 'message': 'السلة فارغة'})
        
        # التحقق من انتهاء الصلاحية
        now = datetime.utcnow()
        expires_at = cart.get('expires_at')
        if expires_at:
            if isinstance(expires_at, str):
                expires = datetime.fromisoformat(expires_at.replace('Z', ''))
            else:
                expires = expires_at
            if expires < now:
                clear_user_cart(str(user_id))
                return jsonify({'status': 'expired', 'message': 'انتهت صلاحية السلة'})
        
        # تحديث حالة المنتجات
        updated_items = []
        for item in cart['items']:
            product_doc = db.collection('products').document(item['product_id']).get()
            if product_doc.exists:
                product = product_doc.to_dict()
                item['sold'] = product.get('sold', False)
                item['current_price'] = float(product.get('price', item['price']))
                item['price_changed'] = item['current_price'] != item['price']
                updated_items.append(item)
            else:
                item['sold'] = True
                updated_items.append(item)
        
        cart['items'] = updated_items
        
        return jsonify({
            'status': 'success',
            'cart': cart
        })
        
    except Exception as e:
        print(f"❌ خطأ في جلب السلة: {e}")
        return jsonify({'status': 'error', 'message': 'حدث خطأ'})


@cart_bp.route('/api/cart/remove', methods=['POST'])
def api_cart_remove():
    """حذف منتج من السلة"""
    try:
        data = request.json
        user_id = str(data.get('user_id'))
        product_id = data.get('product_id')
        
        if not user_id or not product_id:
            return jsonify({'status': 'error', 'message': 'بيانات ناقصة'})
        
        cart = get_user_cart(user_id) or {}
        if not cart or not cart.get('items'):
            return jsonify({'status': 'error', 'message': 'السلة فارغة'})
        
        # حذف المنتج
        cart['items'] = [i for i in cart['items'] if i['product_id'] != product_id]
        cart['updated_at'] = datetime.utcnow().isoformat()
        
        # حفظ في Firebase
        save_user_cart(user_id, cart)
        
        return jsonify({
            'status': 'success',
            'message': 'تم حذف المنتج',
            'cart_count': len(cart['items'])
        })
        
    except Exception as e:
        print(f"❌ خطأ في حذف من السلة: {e}")
        return jsonify({'status': 'error', 'message': 'حدث خطأ'})


@cart_bp.route('/api/cart/checkout', methods=['POST'])
def api_cart_checkout():
    """إتمام شراء السلة"""
    global bot, ADMIN_ID
    
    try:
        data = request.json
        user_id = str(data.get('user_id'))
        
        if not user_id:
            return jsonify({'status': 'error', 'message': 'معرف المستخدم مطلوب'})
        
        # جلب السلة من Firebase
        cart = get_user_cart(user_id) or {}
        if not cart or not cart.get('items'):
            return jsonify({'status': 'error', 'message': 'السلة فارغة'})
        
        # تصفية المنتجات المتاحة
        available_items = []
        total = 0
        
        for item in cart['items']:
            product_doc = db.collection('products').document(item['product_id']).get()
            if product_doc.exists:
                product = product_doc.to_dict()
                if not product.get('sold', False):
                    item['product_data'] = product
                    item['current_price'] = float(product.get('price', item['price']))
                    total += item['current_price']
                    available_items.append(item)
        
        if not available_items:
            return jsonify({'status': 'error', 'message': 'لا توجد منتجات متاحة في السلة'})
        
        # التحقق من الرصيد
        user_doc = db.collection('users').document(user_id).get()
        if not user_doc.exists:
            return jsonify({'status': 'error', 'message': 'حدث خطأ في المستخدم'})
        
        user_data = user_doc.to_dict()
        balance = float(user_data.get('balance', 0))
        
        if balance < total:
            return jsonify({'status': 'error', 'message': f'رصيدك غير كافي! تحتاج {total - balance:.2f} ر.س إضافية'})
        
        # تنفيذ الشراء باستخدام batch
        batch = db.batch()
        new_balance = balance - total
        purchased_items = []
        order_ids = []
        
        # جلب اسم المشتري
        buyer_name = user_data.get('name') or user_data.get('username') or user_data.get('first_name') or 'مستخدم'
        
        for item in available_items:
            product = item['product_data']
            product_id = item['product_id']
            delivery_type = item.get('delivery_type', product.get('delivery_type', 'instant'))
            order_status = 'completed' if delivery_type == 'instant' else 'pending'
            
            # تحديث المنتج كمباع
            product_ref = db.collection('products').document(product_id)
            batch.update(product_ref, {
                'sold': True,
                'buyer_id': user_id,
                'buyer_name': buyer_name,
                'sold_at': firestore.SERVER_TIMESTAMP
            })
            
            # إنشاء الطلب
            order_id = f"ORD_{random.randint(100000, 999999)}"
            order_ref = db.collection('orders').document(order_id)
            batch.set(order_ref, {
                'buyer_id': user_id,
                'buyer_name': buyer_name,
                'item_name': product.get('item_name'),
                'price': item['current_price'],
                'hidden_data': product.get('hidden_data'),
                'details': product.get('details', ''),
                'category': product.get('category', ''),
                'delivery_type': delivery_type,
                'buyer_details': item.get('buyer_details', ''),
                'buyer_instructions': item.get('buyer_instructions', ''),
                'status': order_status,
                'from_cart': True,
                'created_at': firestore.SERVER_TIMESTAMP
            })
            
            order_ids.append(order_id)
            purchased_items.append({
                'name': product.get('item_name'),
                'price': item['current_price'],
                'hidden_data': product.get('hidden_data'),
                'order_id': order_id,
                'delivery_type': delivery_type,
                'buyer_details': item.get('buyer_details', '')
            })
            
            # تحديث إحصائيات
            try:
                stats_ref = db.collection('cart_stats').document(product_id)
                batch.update(stats_ref, {'purchase_count': firestore.Increment(1)})
            except:
                pass
        
        # تحديث رصيد المستخدم
        user_ref = db.collection('users').document(user_id)
        batch.update(user_ref, {'balance': new_balance})
        
        # تنفيذ كل العمليات
        batch.commit()
        
        # حذف السلة من Firebase
        clear_user_cart(user_id)
        
        # فصل المنتجات الفورية عن اليدوية
        instant_items = [i for i in purchased_items if i.get('delivery_type') == 'instant']
        manual_items = [i for i in purchased_items if i.get('delivery_type') == 'manual']
        
        # إرسال البيانات للمشتري عبر البوت
        if bot:
            try:
                msg = "🎉 تم شراء سلتك بنجاح!\n\n"
                
                if instant_items:
                    msg += "⚡ منتجات تسليم فوري:\n"
                    for item in instant_items:
                        msg += f"📦 {item['name']}\n"
                        msg += f"💰 {item['price']} ر.س\n"
                        msg += f"🆔 #{item['order_id']}\n"
                        if item.get('hidden_data'):
                            msg += f"🔐 البيانات:\n{item['hidden_data']}\n"
                        msg += "─────────────\n"
                
                if manual_items:
                    msg += "\n👨‍💼 منتجات تسليم يدوي (بانتظار التنفيذ):\n"
                    for item in manual_items:
                        msg += f"📦 {item['name']}\n"
                        msg += f"💰 {item['price']} ر.س\n"
                        msg += f"🆔 #{item['order_id']}\n"
                        msg += "⏳ سيتم تنفيذه قريباً\n"
                        msg += "─────────────\n"
                
                msg += f"\n💳 رصيدك المتبقي: {new_balance:.2f} ر.س"
                
                bot.send_message(int(user_id), msg)
            except Exception as e:
                print(f"⚠️ فشل إرسال رسالة للمشتري: {e}")
            
            # إشعار الأدمن للطلبات اليدوية
            if manual_items and ADMIN_ID:
                try:
                    import telebot
                    for item in manual_items:
                        claim_markup = telebot.types.InlineKeyboardMarkup()
                        claim_markup.add(telebot.types.InlineKeyboardButton(
                            "📋 استلام الطلب", 
                            callback_data=f"claim_order_{item['order_id']}"
                        ))
                        
                        admin_msg = f"🆕 طلب يدوي جديد من السلة!\n\n"
                        admin_msg += f"🆔 رقم الطلب: #{item['order_id']}\n"
                        admin_msg += f"📦 المنتج: {item['name']}\n"
                        admin_msg += f"👤 المشتري: {buyer_name} ({user_id})\n"
                        admin_msg += f"💰 السعر: {item['price']} ر.س\n"
                        if item.get('buyer_details'):
                            admin_msg += f"\n📝 معلومات المشتري:\n{item['buyer_details']}\n"
                        admin_msg += f"\n👇 اضغط لاستلام الطلب"
                        
                        bot.send_message(ADMIN_ID, admin_msg, reply_markup=claim_markup)
                except Exception as e:
                    print(f"⚠️ فشل إشعار الأدمن: {e}")
            
            # إشعار عام للأدمن
            if ADMIN_ID:
                try:
                    admin_msg = f"🛒 شراء سلة جديد!\n\n"
                    admin_msg += f"👤 المشتري: {buyer_name} ({user_id})\n"
                    admin_msg += f"📦 عدد المنتجات: {len(purchased_items)}\n"
                    admin_msg += f"⚡ فوري: {len(instant_items)} | 👨‍💼 يدوي: {len(manual_items)}\n"
                    admin_msg += f"💰 الإجمالي: {total:.2f} ر.س"
                    bot.send_message(ADMIN_ID, admin_msg)
                except:
                    pass
        
        return jsonify({
            'status': 'success',
            'message': 'تم الشراء بنجاح!',
            'purchased_count': len(purchased_items),
            'total': total,
            'new_balance': new_balance,
            'order_ids': order_ids
        })
        
    except Exception as e:
        print(f"❌ خطأ في إتمام الشراء: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'status': 'error', 'message': 'حدث خطأ في إتمام الشراء'})


@cart_bp.route('/api/cart/count')
def api_cart_count():
    """جلب عدد منتجات السلة"""
    user_id = request.args.get('user_id')
    if not user_id:
        return jsonify({'count': 0})
    
    cart = get_user_cart(str(user_id)) or {}
    count = len(cart.get('items', []))
    return jsonify({'count': count})
