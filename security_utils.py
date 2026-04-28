# -*- coding: utf-8 -*-
"""
أدوات أمنية:
  - تشفير الحقول الحساسة عبر Fernet (cryptography)
  - مصادقة ثنائية TOTP عبر pyotp للأوامر الخطرة
"""
import os
import base64
import hashlib
import time

# ====== Fernet (تشفير الحقول الحساسة) ======
_fernet_cache = {"obj": None, "tried": False}


def _derive_key(secret: str) -> bytes:
    """يشتق مفتاح Fernet (32 بايت base64) من أي نص سرّي."""
    digest = hashlib.sha256(secret.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def _get_fernet():
    if _fernet_cache["obj"] is not None or _fernet_cache["tried"]:
        return _fernet_cache["obj"]
    _fernet_cache["tried"] = True
    try:
        from cryptography.fernet import Fernet  # type: ignore
    except Exception as e:
        print(f"⚠️ cryptography غير مثبت: {e}")
        return None
    secret = os.environ.get("FERNET_KEY") or os.environ.get("BOT_TOKEN") or ""
    if not secret:
        print("⚠️ FERNET_KEY غير مضبوط — التشفير معطّل")
        return None
    try:
        _fernet_cache["obj"] = Fernet(_derive_key(secret))
    except Exception as e:
        print(f"⚠️ تعذّر إنشاء Fernet: {e}")
    return _fernet_cache["obj"]


def encrypt_field(value) -> str:
    """يشفّر نصاً ويعيد سلسلة base64. يعيد '' إن كان value فارغاً."""
    if value is None or value == "":
        return ""
    f = _get_fernet()
    if f is None:
        # في حال غياب Fernet، نعيد القيمة كما هي حتى لا نكسر التطبيق
        return str(value)
    try:
        token = f.encrypt(str(value).encode("utf-8"))
        return token.decode("utf-8")
    except Exception as e:
        print(f"⚠️ encrypt_field: {e}")
        return str(value)


def decrypt_field(token) -> str:
    """يفك تشفير نص. يعيد القيمة الأصلية إن لم تكن مشفّرة."""
    if not token:
        return ""
    f = _get_fernet()
    if f is None:
        return str(token)
    try:
        return f.decrypt(str(token).encode("utf-8")).decode("utf-8")
    except Exception:
        # القيمة ليست مشفّرة (legacy plain text) — نعيدها كما هي
        return str(token)


# ====== TOTP (2FA للأوامر الخطرة) ======
_totp_cache = {"obj": None, "tried": False}


def _get_totp():
    if _totp_cache["obj"] is not None or _totp_cache["tried"]:
        return _totp_cache["obj"]
    _totp_cache["tried"] = True
    try:
        import pyotp  # type: ignore
    except Exception as e:
        print(f"⚠️ pyotp غير مثبت: {e}")
        return None
    secret = os.environ.get("TOTP_SECRET", "").strip()
    if not secret:
        print("⚠️ TOTP_SECRET غير مضبوط — 2FA معطّل")
        return None
    try:
        _totp_cache["obj"] = pyotp.TOTP(secret)
    except Exception as e:
        print(f"⚠️ تعذّر إنشاء TOTP: {e}")
    return _totp_cache["obj"]


def totp_enabled() -> bool:
    return _get_totp() is not None


def verify_totp(code: str) -> bool:
    """يتحقق من رمز 6 أرقام. يقبل ±30 ثانية."""
    t = _get_totp()
    if t is None:
        return False
    code = (code or "").strip().replace(" ", "")
    if not code.isdigit() or len(code) != 6:
        return False
    try:
        return bool(t.verify(code, valid_window=1))
    except Exception as e:
        print(f"⚠️ verify_totp: {e}")
        return False


def totp_provisioning_uri(account_name: str = "owner",
                         issuer: str = "XO Bot") -> str:
    """يعيد رابط otpauth:// لإضافة الحساب في Google Authenticator/Authy."""
    t = _get_totp()
    if t is None:
        return ""
    try:
        return t.provisioning_uri(name=account_name, issuer_name=issuer)
    except Exception as e:
        print(f"⚠️ provisioning_uri: {e}")
        return ""


def generate_totp_secret() -> str:
    """يولّد سرّ TOTP جديد عشوائي base32 (لاستخدامه مرة عند التثبيت)."""
    try:
        import pyotp  # type: ignore
        return pyotp.random_base32()
    except Exception:
        return ""


# ====== مساعد طلبات 2FA معلّقة ======
# {uid: {"action": "reset", "ts": float, "expires": float}}
_pending_2fa = {}
TWOFA_TTL = 120  # ثانية


def request_2fa(uid, action: str):
    """يبدأ طلب 2FA — يعيد True إذا تم التسجيل."""
    _pending_2fa[uid] = {
        "action": action,
        "ts": time.time(),
        "expires": time.time() + TWOFA_TTL,
    }
    return True


def get_pending_2fa(uid):
    """يعيد طلب 2FA المعلّق إن وُجد ولم تنتهِ صلاحيته."""
    p = _pending_2fa.get(uid)
    if not p:
        return None
    if time.time() > p["expires"]:
        _pending_2fa.pop(uid, None)
        return None
    return p


def consume_2fa(uid):
    return _pending_2fa.pop(uid, None)


def cancel_2fa(uid):
    _pending_2fa.pop(uid, None)
