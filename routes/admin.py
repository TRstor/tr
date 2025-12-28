# -*- coding: utf-8 -*-
"""
نظام لوحة التحكم للمالك
يحتوي على جميع صفحات وAPI الأدمن
"""

from flask import Blueprint, render_template, request, jsonify, session, redirect
from google.cloud import firestore
import time
import random
import uuid
import os
import logging

logger = logging.getLogger(__name__)

# إنشاء Blueprint
admin_bp = Blueprint('admin', __name__)

# متغيرات عامة - سيتم تعيينها من init_admin
db = None
bot = None
ADMIN_ID = None
limiter = None
BOT_ACTIVE = False
display_settings = {'categories_columns': 3}

# متغيرات للتحكم في الدخول
admin_login_codes = {}
failed_login_attempts = {}

# ===================== دوال مساعدة =====================

def get_all_products_for_store():
    """جلب جميع المنتجات للمتجر"""
    try:
        products = []
        if db:
            products_ref = db.collection('products')
            for doc in products_ref.stream():
                data = doc.to_dict()
                data['id'] = doc.id
                products.append(data)
        return products
    except Exception as e:
        logger.error(f"Error getting products: {e}")
        return []

def get_categories():
    """جلب الأقسام من Firebase"""
    try:
        categories = []
        if db:
            cats_ref = db.collection('categories').order_by('order')
            for doc in cats_ref.stream():
                data = doc.to_dict()
                data['id'] = doc.id
                categories.append(data)
        return categories
    except Exception as e:
        logger.error(f"Error getting categories: {e}")
        return []

def get_categories_list():
    """جلب قائمة الأقسام"""
    return get_categories()

def get_category_by_id(cat_id):
    """جلب قسم بالـ ID"""
    try:
        if db:
            doc = db.collection('categories').document(cat_id).get()
            if doc.exists:
                data = doc.to_dict()
                data['id'] = doc.id
                return data
        return None
    except Exception as e:
        logger.error(f"Error getting category: {e}")
        return None

def update_category(cat_id, update_data):
    """تحديث قسم"""
    try:
        if db:
            db.collection('categories').document(cat_id).update(update_data)
            return True
    except Exception as e:
        logger.error(f"Error updating category: {e}")
    return False

def delete_category(cat_id):
    """حذف قسم"""
    try:
        if db:
            db.collection('categories').document(cat_id).delete()
            return True
    except Exception as e:
        logger.error(f"Error deleting category: {e}")
    return False

def count_products_in_category(category_name):
    """عد المنتجات في قسم"""
    try:
        if db:
            products = db.collection('products').where('category', '==', category_name).stream()
            return len(list(products))
        return 0
    except Exception as e:
        logger.error(f"Error counting products: {e}")
        return 0

def add_balance(user_id, amount):
    """إضافة رصيد للمستخدم"""
    try:
        if db:
            user_ref = db.collection('users').document(str(user_id))
            user_doc = user_ref.get()
            if user_doc.exists:
                current_balance = user_doc.to_dict().get('balance', 0)
                user_ref.update({'balance': current_balance + amount})
            else:
                user_ref.set({'balance': amount})
            return True
    except Exception as e:
        logger.error(f"Error adding balance: {e}")
    return False

def delete_product(product_id):
    """حذف منتج"""
    try:
        if db:
            db.collection('products').document(product_id).delete()
            return True
    except Exception as e:
        logger.error(f"Error deleting product: {e}")
    return False

def query_where(ref, field, op, value):
    """استعلام بشرط"""
    return ref.where(field, op, value)

# ===================== صفحة الدخول والتحقق =====================

