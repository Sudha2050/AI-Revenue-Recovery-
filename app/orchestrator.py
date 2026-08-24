# app/orchestrator.py
"""
Revenue Recovery Agent - Orchestrator Core
Handles the full workflow: Detect → Diagnose → Decide → Execute → Audit
Uses Rules-First + Gemini LLM fallback for intelligent diagnosis.
"""

import json
import asyncio
from datetime import datetime, timedelta
from app.db import get_pool
from app.actions import (
    retry_payment,
    send_email,
    send_sms,
    escalate_to_human,
    get_customer_contact
)
from app.llm_client import llm_diagnose  # Gemini API wrapper


# ============================================================
# STEP 0: CONTEXT FETCHER (CRM + Historical Failures)
# ============================================================

async def get_customer_context(customer_id: str):
    """
    Fetches rich customer context from the database:
    - LTV (Lifetime Value)
    - Segment (high_ltv, standard, trial)
    - Recent failure history (last 3 months)
    - Is this a repeat offender?
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        # 1. Fetch CRM data (LTV, segment, plan)
        crm_row = await conn.fetchrow(
            "SELECT crm_data FROM customers WHERE customer_id = $1",
            customer_id
        )
        
        crm_data = {}
        if crm_row and crm_row['crm_data']:
            crm_data = crm_row['crm_data'] if isinstance(crm_row['crm_data'], dict) else json.loads(crm_row['crm_data'])
        
        # 2. Fetch failure history (last 3 months)
        history = await conn.fetch("""
            SELECT status, amount_usd, created_at 
            FROM cases 
            WHERE customer_id = $1 
              AND created_at > NOW() - INTERVAL '3 months'
            ORDER BY created_at DESC
        """, customer_id)
        
        total_attempts = len(history)
        failed_attempts = sum(1 for h in history if h['status'] in ['escalated', 'lost', 'retrying', 'awaiting_input'])
        resolved_attempts = sum(1 for h in history if h['status'] == 'resolved')
        
        # 3. Determine segment (fallback if CRM data missing)
        ltv = crm_data.get('ltv', 0)
        segment = crm_data.get('segment', 'standard')
        plan = crm_data.get('plan', 'monthly')
        
        # Auto-detect high LTV if not explicitly set
        if ltv > 5000:  # > ₹4,00,000 INR lifetime value
            segment = 'high_ltv'
        
        return {
            "customer_id": customer_id,
            "ltv": ltv,
            "segment": segment,          # high_ltv, standard, trial
            "plan": plan,                # monthly, annual, free_trial
            "total_attempts": total_attempts,
            "failed_attempts": failed_attempts,
            "resolved_attempts": resolved_attempts,
            "is_repeat_offender": failed_attempts >= 2,
            "is_first_failure": failed_attempts == 0,
            "recent_failure_amounts": [h['amount_usd'] for h in history[:3]]
        }


# ============================================================
# STEP 1: DIAGNOSIS ENGINE (Rules + LLM with Context)
# ============================================================

async def diagnose_root_case(
    error_code: str, 
    error_message: str, 
    amount: float, 
    event_type: str,
    context: dict   # <-- NEW PARAMETER
):
    """
    Hybrid diagnosis using Rules + Gemini, now enriched with Customer Context.
    """
    
    # --- 1. DETERMINISTIC RULES (Now with Context!) ---
    
    # Case A: Insufficient funds + First failure + High-LTV
    if error_code == "insufficient_funds" and context.get('is_first_failure') and context.get('segment') == 'high_ltv':
        return {
            "action": "retry_payment",
            "delay_hours": 72,  # Payday-aligned
            "reasoning": f"First-time failure for high-LTV customer (LTV: ${context['ltv']}). Retrying in 3 days. Sending a friendly heads-up email, no discount needed."
        }
    
    # Case B: Insufficient funds + Repeat offender (2+ failures)
    elif error_code == "insufficient_funds" and context.get('is_repeat_offender'):
        return {
            "action": "send_email",
            "delay_hours": 0,
            "template": "switch_billing_date",
            "reasoning": f"Repeat insufficient-funds failure (attempt {context['failed_attempts']}). Offering to switch billing date or update payment method instead of blind retry."
        }
    
    # Case C: Expired card + High-LTV -> Immediate white-glove email
    elif error_code in ["card_expired", "expired_card"] and context.get('segment') == 'high_ltv':
        return {
            "action": "send_email",
            "delay_hours": 0,
            "template": "urgent_update_card",
            "reasoning": f"High-LTV customer ({context['segment']}) has an expired card. Sending priority email with a concierge link."
        }
    
    # Case D: Fraud risk -> Always escalate, regardless of context
    elif error_code == "suspected_fraud":
        return {
            "action": "human_handoff",
            "delay_hours": 0,
            "reasoning": "Fraud risk detected. Escalating immediately regardless of customer value."
        }
    
    # Case E: Trial user + Any failure -> One automated retry, then drop
    elif context.get('plan') == 'free_trial':
        return {
            "action": "retry_payment",
            "delay_hours": 4,
            "reasoning": f"Free trial user. One automated retry in 4 hours. If it fails, no further action to avoid spam."
        }
    
    # --- 2. UNKNOWN ERRORS: CALL GEMINI WITH CONTEXT! ---
    print(f"🧠 [LLM] Calling Gemini API for: {error_code} (Segment: {context['segment']})")
    
    # Build a rich prompt that includes context
    llm_result = await llm_diagnose(
        error_code=error_code,
        error_message=error_message,
        amount=amount,
        event_type=event_type,
        context=context  # <-- Pass the context here!
    )
    
    return llm_result


# ============================================================
# STEP 2: POLICY ENGINE (Guardrails + Bounds with Context)
# ============================================================

def apply_policy(case_data: dict, diagnosis: dict, context: dict) -> dict:
    """
    Enforces caps, retry limits, and opt-out rules.
    Now adapts based on Customer Context.
    """
    
    # 1. Dynamic Max Retries based on Segment
    max_retries = case_data.get('max_retries', 3)
    if context.get('segment') == 'high_ltv':
        max_retries = 5  # High-LTV customers get more chances
    elif context.get('plan') == 'free_trial':
        max_retries = 1  # Trial users get only 1 attempt
    
    # 2. Enforce Max Retries
    if case_data['current_retry_count'] >= max_retries:
        return {
            "action": "human_handoff",
            "reasoning": f"Max retries ({max_retries}) exceeded for {context['segment']} customer. Escalating."
        }
    
    # 3. High-Value Escalation (Override if LTV > $10,000, escalate safely)
    if case_data['amount_usd'] > 1000 and context.get('segment') != 'high_ltv':
        return {
            "action": "human_handoff",
            "reasoning": f"High amount (${case_data['amount_usd']}) with non-high-LTV customer. Escalating to human."
        }
    
    # 4. Pass through the diagnosis
    return diagnosis


# ============================================================
# STEP 3: MAIN ORCHESTRATOR (State Machine)
# ============================================================

async def process_event(event_id: str):
    """
    Processes a single raw event through the full workflow:
    Detect → Diagnose → Decide → Execute → Audit
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        
        # --- 1. DETECT: Get the unprocessed event ---
        event = await conn.fetchrow(
            "SELECT * FROM raw_events WHERE event_id = $1 AND is_processed = FALSE",
            event_id
        )
        if not event:
            print(f"⚠️ Event {event_id} already processed or not found.")
            return
        
        canonical = event['canonical_event']
        if isinstance(canonical, str):
            canonical = json.loads(canonical)
        
        print(f"\n🔄 Processing Event: {event_id} (Type: {canonical['event_type']})")
        
        # --- 2. DIAGNOSE: Check for existing case or create new ---
        existing_case = await conn.fetchrow(
            "SELECT * FROM cases WHERE event_id = $1",
            event_id
        )
        
        if not existing_case:
            await conn.execute(
                """
                INSERT INTO cases (event_id, customer_id, case_type, amount_usd, status, max_retries)
                VALUES ($1, $2, $3, $4, 'diagnosing', 3)
                """,
                event_id,
                canonical['customer_id'],
                canonical['event_type'],
                canonical.get('amount_usd', 0)
            )
            print("📂 New case created.")
        
        # Get the current case state
        case = await conn.fetchrow(
            "SELECT * FROM cases WHERE event_id = $1",
            event_id
        )
        
        # 2.5: Fetch Customer Context (NEW!)
        customer_context = await get_customer_context(case['customer_id'])
        print(f"👤 Customer Context: Segment={customer_context['segment']}, LTV=${customer_context['ltv']}, Failures={customer_context['failed_attempts']}")

        # --- 3. DIAGNOSE (Deep): Rules + LLM with Context ---
        diagnosis = await diagnose_root_case(
            error_code=canonical.get('raw_error_code'),
            error_message=canonical.get('raw_error_message'),
            amount=case['amount_usd'],
            event_type=case['case_type'],
            context=customer_context  # <-- Pass the context!
        )
        print(f"🧠 Diagnosis: {diagnosis['reasoning']} -> Action: {diagnosis['action']}")
        
        # --- 4. DECIDE + GUARDRAILS (Policy Engine) ---
        decision = apply_policy(dict(case), diagnosis, customer_context)
        print(f"🛡️ Final Decision after policy: {decision['action']}")
        
        # --- 5. EXECUTE (Real Actions) ---
        result = None
        new_status = "processing"
        last_action = ""
        schedule_next = None
        retry_count_increment = 0
        payment_link_url = None
        
        if decision['action'] == 'retry_payment':
            # Get customer contact info
            contact = await get_customer_contact(case['customer_id'])
            
            # Call Stripe/Razorpay to generate a payment link
            result = await retry_payment(
                customer_id=case['customer_id'],
                amount_usd=float(case['amount_usd']),
                currency="usd",
                email=contact.get('email'),
                case_id=case['case_id'],
                description=f"Revenue Recovery for Case #{case['case_id']}"
            )
            
            if result.get('success') and result.get('payment_link'):
                payment_link_url = result['payment_link']
                print(f"   🔗 Payment Link generated: {payment_link_url}")
                
                # Send Email with the payment link
                if contact.get('email'):
                    html = f"""
                    <p>Dear Customer,</p>
                    <p>Your recent payment of ${case['amount_usd']} failed. 
                    Please click the secure link below to complete your payment:</p>
                    <p><a href='{payment_link_url}'>{payment_link_url}</a></p>
                    <p>This link is valid for 24 hours.</p>
                    <p>Thank you,<br>Revenue Team</p>
                    """
                    email_result = await send_email(
                        contact['email'],
                        "Complete your secure payment",
                        html,
                        payment_link_url
                    )
                    last_action = f"Sent payment link to {contact['email']}"
                
                # Send SMS with the payment link (Hinglish)
                if contact.get('phone'):
                    sms_msg = f"Dear Customer, payment of ${case['amount_usd']} failed. Pay securely here: {payment_link_url}"
                    sms_result = await send_sms(contact['phone'], sms_msg)
                    last_action += f" and SMS to {contact['phone']}"
                
                new_status = 'awaiting_input'
                # Schedule a follow-up check in 24 hours
                schedule_next = datetime.utcnow() + timedelta(hours=24)
                
            else:
                # Payment link generation failed
                new_status = 'escalated'
                last_action = f"Payment link generation failed: {result.get('error', 'Unknown error')}"
                decision['action'] = 'human_handoff'
                retry_count_increment = 1
        
        elif decision['action'] == 'send_email':
            contact = await get_customer_contact(case['customer_id'])
            if contact.get('email'):
                html = f"""
                <p>Dear Customer,</p>
                <p>Your payment of ${case['amount_usd']} failed.</p>
                <p>Please update your payment method by clicking the link below:</p>
                <p><a href='https://yourapp.com/update-payment'>Update Payment Method</a></p>
                <p>Thank you,<br>Revenue Team</p>
                """
                result = await send_email(contact['email'], "Payment Action Required", html)
                last_action = f"Sent email to {contact['email']}"
                new_status = 'awaiting_input'
                schedule_next = datetime.utcnow() + timedelta(hours=48)
            else:
                # No email on file -> escalate
                decision['action'] = 'human_handoff'
        
        if decision['action'] == 'send_sms':
            contact = await get_customer_contact(case['customer_id'])
            if contact.get('phone'):
                msg = f"Hi, your payment of ${case['amount_usd']} failed. Please contact support or update your payment method."
                result = await send_sms(contact['phone'], msg)
                last_action = f"Sent SMS to {contact['phone']}"
                new_status = 'awaiting_input'
                schedule_next = datetime.utcnow() + timedelta(hours=24)
            else:
                # No phone -> escalate
                decision['action'] = 'human_handoff'
        
        if decision['action'] == 'human_handoff':
            result = await escalate_to_human(
                customer_id=case['customer_id'],
                reason=decision.get('reasoning', 'LLM requested human review.'),
                case_id=case['case_id']
            )
            last_action = "Escalated to human team via Slack."
            new_status = 'escalated'
        
        # --- 6. AUDIT: Update case state ---
        await conn.execute(
            """
            UPDATE cases 
            SET status = $1, 
                last_action = $2, 
                scheduled_next_action_at = $3,
                current_retry_count = current_retry_count + $4,
                llm_reasoning = $5,
                updated_at = NOW()
            WHERE event_id = $6
            """,
            new_status,
            last_action,
            schedule_next,
            retry_count_increment,
            decision.get('reasoning', ''),
            event_id
        )
        
        # --- 7. Mark raw event as processed ---
        await conn.execute(
            "UPDATE raw_events SET is_processed = TRUE WHERE event_id = $1",
            event_id
        )
        
        print(f"✅ Event {event_id} processed. Status: {new_status}\n")


# ============================================================
# STEP 4: BACKGROUND WORKERS
# ============================================================

async def process_pending_events():
    """
    Background worker: Finds unprocessed raw events and triggers the orchestrator.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        events = await conn.fetch(
            "SELECT event_id FROM raw_events WHERE is_processed = FALSE LIMIT 10"
        )
        for event in events:
            await process_event(event['event_id'])


async def process_scheduled_cases():
    """
    Background worker: Finds cases that are due for retry and re-processes them.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        cases = await conn.fetch(
            """
            SELECT event_id FROM cases 
            WHERE status = 'retrying' 
            AND scheduled_next_action_at <= NOW()
            LIMIT 10
            """
        )
        for case in cases:
            # Mark the raw event as unprocessed so process_event picks it up again
            await conn.execute(
                "UPDATE raw_events SET is_processed = FALSE WHERE event_id = $1",
                case['event_id']
            )
            await process_event(case['event_id'])