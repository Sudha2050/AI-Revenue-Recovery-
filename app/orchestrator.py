# app/orchestrator.py
import json
from datetime import datetime, timedelta
from app.db import get_pool
from app.policy_engine import apply_compliance_policy
from app.actions import send_email, escalate_to_rm, generate_plan_document, get_company_contact
from app.llm_client import llm_diagnose

# ---------- DIAGNOSIS: Rules + LLM ----------
async def diagnose_root_cause(invoice_data: dict, company_data: dict) -> dict:
    """
    Hybrid diagnosis: Rules first, LLM (Gemini) fallback for complex cases.
    Returns: {"action": "send_email"|"offer_plan"|"rm_handoff"|"halt", "root_cause": "...", "reasoning": "..."}
    """
    days_overdue = invoice_data.get('days_overdue', 0)
    failure_code = invoice_data.get('failure_code', '')
    balance_trend = company_data.get('balance_trend', 'healthy')
    dispute_flag = company_data.get('dispute_flag', False)
    mandate_revoked = company_data.get('mandate_revoked', False)
    account_frozen = company_data.get('account_frozen', False)

    # --- 1. Hard Stops (Dispute, Frozen, Default) ---
    if dispute_flag:
        return {"action": "halt", "root_cause": "dispute",
                "reasoning": "Open dispute. 0 automated contact. Escalate to RM."}
    if account_frozen:
        return {"action": "rm_handoff", "root_cause": "account_frozen",
                "reasoning": "Account frozen. Mandatory RM review."}
    if company_data.get('willful_default'):
        return {"action": "halt", "root_cause": "willful_default",
                "reasoning": "Willful default risk. Halt all automated actions."}

    # --- 2. Rail-Specific Diagnosis (NEFT/RTGS/UPI/NACH) ---
    rail = invoice_data.get('payment_rail', 'unknown')
    if rail in ['NEFT', 'RTGS', 'UPI', 'NACH']:
        if 'insufficient_funds' in failure_code.lower():
            return {"action": "send_email", "root_cause": "liquidity_issue",
                    "reasoning": f"{rail} failed due to insufficient funds. Check balance trend."}
        if 'mandate_expired' in failure_code.lower() or 'mandate_revoked' in failure_code.lower():
            return {"action": "send_email", "root_cause": "mandate_expired",
                    "reasoning": f"{rail} mandate expired/revoked. Request re-authorization."}
        if 'account_closed' in failure_code.lower() or 'invalid_account' in failure_code.lower():
            return {"action": "rm_handoff", "root_cause": "process_breakdown",
                    "reasoning": f"{rail} failed due to account issue. Escalate to RM for verification."}

    # --- 3. Balance Trend + Overdue (Liquidity vs Process) ---
    if balance_trend == 'declining' and days_overdue > 30:
        return {"action": "offer_plan", "root_cause": "liquidity_issue",
                "reasoning": f"Declining balance trend + overdue {days_overdue} days. Offer plan (≤2 installments)."}
    if balance_trend == 'critical' and days_overdue > 60:
        return {"action": "rm_handoff", "root_cause": "liquidity_issue",
                "reasoning": f"Critical balance trend + overdue {days_overdue} days. Mandatory RM review."}

    # --- 4. Chronic Late Payer (History) ---
    payment_history = company_data.get('payment_history', [])
    late_count = sum(1 for p in payment_history if p.get('status') == 'late')
    if late_count >= 3 and days_overdue > 15:
        return {"action": "send_email", "root_cause": "chronic_late",
                "reasoning": f"Chronic late payer ({late_count} late payments). Escalated reminders with RM CC'd."}

    # --- 5. LLM Fallback (Complex / Mixed Signals) ---
    print(f"🧠 [LLM] Calling Gemini for B2B diagnosis: {invoice_data.get('invoice_id')}")
    return await llm_diagnose(invoice_data, company_data)