@admin_bp.route('/api/admin/send_code', methods=['POST'])
def api_send_admin_code():
    """إرسال كود التحقق للمالك"""
    global admin_login_codes, failed_login_attempts
    
    try:
        data = request.json
        password = data.get('password', '')
        client_ip = request.headers.get('X-Forwarded-For', request.remote_addr)
        
        # التحقق من الحظر بسبب محاولات فاشلة
        if client_ip in failed_login_attempts:
            attempt_data = failed_login_attempts[client_ip]
            if attempt_data.get('blocked_until', 0) > time.time():
                remaining = int(attempt_data['blocked_until'] - time.time())
                return jsonify({
                    'status': 'error',
                    'message': f'⛔ تم حظرك مؤقتاً. حاول بعد {remaining} ثانية'
                })
        
        # التحقق من كلمة المرور
        admin_password = os.environ.get('ADMIN_PASS', 'admin123')
        
        if password != admin_password:
            # تسجيل المحاولة الفاشلة
            if client_ip not in failed_login_attempts:
                failed_login_attempts[client_ip] = {'count': 0, 'blocked_until': 0}
            
            failed_login_attempts[client_ip]['count'] += 1
            attempts_left = 5 - failed_login_attempts[client_ip]['count']
            
            # حظر بعد 5 محاولات
            if failed_login_attempts[client_ip]['count'] >= 5:
                failed_login_attempts[client_ip]['blocked_until'] = time.time() + 900
                
                # إرسال تنبيه أمني للمالك
                try:
                    alert_msg = f"""
⚠️ *تنبيه أمني!*

محاولات دخول فاشلة متعددة للوحة التحكم!

🌐 *IP:* `{client_ip}`
⏰ *الوقت:* {time.strftime('%Y-%m-%d %H:%M:%S')}
🔒 *الحالة:* تم الحظر لمدة 15 دقيقة
                    """
                    if BOT_ACTIVE and bot:
                        bot.send_message(ADMIN_ID, alert_msg, parse_mode='Markdown')
                except Exception as e:
                    print(f"Failed to send security alert: {e}")
                
                return jsonify({
                    'status': 'error',
                    'message': '⛔ تم حظرك لمدة 15 دقيقة بسبب محاولات فاشلة متكررة'
                })
            
            return jsonify({
                'status': 'error',
                'message': f'❌ كلمة مرور خاطئة! المحاولات المتبقية: {attempts_left}'
            })
        
        # كلمة المرور صحيحة - توليد كود عشوائي
        code = str(random.randint(100000, 999999))
        
        # حفظ الكود مع وقت الانتهاء (3 دقائق)
        admin_login_codes = {
            'code': code,
            'created_at': time.time(),
            'expires_at': time.time() + 180,
            'used': False,
            'ip': client_ip
        }
        
        # إرسال الكود للمالك عبر البوت
        try:
            if BOT_ACTIVE and bot:
                code_msg = f"""
🔐 *طلب دخول للوحة التحكم*

📍 *الكود:* `{code}`
⏰ *صالح لمدة:* 3 دقائق
🌐 *IP:* `{client_ip}`
⏱️ *الوقت:* {time.strftime('%Y-%m-%d %H:%M:%S')}

⚠️ *إذا لم تكن أنت، تجاهل هذا الكود!*
                """
                bot.send_message(ADMIN_ID, code_msg, parse_mode='Markdown')
                
                # مسح المحاولات الفاشلة عند النجاح
                if client_ip in failed_login_attempts:
                    del failed_login_attempts[client_ip]
                
                return jsonify({'status': 'success', 'message': 'تم إرسال الكود'})
            else:
                return jsonify({
                    'status': 'error',
                    'message': '❌ البوت غير متصل! لا يمكن إرسال الكود'
                })
        except Exception as e:
            print(f"Error sending code: {e}")
            return jsonify({
                'status': 'error',
                'message': '❌ فشل إرسال الكود للبوت'
            })
            
    except Exception as e:
        print(f"Error in send_code: {e}")
        return jsonify({'status': 'error', 'message': 'خطأ في السيرفر'})

