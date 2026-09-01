# app/orchestrator.py
import json
from datetime import datetime, timedelta
from app.db import get_pool
from app.policy_engine import apply_compliance_policy
from app.actions import send_email, escalate_to_rm, generate_plan_document, get_company_contact
from app.llm_client import llm_diagnose


# ---------- DIAGNOSIS: pure reasoning, NO policy/compliance decisions here ----------
async def diagnose_root_cause(invoice_data: dict, company_data: dict) -> dict:
    """
    Hybrid diagnosis: Rules first, LLM (Gemini) fallback for complex/mixed signals.

    IMPORTANT: this function only proposes a root cause + a SUGGESTED action.
    It does NOT enforce compliance bounds (contact caps, auto-approve limits,
    hard stops for dispute/default/frozen accounts) -- that is exclusively
    policy_engine.apply_compliance_policy()'s job. Keeping this separation
    means every compliance rule lives in exactly one place and can't drift
    out of sync between two files.

    Returns: {
        "action": "send_email"|"offer_plan"|"rm_handoff",
        "root_cause": "...",
        "reasoning": "...",
        "installments": int (only present when action == "offer_plan"),
        "confidence": float (only present on LLM-fallback diagnoses)
    }
    """
    days_overdue = invoice_data.get('days_overdue', 0)
    failure_code = (invoice_data.get('failure_code') or '').lower()
    balance_trend = company_data.get('balance_trend', 'healthy')

    # --- 1. Rail-specific diagnosis (NEFT/RTGS/UPI/NACH) ---
    rail = invoice_data.get('payment_rail', 'unknown')
    if rail in ['NEFT', 'RTGS', 'UPI', 'NACH']:
        if 'insufficient_funds' in failure_code:
            return {
                "action": "send_email",
                "root_cause": "liquidity_issue",
                "reasoning": f"{rail} failed due to insufficient funds. Check balance trend before next attempt."
            }
        if 'mandate_expired' in failure_code or 'mandate_revoked' in failure_code:
            return {
                "action": "send_email",
                "root_cause": "mandate_expired",
                "reasoning": f"{rail} mandate expired/revoked. Requesting re-authorization from customer."
            }
        if 'account_closed' in failure_code or 'invalid_account' in failure_code:
            return {
                "action": "rm_handoff",
                "root_cause": "process_breakdown",
                "reasoning": f"{rail} failed due to account issue. Needs human verification, not automated retry."
            }

    # --- 2. Balance trend + overdue window (liquidity signal) ---
    if balance_trend == 'declining' and days_overdue > 30:
        return {
            "action": "offer_plan",
            "root_cause": "liquidity_issue",
            "reasoning": f"Declining balance trend + {days_overdue} days overdue. Suggesting a 2-installment plan.",
            "installments": 2
        }
    if balance_trend == 'critical' and days_overdue > 60:
        return {
            "action": "rm_handoff",
            "root_cause": "liquidity_issue",
            "reasoning": f"Critical balance trend + {days_overdue} days overdue. Too severe for automated handling."
        }

    # --- 3. Chronic late payer (history pattern) ---
    payment_history = company_data.get('payment_history') or []
    if isinstance(payment_history, str):
        try:
            payment_history = json.loads(payment_history)
        except Exception:
            payment_history = []
    late_count = sum(1 for p in payment_history if isinstance(p, dict) and p.get('status') == 'late')
    if late_count >= 3 and days_overdue > 15:
        return {
            "action": "send_email",
            "root_cause": "chronic_late",
            "reasoning": f"Chronic late payer ({late_count} late payments on record). Escalated-tone reminder, RM CC'd."
        }

    # --- 4. LLM fallback (complex / mixed signals rules can't cleanly classify) ---
    print(f"Calling Gemini for B2B diagnosis: {invoice_data.get('invoice_id')}")
    return await llm_diagnose(invoice_data, company_data)


