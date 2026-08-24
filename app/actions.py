# app/actions.py
import os
import json
import stripe
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail
from twilio.rest import Client
from slack_sdk.webhook import WebhookClient
from dotenv import load_dotenv

load_dotenv()

DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"

# --- Initialize Clients (only if keys exist) ---
stripe.api_key = os.getenv("STRIPE_API_KEY")
twilio_client = Client(os.getenv("TWILIO_ACCOUNT_SID"), os.getenv("TWILIO_AUTH_TOKEN")) if os.getenv("TWILIO_ACCOUNT_SID") else None
sendgrid_client = SendGridAPIClient(os.getenv("SENDGRID_API_KEY")) if os.getenv("SENDGRID_API_KEY") else None
slack_client = WebhookClient(os.getenv("SLACK_WEBHOOK_URL")) if os.getenv("SLACK_WEBHOOK_URL") else None

# --- 1. Payment Retry (Stripe) ---
async def retry_payment(customer_id: str, amount_usd: float, currency: str = "usd", metadata: dict = None):
    """Attempt to charge a customer again using Stripe."""
    print(f"💳 [ACTION] Attempting payment retry for {customer_id} (Amount: ${amount_usd})")
    
    if DRY_RUN:
        print(f"   🏜️ DRY RUN: Would charge ${amount_usd} to customer {customer_id}")
        return {"success": True, "dry_run": True}
    
    try:
        # In production, you would create a PaymentIntent using the customer's default payment method.
        # For MVP, we'll simulate creating a PaymentIntent.
        # Actual implementation: retrieve customer's default payment method and create intent.
        intent = stripe.PaymentIntent.create(
            amount=int(amount_usd * 100),  # Stripe uses cents
            currency=currency,
            customer=customer_id,
            metadata=metadata or {},
            # In real life, you'd use a saved payment method ID here.
        )
        print(f"   ✅ Stripe PaymentIntent created: {intent.id}")
        return {"success": True, "payment_intent_id": intent.id, "status": intent.status}
    except stripe.error.StripeError as e:
        print(f"   ❌ Stripe error: {e.user_message}")
        return {"success": False, "error": str(e)}

# --- 2. Send Email (SendGrid) ---
# In app/actions.py, update the send_email function

async def send_email(to_email: str, subject: str, html_content: str, payment_link: str = None):
    if payment_link and "{{PAYMENT_LINK}}" in html_content:
        html_content = html_content.replace("{{PAYMENT_LINK}}", payment_link)
    
    print(f"📧 [ACTION] Sending email to {to_email}")
    if DRY_RUN:
        print(f"   🏜️ DRY RUN: Would send email (Link: {payment_link})")
        return {"success": True, "dry_run": True}
    
    if not sendgrid_client:
        return {"success": False, "error": "SendGrid not configured"}
    
    try:
        message = Mail(
            from_email='sudharaju6143@gmail.com',  
            to_emails=to_email,
            subject=subject,
            html_content=html_content
        )
        # Optional: Set sandbox mode (emails don't actually send, but you see them in your dashboard)
        # message.mail_settings = {"sandbox_mode": {"enable": True}}
        
        response = sendgrid_client.send(message)
        print(f"   ✅ Email sent. Status: {response.status_code}")
        return {"success": response.status_code in [200, 202]}
    except Exception as e:
        print(f"   ❌ Email error: {e}")
        return {"success": False, "error": str(e)}

# --- 3. Send SMS (Twilio) ---
async def send_sms(to_phone: str, message: str):
    """Send an SMS via Twilio."""
    print(f"📱 [ACTION] Sending SMS to {to_phone}")
    
    if DRY_RUN:
        print(f"   🏜️ DRY RUN: Would send SMS to {to_phone}")
        print(f"   Message: {message[:100]}...")
        return {"success": True, "dry_run": True}
    
    if not twilio_client:
        print("   ⚠️ Twilio client not initialized. Skipping.")
        return {"success": False, "error": "Twilio not configured"}
    
    try:
        twilio_phone = os.getenv("TWILIO_PHONE_NUMBER")
        message_obj = twilio_client.messages.create(
            body=message,
            from_=twilio_phone,
            to=to_phone
        )
        print(f"   ✅ SMS sent. SID: {message_obj.sid}")
        return {"success": True, "sid": message_obj.sid}
    except Exception as e:
        print(f"   ❌ SMS error: {e}")
        return {"success": False, "error": str(e)}

# --- 4. Human Handoff (Slack) ---
async def escalate_to_human(customer_id: str, reason: str, case_id: int):
    """Notify a human via Slack to take over the case."""
    print(f"👨‍💼 [ACTION] Escalating customer {customer_id} to human.")
    
    message = (
        f"🚨 *Revenue Recovery Escalation*\n"
        f"*Customer:* {customer_id}\n"
        f"*Case ID:* {case_id}\n"
        f"*Reason:* {reason}\n"
        f"*Action:* Please review and contact the customer."
    )
    
    if DRY_RUN:
        print(f"   🏜️ DRY RUN: Would send Slack message to human team.")
        print(f"   Message: {message}")
        return {"success": True, "dry_run": True}
    
    if not slack_client:
        print("   ⚠️ Slack client not initialized. Skipping.")
        return {"success": False, "error": "Slack not configured"}
    
    try:
        response = slack_client.send(text=message)
        print(f"   ✅ Slack notification sent. Status: {response.status_code}")
        return {"success": response.status_code == 200}
    except Exception as e:
        print(f"   ❌ Slack error: {e}")
        return {"success": False, "error": str(e)}

# --- Helper: Get Customer Contact Info from DB ---
async def get_customer_contact(customer_id: str):
    """Fetch email and phone from the customers table."""
    from app.db import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT email, phone FROM customers WHERE customer_id = $1",
            customer_id
        )
        if row:
            return {"email": row['email'], "phone": row['phone']}
    return {"email": None, "phone": None}