@admin_bp.route('/api/admin/verify_code', methods=['POST'])
def api_verify_admin_code():
    """التحقق من كود الدخول"""
    global admin_login_codes
    
    try:
        data = request.json
        code = data.get('code', '').strip()
        client_ip = request.headers.get('X-Forwarded-For', request.remote_addr)
        
        # التحقق من وجود كود نشط
        if not admin_login_codes or not admin_login_codes.get('code'):
            return jsonify({
                'status': 'error',
                'message': '❌ لا يوجد كود نشط. اطلب كود جديد'
            })
        
        # التحقق من انتهاء الصلاحية
        if time.time() > admin_login_codes.get('expires_at', 0):
            admin_login_codes = {}
            return jsonify({
                'status': 'error',
                'message': '⏰ انتهت صلاحية الكود! اطلب كود جديد'
            })
        
        # التحقق من استخدام الكود مسبقاً
        if admin_login_codes.get('used'):
            return jsonify({
                'status': 'error',
                'message': '❌ تم استخدام هذا الكود مسبقاً'
            })
        
        # التحقق من صحة الكود
        if code != admin_login_codes.get('code'):
            return jsonify({
                'status': 'error',
                'message': '❌ كود خاطئ!'
            })
        
        # الكود صحيح - تسجيل الدخول
        admin_login_codes['used'] = True
        session['is_admin'] = True
        
        # إرسال إشعار بنجاح الدخول
        try:
            if BOT_ACTIVE and bot:
                success_msg = f"""
✅ *تم تسجيل الدخول بنجاح!*

🌐 *IP:* `{client_ip}`
⏰ *الوقت:* {time.strftime('%Y-%m-%d %H:%M:%S')}
                """
                bot.send_message(ADMIN_ID, success_msg, parse_mode='Markdown')
        except:
            pass
        
        # مسح الكود
        admin_login_codes = {}
        
        return jsonify({'status': 'success', 'message': 'تم التحقق بنجاح'})
        
    except Exception as e:
        print(f"Error in verify_code: {e}")
        return jsonify({'status': 'error', 'message': 'خطأ في السيرفر'})

@admin_bp.route('/logout_admin')
def logout_admin():
    """تسجيل خروج الأدمن"""
    session.pop('is_admin', None)
    return redirect('/dashboard')

# ===================== صفحات لوحة التحكم =====================

@admin_bp.route('/admin/products')
def admin_products():
    """صفحة إدارة المنتجات"""
    if not session.get('is_admin'):
        return redirect('/dashboard')
    return render_template('admin_products.html', admin_id=ADMIN_ID)

@admin_bp.route('/admin/categories')
def admin_categories():
    """صفحة إدارة الأقسام"""
    if not session.get('is_admin'):
        return redirect('/dashboard')
    return render_template('admin_categories.html')

@admin_bp.route('/admin/invoices')
def admin_invoices():
    """صفحة عرض الفواتير والمعاملات"""
    if not session.get('is_admin'):
        return redirect('/dashboard')
    return render_template('admin_invoices.html')

# ===================== API الفواتير =====================

