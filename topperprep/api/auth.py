import frappe
import random
from frappe.utils import now_datetime, add_to_date
from frappe.utils.password import update_password
from frappe.auth import LoginManager
import requests

OTP_EXPIRY_MINUTES = 2
OTP_EXPIRY_SECONDS = OTP_EXPIRY_MINUTES * 60  # 120 seconds — same TTL for cache + OTP record
MAX_OTP_ATTEMPTS = 5


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create_otp_record(mobile, purpose):
    """Delete old unused OTPs for this mobile+purpose, then create a fresh one."""

    old_otps = frappe.get_all(
        "OTP Verifications",
        filters={"mobile": mobile, "purpose": purpose, "is_used": 0},
        pluck="name"
    )
    for name in old_otps:
        frappe.delete_doc("OTP Verifications", name, ignore_permissions=True)

    otp_code = random.randint(100000, 999999)
    created_at = now_datetime()
    expires_at = add_to_date(created_at, minutes=OTP_EXPIRY_MINUTES)

    doc = frappe.get_doc({
        "doctype": "OTP Verifications",
        "mobile": mobile,
        "otp_code": otp_code,
        "purpose": purpose,
        "attempt_count": 0,
        "is_used": 0,
        "created_at": created_at,
        "expires_at": expires_at,
    })
    doc.insert(ignore_permissions=True)
    frappe.db.commit()

    return otp_code


def _get_valid_otp_doc(mobile, purpose):
    """Return the latest unused, non-expired OTP doc. Returns None if not found/expired."""

    records = frappe.get_all(
        "OTP Verifications",
        filters={"mobile": mobile, "purpose": purpose, "is_used": 0},
        fields=["name"],
        order_by="creation desc",
        limit=1
    )
    if not records:
        return None

    doc = frappe.get_doc("OTP Verifications", records[0]["name"])

    if now_datetime() > doc.expires_at:
        return None

    return doc


def _pending_reg_cache_key(phone):
    return f"pending_registration_{phone}"


# ---------------------------------------------------------------------------
# WhatsApp OTP Sender (Interakt)
# ---------------------------------------------------------------------------

def send_whatsapp_otp(phone, otp):
    url = "https://api.interakt.ai/v1/public/message/"
    headers = {
        "Authorization": "Basic YOUR_INTERAKT_API_KEY",
        "Content-Type": "application/json"
    }
    payload = {
        "countryCode": "+91",
        "phoneNumber": phone,
        "type": "Template",
        "template": {
            "name": "otp_template",
            "languageCode": "en",
            "bodyValues": [str(otp)]
        }
    }
    requests.post(url, json=payload, headers=headers)


# ---------------------------------------------------------------------------
# 1. Register User  →  cache data + send OTP (no User created yet)
# ---------------------------------------------------------------------------

@frappe.whitelist(allow_guest=True)
def register_user(full_name, phone, password, email=None):
    """
    Step 1 of signup.
    Validates uniqueness, stores registration data in cache,
    sends OTP. User is NOT created until OTP is verified.
    """

    # --- Uniqueness checks ---
    if email and frappe.db.exists("User", email):
        return {"status": "error", "message": "Email already registered"}

    if frappe.db.exists("User", {"mobile_no": phone}):
        return {"status": "error", "message": "Phone number already registered"}

    # --- Store registration data in cache (expires with OTP in 2 min) ---
    pending_data = {
        "full_name": full_name,
        "phone": phone,
        "email": email,
        "password": password,
    }
    frappe.cache().set_value(
        _pending_reg_cache_key(phone),
        pending_data,
        expires_in_sec=OTP_EXPIRY_SECONDS
    )

    # --- Create OTP record & send ---
    otp = _create_otp_record(mobile=phone, purpose="Signup")
    # send_whatsapp_otp(phone, otp)  # Uncomment when ready

    return {
        "status": "success",
        "message": "OTP sent to WhatsApp. Please verify within 2 minutes."
    }


# ---------------------------------------------------------------------------
# 2. Verify OTP  →  on success, create User from cached data
# ---------------------------------------------------------------------------

