import frappe
import random
from frappe.utils.password import update_password
import requests

@frappe.whitelist(allow_guest=True)
def register_user(full_name, email=None, phone=None):
    
    if not email and not phone:
        return {"status": "error", "message": "Email or Phone required"}

    # Check if already exists
    if email and frappe.db.exists("User", email):
        return {"status": "error", "message": "Email already registered"}

    if phone and frappe.db.exists("User", {"mobile_no": phone}):
        return {"status": "error", "message": "Phone already registered"}

    # Generate OTP
    otp = random.randint(100000, 999999)

    user = frappe.get_doc({
        "doctype": "User",
        "email": email if email else f"{phone}@app.com",
        "first_name": full_name,
        "mobile_no": phone,
        "enabled": 0,  # disable until verified
        "otp": otp
    })
    user.insert(ignore_permissions=True)

    # Store OTP temporarily
    frappe.cache().set_value(f"otp_{phone}", otp, expires_in_sec=300)


    # Send WhatsApp OTP (Interakt API call)
    # send_whatsapp_otp(phone, otp)

    return {
        "status": "success",
        "message": "OTP sent to WhatsApp",
        "user": user.name
    }




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

@frappe.whitelist(allow_guest=True)
def verify_otp(phone, otp):

    cached_otp = frappe.cache().get_value(f"otp_{phone}")

    if not cached_otp:
        return {"status": "error", "message": "OTP expired"}

    if str(cached_otp) != str(otp):
        return {"status": "error", "message": "Invalid OTP"}

    # Enable user
    user = frappe.get_doc("User", {"mobile_no": phone})
    user.enabled = 1
    user.role_profile_name = "Student"
    user.save(ignore_permissions=True)

    frappe.cache().delete_value(f"otp_{phone}")

    return {"status": "success", "message": "OTP verified"}

@frappe.whitelist(allow_guest=True)
def set_password(phone, password):

    user = frappe.get_doc("User", {"mobile_no": phone})

    update_password(user.name, password)

    return {"status": "success", "message": "Password set successfully"}

import frappe
from frappe.auth import LoginManager

@frappe.whitelist(allow_guest=True)
def login_user(username, password, device_id):

    # Convert phone to email if needed
    if username.isdigit():
        user_doc = frappe.get_doc("User", {"mobile_no": username})
        username = user_doc.name
    else:
        user_doc = frappe.get_doc("User", username)

    # 🚫 Check if already logged in on another device
    if user_doc.is_mobile_logged_in and user_doc.last_device_id != device_id:
        return {
            "status": "error",
            "message": "You are already logged in on another device"
        }

    # Authenticate
    login_manager = LoginManager()
    login_manager.authenticate(username, password)
    login_manager.post_login()

    # Save device info
    user_doc.db_set("last_device_id", device_id)
    user_doc.db_set("is_mobile_logged_in", 1)

    return {
        "status": "success",
        "message": "Login successful",
        "sid": frappe.session.sid
    }

@frappe.whitelist()
def logout_user():
    user = frappe.session.user

    user_doc = frappe.get_doc("User", user)
    user_doc.db_set("is_mobile_logged_in", 0)
    user_doc.db_set("last_device_id", "")

    frappe.local.login_manager.logout()

    return {"status": "success", "message": "Logged out successfully"}