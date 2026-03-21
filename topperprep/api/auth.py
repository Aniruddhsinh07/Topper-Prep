import frappe
import random
import string
from frappe.utils import now_datetime, add_to_date
from frappe.utils.password import update_password
from frappe.auth import LoginManager
import requests

OTP_EXPIRY_MINUTES = 2
OTP_EXPIRY_SECONDS = OTP_EXPIRY_MINUTES * 60
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


def _generate_reference_code(length=8):
    """Generate a unique alphanumeric reference code for an institute."""
    characters = string.ascii_uppercase + string.digits
    while True:
        code = "".join(random.choices(characters, k=length))
        # Ensure uniqueness
        if not frappe.db.exists("Institute", {"reference_code": code}):
            return code


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


# ===========================================================================
# INSTITUTE APIs
# ===========================================================================

# ---------------------------------------------------------------------------
# I-1. Register Institute  →  cache data + send OTP
# ---------------------------------------------------------------------------

@frappe.whitelist(allow_guest=True)
def register_institute(institute_name, phone, password, email=None):
    """
    Step 1 of Institute signup.
    Validates uniqueness, stores registration data in cache, sends OTP.
    Institute User is NOT created until OTP is verified.
    """

    # --- Uniqueness checks ---
    if email and frappe.db.exists("User", email):
        return {"status": "error", "message": "Email already registered"}

    if frappe.db.exists("User", {"mobile_no": phone}):
        return {"status": "error", "message": "Phone number already registered"}

    if frappe.db.exists("Institute", {"phone": phone}):
        return {"status": "error", "message": "An institute with this phone already exists"}

    # --- Store registration data in cache ---
    pending_data = {
        "institute_name": institute_name,
        "phone": phone,
        "email": email,
        "password": password,
        "user_type": "Institute",
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
# I-2. Verify Institute OTP  →  creates User + Institute doc + reference code
# ---------------------------------------------------------------------------

@frappe.whitelist(allow_guest=True)
def verify_institute_otp(phone, otp):
    """
    Step 2 of Institute signup.
    On success: creates User (Institute role) + Institute document with reference code.
    """

    otp_doc = _get_valid_otp_doc(mobile=phone, purpose="Signup")

    if not otp_doc:
        return {"status": "error", "message": "OTP expired or not found. Please request a new OTP."}

    if otp_doc.attempt_count >= MAX_OTP_ATTEMPTS:
        return {"status": "error", "message": "Too many incorrect attempts. Please request a new OTP."}

    if str(otp_doc.otp_code) != str(otp):
        otp_doc.attempt_count += 1
        otp_doc.save(ignore_permissions=True)
        frappe.db.commit()
        remaining = MAX_OTP_ATTEMPTS - otp_doc.attempt_count
        return {"status": "error", "message": f"Invalid OTP. {remaining} attempt(s) remaining."}

    # --- OTP correct — mark used ---
    otp_doc.is_used = 1
    otp_doc.save(ignore_permissions=True)

    # --- Retrieve cached data ---
    cache_key = _pending_reg_cache_key(phone)
    pending = frappe.cache().get_value(cache_key)

    if not pending:
        return {"status": "error", "message": "Registration session expired. Please sign up again."}

    if pending.get("user_type") != "Institute":
        return {"status": "error", "message": "Invalid registration type for this endpoint."}

    # --- Re-check uniqueness ---
    if pending.get("email") and frappe.db.exists("User", pending["email"]):
        return {"status": "error", "message": "Email already registered"}

    if frappe.db.exists("User", {"mobile_no": phone}):
        return {"status": "error", "message": "Phone number already registered"}

    # --- Create User with Institute role ---
    user = frappe.get_doc({
        "doctype": "User",
        "email": pending["email"] if pending.get("email") else f"{phone}@institute.com",
        "first_name": pending["institute_name"],
        "mobile_no": phone,
        "enabled": 1,
        "role_profile_name": "Institute",   # <-- Institute role profile
    })
    user.insert(ignore_permissions=True)
    update_password(user.name, pending["password"])

    # --- Generate unique reference code ---
    reference_code = _generate_reference_code()

    # --- Create Institute document ---
    institute_doc = frappe.get_doc({
        "doctype": "Institute",
        "institute_name": pending["institute_name"],
        "phone": phone,
        "email": pending.get("email", ""),
        "user": user.name,                  # Link to Frappe User
        "reference_code": reference_code,
    })
    institute_doc.insert(ignore_permissions=True)

    # Clean up cache
    frappe.cache().delete_value(cache_key)
    frappe.db.commit()

    return {
        "status": "success",
        "message": "Institute registered successfully",
        "reference_code": reference_code
    }


# ---------------------------------------------------------------------------
# I-3. Add Student to Institute Students list (by institute)
# ---------------------------------------------------------------------------

@frappe.whitelist(allow_guest=True)
def add_institute_student(student_mobile):
    """
    Called by an authenticated Institute user.
    Adds a student mobile to the Institute's student list.
    If the student already has an account, links them immediately.
    """

    # --- Identify the calling institute ---
    institute = frappe.db.get_value(
        "Institute",
        {"user": frappe.session.user},
        ["name", "reference_code"],
        as_dict=True
    )

    if not institute:
        return {"status": "error", "message": "Only institute accounts can add students"}

    # --- Check if student already in this institute's list ---
    existing = frappe.db.exists(
        "Institute Students",
        {"institute": institute["name"], "student_mobile": student_mobile}
    )
    if existing:
        return {"status": "error", "message": "Student already added to this institute"}

    # --- Check if student has a Frappe User account ---
    user_exists = frappe.db.get_value("User", {"mobile_no": student_mobile}, "name")

    # --- Add to Institute Students ---
    student_entry = frappe.get_doc({
        "doctype": "Institute Students",
        "institute": institute["name"],
        "student_mobile": student_mobile,
        "student_user": user_exists if user_exists else None,   # Link if account exists
        "is_verified": 1 if user_exists else 0,
    })
    student_entry.insert(ignore_permissions=True)
    frappe.db.commit()

    return {
        "status": "success",
        "message": "Student added successfully",
        "linked": bool(user_exists)
    }


# ---------------------------------------------------------------------------
# I-4. Get Institute Students + Progress (institute sees only their students)
# ---------------------------------------------------------------------------

@frappe.whitelist(allow_guest=True)
def get_institute_students():
    """
    Returns all students belonging to the calling institute,
    with their progress data. Institute can only see their own students.
    """

    institute = frappe.db.get_value(
        "Institute",
        {"user": frappe.session.user},
        "name"
    )

    if not institute:
        return {"status": "error", "message": "Only institute accounts can view students"}

    students = frappe.get_all(
        "Institute Students",
        filters={"institute": institute},
        fields=[
            "name",
            "student_mobile",
            "student_user",
            "is_verified",
            "creation"
        ]
    )

    # --- Enrich with user info if account exists ---
    enriched = []
    for s in students:
        record = dict(s)
        if s.get("student_user"):
            user_info = frappe.db.get_value(
                "User",
                s["student_user"],
                ["first_name", "last_name", "email", "mobile_no"],
                as_dict=True
            )
            record["full_name"] = f"{user_info.get('first_name', '')} {user_info.get('last_name', '')}".strip()
            record["email"] = user_info.get("email", "")

            # ---------------------------------------------------------------
            # TODO: Replace the section below with your actual progress doctype.
            # Example assumes a "Course Progress" doctype with fields:
            #   student (link to User), course, completion_percentage, last_activity
            # ---------------------------------------------------------------
            progress = frappe.get_all(
                "Course Progress",
                filters={"student": s["student_user"]},
                fields=["course", "completion_percentage", "last_activity"],
                ignore_permissions=True
            ) if frappe.db.table_exists("tabCourse Progress") else []

            record["progress"] = progress
        else:
            record["full_name"] = None
            record["email"] = None
            record["progress"] = []

        enriched.append(record)

    return {"status": "success", "students": enriched}


# ---------------------------------------------------------------------------
# I-5. Get Institute Reference Code (for logged-in institute)
# ---------------------------------------------------------------------------

@frappe.whitelist(allow_guest=True)
def get_reference_code():
    """Returns the reference code of the logged-in institute user."""

    institute = frappe.db.get_value(
        "Institute",
        {"user": frappe.session.user},
        ["reference_code", "institute_name"],
        as_dict=True
    )

    if not institute:
        return {"status": "error", "message": "Not an institute account"}

    return {
        "status": "success",
        "reference_code": institute["reference_code"],
        "institute_name": institute["institute_name"]
    }


# ===========================================================================
# STUDENT APIs  (updated to support reference code)
# ===========================================================================

# ---------------------------------------------------------------------------
# 1. Register User  →  cache data + send OTP (no User created yet)
# ---------------------------------------------------------------------------

@frappe.whitelist(allow_guest=True)
def register_user(full_name, phone, password, email=None, reference_code=None):
    """
    Step 1 of Student signup.
    Validates uniqueness, optionally validates reference_code,
    stores registration data in cache, sends OTP.
    User is NOT created until OTP is verified.
    """

    # --- Uniqueness checks ---
    if email and frappe.db.exists("User", email):
        return {"status": "error", "message": "Email already registered"}

    if frappe.db.exists("User", {"mobile_no": phone}):
        return {"status": "error", "message": "Phone number already registered"}

    # --- Validate reference code (if provided) ---
    institute_name = None
    if reference_code:
        institute_name = frappe.db.get_value(
            "Institute",
            {"reference_code": reference_code},
            "name"
        )
        if not institute_name:
            return {"status": "error", "message": "Invalid reference code"}

    # --- Store registration data in cache ---
    pending_data = {
        "full_name": full_name,
        "phone": phone,
        "email": email,
        "password": password,
        "reference_code": reference_code,
        "institute": institute_name,        # store resolved institute name
        "user_type": "Student",
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
    Step 2 of Student signup (or forgot-password verification).
    Validates OTP. On success for Signup: creates and enables the User,
    then links to Institute if a reference code was used.
    """

    otp_doc = _get_valid_otp_doc(mobile=phone, purpose=purpose)

    if not otp_doc:
        return {"status": "error", "message": "OTP expired or not found. Please request a new OTP."}

    if otp_doc.attempt_count >= MAX_OTP_ATTEMPTS:
        return {"status": "error", "message": "Too many incorrect attempts. Please request a new OTP."}

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

        # Route institute signups to their own verifier
        if pending.get("user_type") == "Institute":
            return {
                "status": "error",
                "message": "Please use the institute OTP verification endpoint."
            }

        # Re-check uniqueness
        if pending.get("email") and frappe.db.exists("User", pending["email"]):
            return {"status": "error", "message": "Email already registered"}

        if frappe.db.exists("User", {"mobile_no": phone}):
            return {"status": "error", "message": "Phone number already registered"}

        # --- Create Student User ---
        user = frappe.get_doc({
            "doctype": "User",
            "email": pending["email"] if pending.get("email") else f"{phone}@app.com",
            "first_name": pending["full_name"],
            "mobile_no": phone,
            "enabled": 1,
            "role_profile_name": "Student",
        })
        user.insert(ignore_permissions=True)
        update_password(user.name, pending["password"])

        # --- Institute linking: match student via reference code ---
        institute = pending.get("institute")
        if institute:
            # Check if institute pre-added this student
            existing_entry = frappe.db.get_value(
                "Institute Students",
                {"institute": institute, "student_mobile": phone},
                "name"
            )

            if existing_entry:
                # Student was pre-added — update the existing record
                frappe.db.set_value(
                    "Institute Students",
                    existing_entry,
                    {
                        "student_user": user.name,
                        "is_verified": 1,
                    }
                )
            else:
                # Student used the code but wasn't pre-added — create new record
                new_entry = frappe.get_doc({
                    "doctype": "Institute Students",
                    "institute": institute,
                    "student_mobile": phone,
                    "student_user": user.name,
                    "is_verified": 1,
                })
                new_entry.insert(ignore_permissions=True)

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
    update_password(username, password)

    frappe.db.delete("Sessions", {"user": username})
    frappe.db.commit()

    return {"status": "success", "message": "Password reset successfully"}


# ---------------------------------------------------------------------------
# 5. Login
# ---------------------------------------------------------------------------

@frappe.whitelist(allow_guest=True)
def login_user(username, password, device_id):

    if username.isdigit():
        user_doc = frappe.get_doc("User", {"mobile_no": username})
        username = user_doc.name
    else:
        user_doc = frappe.get_doc("User", username)

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

    # --- Identify user type and return extra context ---
    user_type = "Student"
    extra = {}

    institute = frappe.db.get_value(
        "Institute",
        {"user": user_doc.name},
        ["name", "reference_code", "institute_name"],
        as_dict=True
    )
    if institute:
        user_type = "Institute"
        extra["reference_code"] = institute["reference_code"]
        extra["institute_name"] = institute["institute_name"]

    return {
        "status": "success",
        "message": "Login successful",
        "sid": frappe.session.sid,
        "user_type": user_type,
        **extra
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