@frappe.whitelist(allow_guest=True)
def verify_otp(phone, otp, purpose="Signup"):
    """
    Step 2 of signup (or forgot-password verification).
    Validates OTP. On success for Signup: creates and enables the User.
    """

    otp_doc = _get_valid_otp_doc(mobile=phone, purpose=purpose)

    if not otp_doc:
        return {"status": "error", "message": "OTP expired or not found. Please request a new OTP."}

    # --- Max attempts guard ---
    if otp_doc.attempt_count >= MAX_OTP_ATTEMPTS:
        return {"status": "error", "message": "Too many incorrect attempts. Please request a new OTP."}

    # --- Wrong OTP ---
    if str(otp_doc.otp_code) != str(otp):
        otp_doc.attempt_count += 1
        otp_doc.save(ignore_permissions=True)
        frappe.db.commit()
        remaining = MAX_OTP_ATTEMPTS - otp_doc.attempt_count
        return {"status": "error", "message": f"Invalid OTP. {remaining} attempt(s) remaining."}

    # --- OTP is correct — mark as used ---
    otp_doc.is_used = 1
    otp_doc.save(ignore_permissions=True)

    # --- Signup: create User from cached data ---
    if purpose == "Signup":
        cache_key = _pending_reg_cache_key(phone)
        pending = frappe.cache().get_value(cache_key)

        if not pending:
            return {
                "status": "error",
                "message": "Registration session expired. Please sign up again."
            }

        # Re-check uniqueness (edge case: duplicate request in the 2 min window)
        if pending.get("email") and frappe.db.exists("User", pending["email"]):
            return {"status": "error", "message": "Email already registered"}

        if frappe.db.exists("User", {"mobile_no": phone}):
            return {"status": "error", "message": "Phone number already registered"}

        # Create the user — already enabled, OTP is proof of ownership
        # NOTE: Do NOT pass new_password here — Frappe runs password_strength_test()
        # during validate() on insert which can reject valid passwords.
        user = frappe.get_doc({
            "doctype": "User",
            "email": pending["email"] if pending.get("email") else f"{phone}@app.com",
            "first_name": pending["full_name"],
            "mobile_no": phone,
            "enabled": 1,
            "role_profile_name": "Student",
        })
        user.insert(ignore_permissions=True)

        # Set password directly after insert — update_password() writes to
        # the __Auth table and bypasses password_strength_test() entirely.
        update_password(user.name, pending["password"])

        # Clean up cache
        frappe.cache().delete_value(cache_key)

    frappe.db.commit()

    return {"status": "success", "message": "OTP verified successfully"}


# ---------------------------------------------------------------------------
# 3. Forgot Password  →  send OTP with purpose="Forgot Password"
# ---------------------------------------------------------------------------

@frappe.whitelist(allow_guest=True)
def forgot_password(phone):

    if not frappe.db.exists("User", {"mobile_no": phone}):
        return {"status": "error", "message": "No account found with this phone number"}

    otp = _create_otp_record(mobile=phone, purpose="Forgot Password")
    # send_whatsapp_otp(phone, otp)  # Uncomment when ready

    return {"status": "success", "message": "OTP sent to WhatsApp"}


# ---------------------------------------------------------------------------
# 4. Set / Reset Password  (called after forgot-password OTP is verified)
# ---------------------------------------------------------------------------

@frappe.whitelist(allow_guest=True)
def set_password(phone, password):
    """
    Called after verify_otp(purpose="Forgot Password") succeeds.
    Safety check: a verified OTP must exist for this phone.
    """

    verified = frappe.get_all(
        "OTP Verifications",
        filters={"mobile": phone, "purpose": "Forgot Password", "is_used": 1},
        order_by="modified desc",
        limit=1
    )

    if not verified:
        return {"status": "error", "message": "OTP verification required before resetting password"}

    users = frappe.get_all("User", filters={"mobile_no": phone}, pluck="name", limit=1)
    if not users:
        return {"status": "error", "message": "No account found with this phone number"}

    username = users[0]

    # update_password writes directly to __Auth table -
    # bypasses password_strength_test() so any password is accepted
    update_password(username, password)

    # Force-clear sessions so old password cannot be reused
    frappe.db.delete("Sessions", {"user": username})
    frappe.db.commit()

    return {"status": "success", "message": "Password reset successfully"}


# ---------------------------------------------------------------------------
# 5. Login
# ---------------------------------------------------------------------------

@frappe.whitelist(allow_guest=True)
def login_user(username, password, device_id):

    # Resolve phone → email (Frappe username)
    if username.isdigit():
        user_doc = frappe.get_doc("User", {"mobile_no": username})
        username = user_doc.name
    else:
        user_doc = frappe.get_doc("User", username)

    # Single-device enforcement
    if user_doc.is_mobile_logged_in and user_doc.last_device_id != device_id:
        return {
            "status": "error",
            "message": "You are already logged in on another device"
        }

    login_manager = LoginManager()
    login_manager.authenticate(username, password)
    login_manager.post_login()

    user_doc.db_set("last_device_id", device_id)
    user_doc.db_set("is_mobile_logged_in", 1)

    return {
        "status": "success",
        "message": "Login successful",
        "sid": frappe.session.sid
    }


# ---------------------------------------------------------------------------
# 6. Logout
# ---------------------------------------------------------------------------

@frappe.whitelist()
def logout_user():

    user = frappe.session.user
    user_doc = frappe.get_doc("User", user)
    user_doc.db_set("is_mobile_logged_in", 0)
    user_doc.db_set("last_device_id", "")

    frappe.local.login_manager.logout()

    return {"status": "success", "message": "Logged out successfully"}