# ---------- MAIN ORCHESTRATOR ----------
async def process_event(event_id: str):
    """
    Full 6-step pipeline:
    Detect -> Fetch Context -> Diagnose -> Decide (policy) -> Execute -> Audit
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            # --- 1. DETECT: fetch + lock the event so concurrent workers can't double-process it ---
            event = await conn.fetchrow(
                """
                SELECT * FROM raw_events
                WHERE event_id = $1 AND is_processed = FALSE
                FOR UPDATE SKIP LOCKED
                """,
                event_id
            )
            if not event:
                print(f"Event {event_id} already processed, locked by another worker, or not found.")
                return

            payload = event['payload'] if isinstance(event['payload'], dict) else json.loads(event['payload'])
            invoice_id = payload.get('invoice_id')
            company_id = payload.get('company_id')

            print(f"Processing Event: {event_id} | Invoice: {invoice_id}")

            # --- 2. FETCH CONTEXT: invoice + company ---
            invoice_data = await conn.fetchrow(
                "SELECT * FROM invoices WHERE invoice_id = $1", invoice_id
            )

            # Defensive fallback: if the event payload didn't carry company_id,
            # look it up from the invoice itself.
            if not company_id and invoice_data:
                company_id = invoice_data['company_id']

            company_data = None
            if company_id:
                company_data = await conn.fetchrow(
                    "SELECT * FROM companies WHERE company_id = $1", company_id
                )

            if not invoice_data or not company_data:
                print(f"Missing invoice or company data for event {event_id} "
                      f"(invoice_id={invoice_id}, company_id={company_id}).")
                await conn.execute(
                    "UPDATE raw_events SET is_processed = TRUE WHERE event_id = $1",
                    event_id
                )
                return

            invoice_dict = dict(invoice_data)
            company_dict = dict(company_data)

            # --- 3. DIAGNOSE (pure reasoning, no compliance logic) ---
            diagnosis = await diagnose_root_cause(invoice_dict, company_dict)
            print(f"Diagnosis: {diagnosis['reasoning']} -> suggested action: {diagnosis['action']}")

            # --- 4. DECIDE (compliance policy engine -- the ONLY place hard stops/caps live) ---
            case_state = {
                'current_contact_attempt': invoice_dict.get('contact_attempts', 0),
                'contact_timestamps': invoice_dict.get('contact_timestamps', []),
                'days_overdue': invoice_dict.get('days_overdue', 0),
                'payment_rail': invoice_dict.get('payment_rail', ''),
                'current_retry_count': invoice_dict.get('retry_count', 0),
            }
            decision = apply_compliance_policy(case_state, diagnosis, company_dict)

            if decision.get('overridden'):
                print(f"Policy OVERRODE diagnosis: '{decision.get('diagnosis_action')}' -> "
                      f"'{decision['action']}'. Reason: {decision['reasoning']}")
            else:
                print(f"Final Decision (Policy): {decision['action']}")

            # --- 5. EXECUTE ---
            action = decision['action']
            root_cause = decision.get('root_cause', 'unknown')
            reasoning = decision.get('reasoning', 'No reasoning provided.')
            new_status = "processing"
            last_action = ""
            schedule_next = None

            contact = company_dict.get('ap_contact', {})
            if isinstance(contact, str):
                contact = json.loads(contact)

            if action == 'halt':
                new_status = 'halted'
                last_action = f"Halted. Reason: {reasoning}"
                await escalate_to_rm(
                    company_id, root_cause,
                    f"[Informational -- automation halted] {reasoning}"
                )
                await conn.execute(
                    "UPDATE invoices SET recovery_status = 'halted', updated_at = NOW() WHERE invoice_id = $1",
                    invoice_id
                )

            elif action == 'rm_handoff':
                new_status = 'escalated'
                last_action = f"Escalated to RM. Reason: {reasoning}"
                await escalate_to_rm(company_id, root_cause, reasoning)
                await conn.execute(
                    "UPDATE invoices SET recovery_status = 'escalated', updated_at = NOW() WHERE invoice_id = $1",
                    invoice_id
                )

            elif action == 'send_email':
                new_status = 'reminding'
                subject = f"Reminder: Invoice {invoice_id} is overdue"
                body = (
                    f"Dear AP Team,\n\nInvoice {invoice_id} for Rs.{invoice_dict['amount']} is overdue by "
                    f"{invoice_dict['days_overdue']} days. Please arrange payment.\n\nRegards,\nFinance Team"
                )
                if contact and contact.get('email'):
                    await send_email(contact['email'], subject, body)
                    last_action = f"Sent reminder to {contact['email']}"
                    schedule_next = datetime.utcnow() + timedelta(days=5)
                else:
                    last_action = "No email on file. Escalated to RM."
                    await escalate_to_rm(company_id, "no_contact", "No email address found for AP contact.")
                    new_status = 'escalated'

            elif action == 'offer_plan':
                new_status = 'plan_offered'
                installments = decision.get('installments', 2)
                await generate_plan_document(company_dict['name'], invoice_dict['amount'], installments)
                last_action = f"Offered installment plan ({installments} installments)"
                if contact and contact.get('email'):
                    await send_email(
                        contact['email'], "Payment Plan Offer",
                        f"We are offering a {installments}-installment plan for invoice {invoice_id}."
                    )
                schedule_next = datetime.utcnow() + timedelta(days=7)

            else:
                new_status = 'unknown'
                last_action = f"Unknown action: {action}"

            # --- 6. AUDIT: record everything, including whether policy overrode diagnosis ---
            audit_reasoning = reasoning
            if decision.get('overridden'):
                audit_reasoning = (
                    f"[POLICY OVERRIDE] diagnosis suggested '{decision.get('diagnosis_action')}', "
                    f"policy enforced '{action}'. {reasoning}"
                )

            new_contact_timestamp = datetime.utcnow()

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
                invoice_id, company_id, new_status, root_cause,
                invoice_dict['amount'], last_action, audit_reasoning, schedule_next
            )

            if action in ('send_email', 'offer_plan'):
                await conn.execute(
                    """
                    UPDATE invoices
                    SET contact_attempts = COALESCE(contact_attempts, 0) + 1,
                        contact_timestamps = COALESCE(contact_timestamps, '[]'::jsonb) || $2::jsonb,
                        updated_at = NOW()
                    WHERE invoice_id = $1
                    """,
                    invoice_id, json.dumps([new_contact_timestamp.isoformat()])
                )

            await conn.execute(
                "UPDATE raw_events SET is_processed = TRUE WHERE event_id = $1",
                event_id
            )
            print(f"Event {event_id} processed. Status: {new_status}")


# ---------- BACKGROUND WORKERS ----------
async def process_pending_events():
    pool = await get_pool()
    async with pool.acquire() as conn:
        events = await conn.fetch(
            """
            SELECT event_id FROM raw_events
            WHERE is_processed = FALSE
            LIMIT 10
            """
        )
        for event in events:
            await process_event(event['event_id'])


async def process_scheduled_cases():
    """
    Re-enters the pipeline for cases due a follow-up (reminder sent or plan
    offered, and the scheduled time has passed). Fixed to always carry
    company_id in the payload -- previously this was omitted, which caused
    process_event() to silently drop every scheduled follow-up.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        cases = await conn.fetch(
            """
            SELECT c.invoice_id, i.company_id
            FROM cases c
            JOIN invoices i ON i.invoice_id = c.invoice_id
            WHERE c.status IN ('reminding', 'plan_offered')
              AND c.scheduled_next_action_at <= NOW()
              AND c.current_contact_attempt < 2
            """
        )
        for case in cases:
            event_id = f"scheduled_{case['invoice_id']}_{int(datetime.utcnow().timestamp())}"
            await conn.execute(
                """
                INSERT INTO raw_events (event_id, event_type, payload, is_processed)
                VALUES ($1, $2, $3, FALSE)
                ON CONFLICT (event_id) DO NOTHING
                """,
                event_id, "scheduled_followup",
                json.dumps({"invoice_id": case['invoice_id'], "company_id": case['company_id']})
            )