# ---------- MAIN ORCHESTRATOR ----------
async def process_event(event_id: str):
    """
    Full 6-step pipeline:
    Detect -> Fetch Context -> Diagnose -> Decide -> Execute -> Audit
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        # --- 1. DETECT: Fetch event ---
        event = await conn.fetchrow(
            "SELECT * FROM raw_events WHERE event_id = $1 AND is_processed = FALSE",
            event_id
        )
        if not event:
            print(f"⚠️ Event {event_id} already processed or not found.")
            return

        payload = event['payload'] if isinstance(event['payload'], dict) else json.loads(event['payload'])
        invoice_id = payload.get('invoice_id')
        company_id = payload.get('company_id')

        print(f"\n🔄 Processing Event: {event_id} | Invoice: {invoice_id}")

        # --- 2. FETCH CONTEXT: Invoice + Company ---
        invoice_data = await conn.fetchrow(
            "SELECT * FROM invoices WHERE invoice_id = $1", invoice_id
        )
        company_data = await conn.fetchrow(
            "SELECT * FROM companies WHERE company_id = $1", company_id
        )
        if not invoice_data or not company_data:
            print("❌ Missing invoice or company data.")
            return

        invoice_dict = dict(invoice_data)
        company_dict = dict(company_data)

        # --- 3. DIAGNOSE (Rules + LLM) ---
        diagnosis = await diagnose_root_cause(invoice_dict, company_dict)
        print(f"🧠 Diagnosis: {diagnosis['reasoning']} -> Action: {diagnosis['action']}")

        # --- 4. DECIDE (Compliance Policy Engine) ---
        case_state = {
            'current_contact_attempt': invoice_dict.get('contact_attempts', 0),
            'days_overdue': invoice_dict.get('days_overdue', 0),
            'payment_rail': invoice_dict.get('payment_rail', ''),
            'current_retry_count': invoice_dict.get('retry_count', 0)
        }
        decision = apply_compliance_policy(case_state, diagnosis, company_dict)
        print(f"🛡️ Final Decision (Policy): {decision['action']}")

        # --- 5. EXECUTE ---
        action = decision['action']
        root_cause = decision.get('root_cause', 'unknown')
        reasoning = decision.get('reasoning', 'No reasoning provided.')
        new_status = "processing"
        last_action = ""
        schedule_next = None

        # Get AP contact
        contact = company_dict.get('ap_contact', {})
        if isinstance(contact, str):
            contact = json.loads(contact)

        if action == 'halt':
            new_status = 'halted'
            last_action = f"🛑 Halted. Reason: {reasoning}"
            # Update invoice to stop further attempts
            await conn.execute(
                "UPDATE invoices SET recovery_status = 'halted', updated_at = NOW() WHERE invoice_id = $1",
                invoice_id
            )

        elif action == 'rm_handoff':
            new_status = 'escalated'
            last_action = f"👨💼 Escalated to RM. Reason: {reasoning}"
            await escalate_to_rm(company_id, root_cause, reasoning)
            await conn.execute(
                "UPDATE invoices SET recovery_status = 'escalated', updated_at = NOW() WHERE invoice_id = $1",
                invoice_id
            )

        elif action == 'send_email':
            new_status = 'reminding'
            subject = f"Reminder: Invoice {invoice_id} is overdue"
            body = f"Dear AP Team,\n\nInvoice {invoice_id} for ₹{invoice_dict['amount']} is overdue by {invoice_dict['days_overdue']} days. Please arrange payment.\n\nRegards,\nFinance Team"
            if contact and contact.get('email'):
                await send_email(contact['email'], subject, body)
                last_action = f"📧 Sent reminder to {contact['email']}"
            else:
                last_action = "⚠️ No email on file. Escalated to RM."
                await escalate_to_rm(company_id, "no_contact", "No email address found for AP contact.")
                new_status = 'escalated'

            # Schedule next follow-up in 5 days (RBI-friendly gap)
            schedule_next = datetime.utcnow() + timedelta(days=5)

        elif action == 'offer_plan':
            new_status = 'plan_offered'
            installments = decision.get('installments', 2)
            await generate_plan_document(company_dict['name'], invoice_dict['amount'], installments)
            last_action = f"📄 Offered installment plan ({installments} installments)"
            if contact and contact.get('email'):
                await send_email(contact['email'], "Payment Plan Offer", f"We are offering a {installments}-installment plan for invoice {invoice_id}.")
            schedule_next = datetime.utcnow() + timedelta(days=7)  # Give them time to respond

        else:
            new_status = 'unknown'
            last_action = f"Unknown action: {action}"

        # --- 6. AUDIT: Update case and log ---
        await conn.execute(
            """
            INSERT INTO cases (invoice_id, company_id, status, root_cause, amount, last_action, 
                               llm_reasoning, current_contact_attempt, scheduled_next_action_at, updated_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, 1, $8, NOW())
            ON CONFLICT (invoice_id) DO UPDATE SET
                status = EXCLUDED.status,
                root_cause = EXCLUDED.root_cause,
                last_action = EXCLUDED.last_action,
                llm_reasoning = EXCLUDED.llm_reasoning,
                current_contact_attempt = cases.current_contact_attempt + 1,
                scheduled_next_action_at = EXCLUDED.scheduled_next_action_at,
                updated_at = NOW()
            """,
            invoice_id,
            company_id,
            new_status,
            root_cause,
            invoice_dict['amount'],
            last_action,
            reasoning,
            schedule_next
        )

        # Mark raw event as processed
        await conn.execute(
            "UPDATE raw_events SET is_processed = TRUE WHERE event_id = $1",
            event_id
        )
        print(f"✅ Event {event_id} processed. Status: {new_status}\n")


# ---------- BACKGROUND WORKERS ----------
async def process_pending_events():
    pool = await get_pool()
    async with pool.acquire() as conn:
        events = await conn.fetch(
            "SELECT event_id FROM raw_events WHERE is_processed = FALSE LIMIT 10"
        )
        for event in events:
            await process_event(event['event_id'])

async def process_scheduled_cases():
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Find cases that need a follow-up (reminding or plan_offered, and scheduled time passed)
        cases = await conn.fetch("""
            SELECT invoice_id FROM cases 
            WHERE status IN ('reminding', 'plan_offered') 
              AND scheduled_next_action_at <= NOW()
              AND current_contact_attempt < 2  -- RBI cap
        """)
        for case in cases:
            # Create a new event to re-enter the pipeline
            await conn.execute(
                "INSERT INTO raw_events (event_id, event_type, payload) VALUES ($1, $2, $3) ON CONFLICT (event_id) DO NOTHING",
                f"scheduled_{case['invoice_id']}", "scheduled_followup", {"invoice_id": case['invoice_id']}
            )
            # Mark the event for processing
            await conn.execute(
                "UPDATE raw_events SET is_processed = FALSE WHERE event_id = $1",
                f"scheduled_{case['invoice_id']}"
            )