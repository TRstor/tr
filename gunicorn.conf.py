# تكوين Gunicorn لـ Render

# عدد الـ Workers (عمليات التشغيل)
workers = 1

# نوع الـ Worker
worker_class = 'sync'

# المنفذ - يتم تحديده تلقائياً من متغير PORT
bind = '0.0.0.0:10000'

# مهلة الطلب (بالثواني)
timeout = 120

# الملفات المراد مراقبتها للتحديث التلقائي (في التطوير فقط)
reload = False

# مستوى السجلات
loglevel = 'info'

# تنسيق السجلات
accesslog = '-'
errorlog = '-'

# عدد الاتصالات المتزامنة لكل worker
worker_connections = 1000

# الحد الأقصى للطلبات قبل إعادة تشغيل worker
max_requests = 1000
max_requests_jitter = 50

# حفظ PID
pidfile = None

# معالجة الأخطاء
graceful_timeout = 30
keepalive = 5
