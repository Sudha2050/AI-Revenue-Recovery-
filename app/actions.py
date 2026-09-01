# app/actions.py
import os
import json
from dotenv import load_dotenv

load_dotenv()
DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"

# Slack webhook (optional)
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL")

async def send_email(to: str, subject: str, body: str):
    """Simulate email sending (or use SendGrid/Twilio in production)."""
    print(f"📧 [ACTION] Email to {to}: {subject}")
    if DRY_RUN:
        print("   🏜️ DRY RUN: would send email.")
    else:
        print("   ✅ Email sent (simulated).")
    return {"success": True}

async def send_whatsapp(to_phone: str, message: str):
    """Send WhatsApp message (simulated or via Twilio / Meta WhatsApp Business API)."""
    print(f"💬 [ACTION] WhatsApp to {to_phone}: {message[:100]}...")
    if DRY_RUN:
        print("   🏜️ DRY RUN: would send WhatsApp message.")
    else:
        print("   ✅ WhatsApp message sent (simulated).")
    return {"success": True}

async def send_slack_alert(message: str):
    """Send alert to Slack (RM handoff queue)."""
    print(f"👨💼 [ACTION] Slack alert: {message[:100]}...")
    if SLACK_WEBHOOK_URL and not DRY_RUN:
        # In production, use slack_sdk
        print("   ✅ Slack alert sent (simulated).")
    else:
        print("   🏜️ DRY RUN: would send Slack alert.")
    return {"success": True}

async def generate_plan_document(company: str, amount: float, installments: int):
    """Generate an installment plan document (simulated)."""
    print(f"📄 [ACTION] Generating plan document for {company}: ₹{amount} in {installments} installments.")
    return {"plan_id": f"PLAN-{company[:4]}-{installments}", "document": "draft_plan.pdf"}

async def escalate_to_rm(company_id: str, root_cause: str, reasoning: str):
    """Escalate to Relationship Manager via Slack/CRM."""
    message = f"🚨 RM Escalation\nCompany: {company_id}\nRoot Cause: {root_cause}\nReasoning: {reasoning}"
    await send_slack_alert(message)
    return {"status": "escalated"}

async def get_company_contact(company_id: str):
    """Fetch contact info from companies table."""
    from app.db import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT ap_contact FROM companies WHERE company_id = $1",
            company_id
        )
        if row:
            return row['ap_contact'] if isinstance(row['ap_contact'], dict) else json.loads(row['ap_contact'])
    return {"email": None, "phone": None}