@admin_bp.route('/api/admin/get_invoices')
def api_get_invoices():
    """جلب جميع الفواتير والمعاملات المالية"""
    if not session.get('is_admin'):
        return jsonify({'status': 'error', 'message': 'غير مصرح'})
    
    try:
        # 1️⃣ طلبات الدفع (pending_payments)
        pending_payments_list = []
        try:
            pending_ref = db.collection('pending_payments').order_by('created_at', direction=firestore.Query.DESCENDING).limit(100)
            for doc in pending_ref.stream():
                data = doc.to_dict()
                user_name = 'غير معروف'
                user_id = data.get('user_id', '')
                try:
                    user_doc = db.collection('users').document(str(user_id)).get()
                    if user_doc.exists:
                        user_data = user_doc.to_dict()
                        user_name = user_data.get('name', user_data.get('telegram_name', f'مستخدم {user_id}'))
                except:
                    pass
                
                pending_payments_list.append({
                    'id': doc.id,
                    'order_id': data.get('order_id', doc.id),
                    'user_id': user_id,
                    'user_name': user_name,
                    'amount': data.get('amount', 0),
                    'status': data.get('status', 'pending'),
                    'type': 'فاتورة تاجر' if data.get('is_merchant_invoice') else 'شحن رصيد',
                    'is_merchant_invoice': data.get('is_merchant_invoice', False),
                    'invoice_id': data.get('invoice_id', ''),
                    'trans_id': data.get('trans_id', ''),
                    'created_at': str(data.get('created_at', '')),
                    'completed_at': str(data.get('completed_at', ''))
                })
        except Exception as e:
            print(f"⚠️ خطأ في جلب pending_payments: {e}")
        
        # 2️⃣ فواتير التجار (merchant_invoices)
        merchant_invoices_list = []
        try:
            invoices_ref = db.collection('merchant_invoices').order_by('created_at', direction=firestore.Query.DESCENDING).limit(100)
            for doc in invoices_ref.stream():
                data = doc.to_dict()
                merchant_invoices_list.append({
                    'id': doc.id,
                    'merchant_id': data.get('merchant_id', ''),
                    'merchant_name': data.get('merchant_name', 'تاجر'),
                    'customer_phone': data.get('customer_phone', ''),
                    'amount': data.get('amount', 0),
                    'status': data.get('status', 'pending'),
                    'type': 'فاتورة تاجر',
                    'created_at': str(data.get('created_at', '')),
                    'completed_at': str(data.get('completed_at', ''))
                })
        except Exception as e:
            print(f"⚠️ خطأ في جلب merchant_invoices: {e}")
        
        # 3️⃣ سجل الشحن (charge_history)
        charge_history_list = []
        try:
            charge_ref = db.collection('charge_history').order_by('created_at', direction=firestore.Query.DESCENDING).limit(100)
            for doc in charge_ref.stream():
                data = doc.to_dict()
                user_name = 'غير معروف'
                user_id = data.get('user_id', '')
                try:
                    user_doc = db.collection('users').document(str(user_id)).get()
                    if user_doc.exists:
                        user_data = user_doc.to_dict()
                        user_name = user_data.get('name', user_data.get('telegram_name', f'مستخدم {user_id}'))
                except:
                    pass
                
                charge_history_list.append({
                    'id': doc.id,
                    'user_id': user_id,
                    'user_name': user_name,
                    'amount': data.get('amount', 0),
                    'method': data.get('method', 'key'),
                    'key_code': data.get('key_code', ''),
                    'type': 'شحن بمفتاح' if data.get('method') == 'key' else 'شحن إلكتروني',
                    'created_at': str(data.get('created_at', ''))
                })
        except Exception as e:
            print(f"⚠️ خطأ في جلب charge_history: {e}")
        
        # 4️⃣ الطلبات/المشتريات (orders)
        orders_list = []
        try:
            orders_ref = db.collection('orders').order_by('created_at', direction=firestore.Query.DESCENDING).limit(100)
            for doc in orders_ref.stream():
                data = doc.to_dict()
                orders_list.append({
                    'id': doc.id,
                    'order_id': doc.id[:8],
                    'item_name': data.get('item_name', 'منتج'),
                    'price': data.get('price', 0),
                    'buyer_id': data.get('buyer_id', ''),
                    'buyer_name': data.get('buyer_name', 'مشتري'),
                    'seller_id': data.get('seller_id', ''),
                    'seller_name': data.get('seller_name', 'بائع'),
                    'status': data.get('status', 'completed'),
                    'delivery_type': data.get('delivery_type', 'instant'),
                    'type': 'شراء من الموقع',
                    'created_at': str(data.get('created_at', ''))
                })
        except Exception as e:
            print(f"⚠️ خطأ في جلب orders: {e}")
        
        # 5️⃣ المنتجات المباعة والمتاحة
        sold_products_list = []
        available_products_list = []
        try:
            products_ref = db.collection('products')
            for doc in products_ref.stream():
                data = doc.to_dict()
                
                buyer_name = data.get('buyer_name', '')
                buyer_id = data.get('buyer_id', '')
                
                if data.get('sold') and buyer_id:
                    if not buyer_name or buyer_name == '':
                        try:
                            buyer_doc = db.collection('users').document(str(buyer_id)).get()
                            if buyer_doc.exists:
                                buyer_data = buyer_doc.to_dict()
                                buyer_name = buyer_data.get('name') or buyer_data.get('username') or buyer_data.get('telegram_name') or ''
                        except Exception as e:
                            print(f"⚠️ خطأ في جلب بيانات المشتري {buyer_id}: {e}")
                    
                    if not buyer_name:
                        buyer_name = f'مستخدم {buyer_id}'
                
                product_info = {
                    'id': doc.id,
                    'item_name': data.get('item_name', 'منتج'),
                    'price': data.get('price', 0),
                    'category': data.get('category', ''),
                    'seller_name': data.get('seller_name', 'المتجر'),
                    'delivery_type': data.get('delivery_type', 'instant'),
                    'sold': data.get('sold', False),
                    'buyer_id': buyer_id,
                    'buyer_name': buyer_name,
                    'sold_at': str(data.get('sold_at', '')),
                    'created_at': str(data.get('created_at', ''))
                }
                if data.get('sold'):
                    sold_products_list.append(product_info)
                else:
                    available_products_list.append(product_info)
        except Exception as e:
            print(f"⚠️ خطأ في جلب products: {e}")
        
        # 6️⃣ إحصائيات
        stats = {
            'total_payments': len(pending_payments_list),
            'completed_payments': len([p for p in pending_payments_list if p['status'] == 'completed']),
            'pending_payments': len([p for p in pending_payments_list if p['status'] == 'pending']),
            'total_merchant_invoices': len(merchant_invoices_list),
            'total_charges': len(charge_history_list),
            'total_orders': len(orders_list),
            'sold_products': len(sold_products_list),
            'available_products': len(available_products_list),
            'total_revenue': sum([o['price'] for o in orders_list]),
            'total_charged': sum([c['amount'] for c in charge_history_list])
        }
        
        return jsonify({
            'status': 'success',
            'pending_payments': pending_payments_list,
            'merchant_invoices': merchant_invoices_list,
            'charge_history': charge_history_list,
            'orders': orders_list,
            'sold_products': sold_products_list,
            'available_products': available_products_list,
            'stats': stats
        })
        
    except Exception as e:
        print(f"❌ خطأ في جلب الفواتير: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'status': 'error', 'message': str(e)})

# ===================== API المنتجات =====================

@admin_bp.route('/api/admin/get_products')
def api_get_products():
    """جلب جميع المنتجات"""
    if not session.get('is_admin'):
        return jsonify({'status': 'error', 'message': 'غير مصرح'})
    
    try:
        available = []
        sold = []
        
        if db:
            products_ref = db.collection('products')
            
            available_query = query_where(products_ref, 'sold', '==', False)
            for doc in available_query.stream():
                data = doc.to_dict()
                data['id'] = doc.id
                available.append(data)
            
            sold_query = query_where(products_ref, 'sold', '==', True)
            for doc in sold_query.stream():
                data = doc.to_dict()
                data['id'] = doc.id
                sold.append(data)
        
        return jsonify({
            'status': 'success',
            'available': available,
            'sold': sold
        })
        
    except Exception as e:
        logger.error(f"Error getting products: {e}")
        return jsonify({'status': 'error', 'message': 'حدث خطأ، حاول لاحقاً'})

@admin_bp.route('/api/admin/add_product_new', methods=['POST'])
def api_add_product_new():
    """إضافة منتج جديد"""
    if not session.get('is_admin'):
        return jsonify({'status': 'error', 'message': 'غير مصرح'})
    
    try:
        data = request.json
        name = data.get('name', '').strip()
        price = float(data.get('price', 0))
        category = data.get('category', '').strip()
        details = data.get('details', '').strip()
        hidden_data = data.get('hidden_data', '').strip()
        buyer_instructions = data.get('buyer_instructions', '').strip()
        image = data.get('image', '').strip()
        delivery_type = data.get('delivery_type', 'instant').strip()
        
        if delivery_type not in ['instant', 'manual']:
            delivery_type = 'instant'
        
        if not name or price <= 0 or not category:
            return jsonify({'status': 'error', 'message': 'بيانات ناقصة (الاسم، السعر، الفئة)'})
        
        if delivery_type == 'instant' and not hidden_data:
            return jsonify({'status': 'error', 'message': 'البيانات السرية مطلوبة للتسليم الفوري'})
        
        if delivery_type == 'manual' and not buyer_instructions:
            return jsonify({'status': 'error', 'message': 'يجب تحديد ما تحتاجه من المشتري'})
        
        product_id = str(uuid.uuid4())
        product_data = {
            'id': product_id,
            'item_name': name,
            'price': price,
            'category': category,
            'details': details,
            'hidden_data': hidden_data,
            'buyer_instructions': buyer_instructions,
            'image_url': image,
            'seller_id': ADMIN_ID,
            'seller_name': 'المتجر الرسمي',
            'delivery_type': delivery_type,
            'sold': False,
            'created_at': time.time()
        }
        
        if db:
            db.collection('products').document(product_id).set(product_data)
            print(f"✅ تم حفظ المنتج في Firebase: {name} (التسليم: {delivery_type})")
        
        return jsonify({'status': 'success', 'product_id': product_id})
        
    except Exception as e:
        logger.error(f"Error adding product: {e}")
        return jsonify({'status': 'error', 'message': 'حدث خطأ، حاول لاحقاً'})

@admin_bp.route('/api/admin/delete_product', methods=['POST'])
def api_delete_product():
    """حذف منتج"""
    if not session.get('is_admin'):
        return jsonify({'status': 'error', 'message': 'غير مصرح'})
    
    try:
        data = request.json
        product_id = data.get('product_id')
        
        if not product_id:
            return jsonify({'status': 'error', 'message': 'معرف المنتج مطلوب'})
        
        delete_product(product_id)
        
        return jsonify({'status': 'success'})
        
    except Exception as e:
        logger.error(f"Error deleting product: {e}")
        return jsonify({'status': 'error', 'message': 'حدث خطأ، حاول لاحقاً'})

@admin_bp.route('/api/add_balance', methods=['POST'])
def api_add_balance():
    """شحن رصيد مستخدم"""
    if not session.get('is_admin'):
        return {'status': 'error', 'message': 'غير مصرح!'}
    
    data = request.json
    user_id = str(data.get('user_id'))
    amount = float(data.get('amount'))
    
    if not user_id or amount <= 0:
        return {'status': 'error', 'message': 'بيانات غير صحيحة'}
    
    add_balance(user_id, amount)
    
    try:
        if bot:
            bot.send_message(int(user_id), f"🎉 تم شحن رصيدك بمبلغ {amount} ريال!")
    except:
        pass
    
    return {'status': 'success'}

@admin_bp.route('/api/add_product', methods=['POST'])
def api_add_product():
    """إضافة منتج"""
    if not session.get('is_admin'):
        return {'status': 'error', 'message': 'غير مصرح!'}
    
    try:
        data = request.json
        name = data.get('name')
        price = data.get('price')
        category = data.get('category')
        details = data.get('details', '')
        image = data.get('image', '')
        hidden_data = data.get('hidden_data')
        
        if not name or not price or not hidden_data:
            return {'status': 'error', 'message': 'بيانات غير كاملة'}
        
        new_id = str(uuid.uuid4())
        item = {
            'id': new_id,
            'item_name': name,
            'price': float(price),
            'seller_id': str(ADMIN_ID),
            'seller_name': 'المالك',
            'hidden_data': hidden_data,
            'category': category,
            'details': details,
            'image_url': image,
            'sold': False,
            'created_at': firestore.SERVER_TIMESTAMP
        }
        
        db.collection('products').document(new_id).set(item)
        print(f"✅ تم حفظ المنتج {new_id} في Firestore: {name}")
        
        try:
            if bot:
                bot.send_message(
                    ADMIN_ID,
                    f"✅ **تم إضافة منتج جديد**\n📦 {name}\n💰 {price} ريال",
                    parse_mode="Markdown"
                )
        except Exception as e:
            print(f"فشل إرسال الإشعار: {e}")
            
        return {'status': 'success', 'message': 'تم الحفظ في قاعدة البيانات'}

    except Exception as e:
        print(f"Error in add_product: {e}")
        return {'status': 'error', 'message': f'حدث خطأ في السيرفر: {str(e)}'}

@admin_bp.route('/api/generate_keys', methods=['POST'])
def api_generate_keys():
    """توليد مفاتيح شحن"""
    if not session.get('is_admin'):
        return {'status': 'error', 'message': 'غير مصرح!'}
    
    try:
        data = request.json
        amount = float(data.get('amount'))
        count = int(data.get('count', 1))
        
        if amount <= 0 or count <= 0 or count > 100:
            return {'status': 'error', 'message': 'أرقام غير صحيحة'}
        
        generated_keys = []
        batch = db.batch()
        
        for _ in range(count):
            key_code = f"KEY-{random.randint(10000, 99999)}-{random.randint(1000, 9999)}"
            
            key_data = {
                'amount': amount,
                'used': False,
                'used_by': None,
                'created_at': firestore.SERVER_TIMESTAMP
            }
            
            doc_ref = db.collection('charge_keys').document(key_code)
            batch.set(doc_ref, key_data)
            
            generated_keys.append(key_code)
            
        batch.commit()
        
        return {'status': 'success', 'keys': generated_keys}

    except Exception as e:
        print(f"Error generating keys: {e}")
        return {'status': 'error', 'message': f'فشل التوليد: {str(e)}'}

# ===================== API الأقسام =====================

@admin_bp.route('/api/admin/get_categories', methods=['GET'])
def api_get_categories():
    """جلب قائمة الأقسام"""
    if not session.get('is_admin'):
        return jsonify({'status': 'error', 'message': 'غير مصرح'})
    
    try:
        all_products = get_all_products_for_store()
        category_counts = {}
        for item in all_products:
            cat = item.get('category', '')
            if cat:
                category_counts[cat] = category_counts.get(cat, 0) + 1
        
        categories = get_categories_list()
        
        result = []
        for cat in categories:
            cat_data = cat.copy()
            cat_data['product_count'] = category_counts.get(cat['name'], 0)
            result.append(cat_data)
        
        return jsonify({'status': 'success', 'categories': result})
    except Exception as e:
        logger.error(f"Error getting categories: {e}")
        return jsonify({'status': 'error', 'message': 'حدث خطأ، حاول لاحقاً'})

@admin_bp.route('/api/admin/add_category', methods=['POST'])
def api_add_category():
    """إضافة قسم جديد"""
    if not session.get('is_admin'):
        return jsonify({'status': 'error', 'message': 'غير مصرح'})
    
    try:
        data = request.json
        name = data.get('name', '').strip()
        image_url = data.get('image_url', '').strip()
        delivery_type = data.get('delivery_type', 'instant').strip()
        
        if delivery_type not in ['instant', 'manual']:
            delivery_type = 'instant'
        
        if not name:
            return jsonify({'status': 'error', 'message': 'اسم القسم مطلوب'})
        
        current_categories = get_categories()
        
        for cat in current_categories:
            if cat['name'] == name:
                return jsonify({'status': 'error', 'message': 'هذا القسم موجود مسبقاً'})
        
        cat_id = str(uuid.uuid4())[:8]
        new_order = len(current_categories) + 1
        
        new_category = {
            'id': cat_id,
            'name': name,
            'image_url': image_url or 'https://via.placeholder.com/100?text=' + name,
            'order': new_order,
            'delivery_type': delivery_type,
            'created_at': time.time()
        }
        
        if db:
            db.collection('categories').document(cat_id).set(new_category)
            print(f"✅ تم حفظ القسم في Firebase: {name} ({delivery_type})")
        
        return jsonify({'status': 'success', 'category': new_category})
        
    except Exception as e:
        logger.error(f"Error adding category: {e}")
        return jsonify({'status': 'error', 'message': 'حدث خطأ، حاول لاحقاً'})

@admin_bp.route('/api/admin/update_category', methods=['POST'])
def api_update_category():
    """تعديل قسم"""
    if not session.get('is_admin'):
        return jsonify({'status': 'error', 'message': 'غير مصرح'})
    
    try:
        data = request.json
        cat_id = data.get('id')
        new_name = data.get('name', '').strip()
        new_image = data.get('image_url', '').strip()
        new_delivery_type = data.get('delivery_type', '').strip()
        
        if not cat_id:
            return jsonify({'status': 'error', 'message': 'معرف القسم مطلوب'})
        
        cat_found = get_category_by_id(cat_id)
        
        if not cat_found:
            return jsonify({'status': 'error', 'message': 'القسم غير موجود'})
        
        old_name = cat_found.get('name', '')
        
        update_data = {}
        if new_name:
            update_data['name'] = new_name
        if new_image:
            update_data['image_url'] = new_image
        if new_delivery_type in ['instant', 'manual']:
            update_data['delivery_type'] = new_delivery_type
        
        update_category(cat_id, update_data)
        
        if old_name and new_name and old_name != new_name:
            all_products = get_all_products_for_store()
            for item in all_products:
                if item.get('category') == old_name:
                    if item.get('id'):
                        try:
                            db.collection('products').document(item['id']).update({'category': new_name})
                        except:
                            pass
        
        cat_found.update(update_data)
        return jsonify({'status': 'success', 'category': cat_found})
        
    except Exception as e:
        logger.error(f"Error updating category: {e}")
        return jsonify({'status': 'error', 'message': 'حدث خطأ، حاول لاحقاً'})

@admin_bp.route('/api/admin/delete_category', methods=['POST'])
def api_delete_category():
    """حذف قسم"""
    if not session.get('is_admin'):
        return jsonify({'status': 'error', 'message': 'غير مصرح'})
    
    try:
        data = request.json
        cat_id = data.get('id')
        
        if not cat_id:
            return jsonify({'status': 'error', 'message': 'معرف القسم مطلوب'})
        
        cat_found = get_category_by_id(cat_id)
        
        if not cat_found:
            return jsonify({'status': 'error', 'message': 'القسم غير موجود'})
        
        product_count = count_products_in_category(cat_found.get('name', ''))
        
        if product_count > 0:
            return jsonify({
                'status': 'error', 
                'message': f'لا يمكن حذف القسم - يوجد {product_count} منتج فيه'
            })
        
        delete_category(cat_id)
        
        return jsonify({'status': 'success'})
        
    except Exception as e:
        logger.error(f"Error deleting category: {e}")
        return jsonify({'status': 'error', 'message': 'حدث خطأ، حاول لاحقاً'})

@admin_bp.route('/api/admin/reorder_categories', methods=['POST'])
def api_reorder_categories():
    """إعادة ترتيب الأقسام"""
    if not session.get('is_admin'):
        return jsonify({'status': 'error', 'message': 'غير مصرح'})
    
    try:
        data = request.json
        new_order = data.get('order', [])
        
        if not new_order:
            return jsonify({'status': 'error', 'message': 'الترتيب مطلوب'})
        
        for idx, cat_id in enumerate(new_order):
            if db:
                try:
                    db.collection('categories').document(cat_id).update({'order': idx + 1})
                except:
                    pass
        
        return jsonify({'status': 'success'})
        
    except Exception as e:
        logger.error(f"Error reordering categories: {e}")
        return jsonify({'status': 'error', 'message': 'حدث خطأ، حاول لاحقاً'})

@admin_bp.route('/api/categories', methods=['GET'])
def api_public_categories():
    """جلب الأقسام للعرض العام"""
    try:
        categories = get_categories_list()
        result = []
        for cat in categories:
            result.append({
                'name': cat['name'],
                'image_url': cat.get('image_url', ''),
                'delivery_type': cat.get('delivery_type', 'instant')
            })
        return jsonify({
            'status': 'success', 
            'categories': result,
            'columns': display_settings.get('categories_columns', 3)
        })
    except Exception as e:
        logger.error(f"Error in public categories: {e}")
        return jsonify({'status': 'error', 'message': 'حدث خطأ، حاول لاحقاً'})

# ===================== إعدادات العرض =====================

@admin_bp.route('/api/admin/get_display_settings', methods=['GET'])
def api_get_display_settings():
    """جلب إعدادات العرض"""
    if not session.get('is_admin'):
        return jsonify({'status': 'error', 'message': 'غير مصرح'})
    
    return jsonify({
        'status': 'success',
        'categories_columns': display_settings.get('categories_columns', 3)
    })

@admin_bp.route('/api/admin/set_display_settings', methods=['POST'])
def api_set_display_settings():
    """تعديل إعدادات العرض"""
    if not session.get('is_admin'):
        return jsonify({'status': 'error', 'message': 'غير مصرح'})
    
    try:
        data = request.json
        cols = data.get('categories_columns')
        
        if cols and cols in [2, 3, 4]:
            display_settings['categories_columns'] = cols
            
            if db:
                db.collection('settings').document('display').set({
                    'categories_columns': cols
                }, merge=True)
            
            return jsonify({'status': 'success'})
        else:
            return jsonify({'status': 'error', 'message': 'قيمة غير صالحة'})
            
    except Exception as e:
        logger.error(f"Error setting display settings: {e}")
        return jsonify({'status': 'error', 'message': 'حدث خطأ، حاول لاحقاً'})

# ===================== دالة التهيئة =====================

def init_admin(app_db, app_bot, admin_id, app_limiter=None, bot_active=False):
    """تهيئة لوحة التحكم"""
    global db, bot, ADMIN_ID, limiter, BOT_ACTIVE
    db = app_db
    bot = app_bot
    ADMIN_ID = admin_id
    limiter = app_limiter
    BOT_ACTIVE = bot_active
    
    # تحميل إعدادات العرض من Firebase
    try:
        if db:
            doc = db.collection('settings').document('display').get()
            if doc.exists:
                data = doc.to_dict()
                display_settings['categories_columns'] = data.get('categories_columns', 3)
    except:
        pass
    
    print("✅ تم تهيئة لوحة التحكم")
