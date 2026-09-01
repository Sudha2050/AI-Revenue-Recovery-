# app/orchestrator.py
import json
from datetime import datetime, timedelta
from app.db import get_pool
from app.policy_engine import apply_compliance_policy
from app.actions import send_email, send_whatsapp, escalate_to_rm, generate_plan_document, get_company_contact, send_slack_alert
from app.llm_client import llm_diagnose, classify_customer_intent


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
                delivered = False
                if contact and contact.get('email'):
                    await send_email(contact['email'], subject, body)
                    last_action = f"Sent reminder to {contact['email']}"
                    delivered = True
                
                # Multi-channel: send WhatsApp alert if phone is present
                if contact and contact.get('phone'):
                    wa_msg = f"Reminder: Invoice {invoice_id} (Rs.{invoice_dict['amount']}) is overdue by {invoice_dict['days_overdue']} days. Reply with your payment date or questions."
                    await send_whatsapp(contact['phone'], wa_msg)
                    last_action += f" & WhatsApp to {contact['phone']}"
                    delivered = True

                if delivered:
                    schedule_next = datetime.utcnow() + timedelta(days=5)
                else:
                    last_action = "No email or phone on file. Escalated to RM."
                    await escalate_to_rm(company_id, "no_contact", "No email or phone found for AP contact.")
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
                if contact and contact.get('phone'):
                    await send_whatsapp(
                        contact['phone'],
                        f"We have offered a {installments}-installment payment plan for invoice {invoice_id}."
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


# ---------- PTP (PROMISE TO PAY) FUNCTIONS ----------
async def extract_ptp_from_message(customer_message: str, invoice_id: str) -> dict:
    """
    Use LLM (or simple regex) to extract:
    - amount
    - due_date (relative or absolute)
    - maybe multiple installments
    """
    import re
    amounts = [float(x.replace(',', '')) for x in re.findall(r'₹?\s*(\d+(?:,\d+)*(?:\.\d+)?)', customer_message) if float(x.replace(',', '')) > 0]
    
    now = datetime.utcnow()
    default_due = (now + timedelta(days=7)).strftime('%Y-%m-%d')
    
    if len(amounts) >= 2:
        installments = [
            {"amount": amounts[0], "due_date": (now + timedelta(days=7)).strftime('%Y-%m-%d')},
            {"amount": amounts[1], "due_date": (now + timedelta(days=14)).strftime('%Y-%m-%d')}
        ]
        reasoning = f"Customer promises {len(amounts)} installments."
    elif len(amounts) == 1:
        installments = [
            {"amount": amounts[0], "due_date": default_due}
        ]
        reasoning = f"Customer promises single payment of ₹{amounts[0]} on {default_due}."
    else:
        pool = await get_pool()
        inv_amount = 50000.0
        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT amount FROM invoices WHERE invoice_id = $1", invoice_id)
            if row and row['amount']:
                inv_amount = float(row['amount'])
        installments = [
            {"amount": inv_amount, "due_date": default_due}
        ]
        reasoning = f"Customer promises full payment of ₹{inv_amount} on {default_due}."

    return {
        "installments": installments,
        "llm_reasoning": reasoning
    }


async def create_ptp(invoice_id: str, company_id: str, installments: list, reasoning: str) -> str:
    pool = await get_pool()
    async with pool.acquire() as conn:
        ptp_id = f"PTP-{invoice_id}-{int(datetime.utcnow().timestamp())}"
        total_amount = sum(float(i['amount']) for i in installments)

        await conn.execute("""
            INSERT INTO ptp_headers (ptp_id, invoice_id, company_id, status, total_promised_amount, llm_reasoning)
            VALUES ($1, $2, $3, 'PENDING_APPROVAL', $4, $5)
        """, ptp_id, invoice_id, company_id, total_amount, reasoning)

        for seq, inst in enumerate(installments, 1):
            due_d = inst['due_date']
            if isinstance(due_d, str):
                try:
                    due_d = datetime.fromisoformat(due_d).date()
                except Exception:
                    due_d = (datetime.utcnow() + timedelta(days=7)).date()
            elif isinstance(due_d, datetime):
                due_d = due_d.date()

            await conn.execute("""
                INSERT INTO ptp_installments (ptp_id, sequence, amount, due_date, status)
                VALUES ($1, $2, $3, $4, 'PENDING')
            """, ptp_id, seq, float(inst['amount']), due_d)

        return ptp_id


async def approve_ptp(ptp_id: str, approved_by: str = "system") -> dict:
    pool = await get_pool()
    async with pool.acquire() as conn:
        ptp = await conn.fetchrow("SELECT * FROM ptp_headers WHERE ptp_id = $1", ptp_id)
        if not ptp:
            return {"status": "error", "message": "PTP not found"}

        installments = await conn.fetch("SELECT * FROM ptp_installments WHERE ptp_id = $1", ptp_id)

        # Policy bounds check: auto-approve if total <= 100,000 and installments <= 2
        if float(ptp['total_promised_amount']) <= 100000 and len(installments) <= 2:
            status = 'ACTIVE'
        else:
            status = 'PENDING_APPROVAL'
            await send_slack_alert(f"PTP {ptp_id} requires human approval: ₹{ptp['total_promised_amount']}")
            await conn.execute("""
                UPDATE ptp_headers SET status = 'PENDING_APPROVAL', updated_at = NOW() WHERE ptp_id = $1
            """, ptp_id)
            return {"status": "pending_human"}

        # Update header
        await conn.execute("""
            UPDATE ptp_headers SET status = $1, approved_by = $2, approved_at = NOW(), updated_at = NOW()
            WHERE ptp_id = $3
        """, status, approved_by, ptp_id)

        # Link active PTP to invoice
        await conn.execute("UPDATE invoices SET active_ptp_id = $1 WHERE invoice_id = $2", ptp_id, ptp['invoice_id'])

        # Schedule monitoring events for each installment
        for inst in installments:
            await conn.execute("""
                INSERT INTO raw_events (event_id, event_type, payload) VALUES ($1, 'ptp_due', $2)
                ON CONFLICT (event_id) DO NOTHING
            """, f"ptp_{ptp_id}_{inst['sequence']}", json.dumps({"ptp_id": ptp_id, "sequence": inst['sequence']}))

        return {"status": "active"}


async def check_payment_received(invoice_id: str, min_amount: float, due_date=None) -> float:
    """
    Checks if a payment has been recorded for this invoice via system/webhooks.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        inv = await conn.fetchrow("SELECT recovery_status, amount FROM invoices WHERE invoice_id = $1", invoice_id)
        if inv and inv['recovery_status'] in ('resolved', 'completed'):
            return float(inv['amount'])

        event = await conn.fetchrow("""
            SELECT payload FROM raw_events 
            WHERE event_type = 'payment_received' 
              AND (payload->>'invoice_id') = $1
        """, invoice_id)
        if event:
            payload = event['payload'] if isinstance(event['payload'], dict) else json.loads(event['payload'])
            return float(payload.get('amount', min_amount))

    return 0.0


async def check_ptp_installment(ptp_id: str, sequence: int):
    pool = await get_pool()
    async with pool.acquire() as conn:
        inst = await conn.fetchrow("""
            SELECT i.*, h.invoice_id, h.company_id 
            FROM ptp_installments i
            JOIN ptp_headers h ON h.ptp_id = i.ptp_id
            WHERE i.ptp_id = $1 AND i.sequence = $2
        """, ptp_id, sequence)
        if not inst or inst['status'] == 'PAID':
            return

        paid_amount = await check_payment_received(inst['invoice_id'], float(inst['amount']), inst['due_date'])

        if paid_amount >= float(inst['amount']):
            # Paid
            await conn.execute("""
                UPDATE ptp_installments SET status = 'PAID', actual_paid_amount = $1, paid_at = NOW(), updated_at = NOW()
                WHERE ptp_id = $2 AND sequence = $3
            """, paid_amount, ptp_id, sequence)
            await conn.execute("""
                UPDATE ptp_headers SET total_received_amount = COALESCE(total_received_amount, 0) + $1, updated_at = NOW()
                WHERE ptp_id = $2
            """, paid_amount, ptp_id)
            await close_ptp_if_complete(ptp_id)
        else:
            today = datetime.utcnow().date()
            inst_due = inst['due_date']
            if isinstance(inst_due, datetime):
                inst_due = inst_due.date()

            if today > inst_due:
                # Missed
                await conn.execute("""
                    UPDATE ptp_installments SET status = 'MISSED', updated_at = NOW()
                    WHERE ptp_id = $1 AND sequence = $2
                """, ptp_id, sequence)
                await handle_ptp_breach(ptp_id, sequence)


async def handle_ptp_breach(ptp_id: str, sequence: int):
    pool = await get_pool()
    async with pool.acquire() as conn:
        inst = await conn.fetchrow("""
            SELECT i.*, h.company_id, h.invoice_id 
            FROM ptp_installments i
            JOIN ptp_headers h ON h.ptp_id = i.ptp_id
            WHERE i.ptp_id = $1 AND i.sequence = $2
        """, ptp_id, sequence)
        if not inst:
            return

        new_reminder_count = (inst['reminder_count'] or 0) + 1
        await conn.execute("""
            UPDATE ptp_installments SET reminder_count = $1, last_reminder_at = NOW(), updated_at = NOW()
            WHERE ptp_id = $2 AND sequence = $3
        """, new_reminder_count, ptp_id, sequence)

        max_reminders = 3
        if new_reminder_count <= max_reminders:
            contact = await get_company_contact(inst['company_id'])
            contact_email = contact.get('email') if contact else None
            if contact_email:
                await send_email(
                    contact_email,
                    f"Payment Reminder for PTP {ptp_id}",
                    f"Dear Customer, you promised ₹{inst['amount']} on {inst['due_date']}. Please make the payment."
                )

            next_check = datetime.utcnow() + timedelta(days=1)
            await conn.execute("""
                INSERT INTO raw_events (event_id, event_type, payload) VALUES ($1, 'ptp_retry', $2)
                ON CONFLICT (event_id) DO NOTHING
            """, f"ptp_retry_{ptp_id}_{sequence}_{int(next_check.timestamp())}",
                json.dumps({"ptp_id": ptp_id, "sequence": sequence, "scheduled_at": next_check.isoformat()}))
        else:
            await conn.execute("""
                UPDATE ptp_headers SET status = 'BROKEN', updated_at = NOW() WHERE ptp_id = $1
            """, ptp_id)
            await conn.execute("""
                UPDATE invoices SET recovery_status = 'escalated', updated_at = NOW() WHERE invoice_id = $1
            """, inst['invoice_id'])
            await conn.execute("""
                UPDATE cases SET status = 'escalated', updated_at = NOW() WHERE invoice_id = $1
            """, inst['invoice_id'])
            await escalate_to_rm(
                inst['company_id'], "ptp_broken",
                f"PTP {ptp_id} installment {sequence} missed after {max_reminders} reminders."
            )


async def close_ptp_if_complete(ptp_id: str):
    pool = await get_pool()
    async with pool.acquire() as conn:
        header = await conn.fetchrow("SELECT * FROM ptp_headers WHERE ptp_id = $1", ptp_id)
        if not header:
            return
        total_promised = float(header['total_promised_amount'] or 0)
        total_received = float(header['total_received_amount'] or 0)
        if total_received >= total_promised:
            await conn.execute("UPDATE ptp_headers SET status = 'COMPLETED', updated_at = NOW() WHERE ptp_id = $1", ptp_id)
            await conn.execute("UPDATE invoices SET recovery_status = 'resolved', updated_at = NOW() WHERE invoice_id = $1", header['invoice_id'])
            await conn.execute("UPDATE cases SET status = 'resolved', updated_at = NOW() WHERE invoice_id = $1", header['invoice_id'])


async def handle_payment_received(invoice_id: str, amount: float, payment_reference: str = None) -> dict:
    """
    Executes when an authentic payment receipt webhook triggers.
    Marks invoice resolved, updates case audit, and updates active PTP installment / headers.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        inv = await conn.fetchrow("SELECT * FROM invoices WHERE invoice_id = $1", invoice_id)
        if not inv:
            return {"status": "error", "message": f"Invoice {invoice_id} not found."}

        await conn.execute("UPDATE invoices SET recovery_status = 'resolved', updated_at = NOW() WHERE invoice_id = $1", invoice_id)
        await conn.execute("""
            UPDATE cases SET status = 'resolved',
                             last_action = $2,
                             updated_at = NOW()
            WHERE invoice_id = $1
        """, invoice_id, f"✅ Confirmed payment received: ₹{amount} (Ref: {payment_reference or 'N/A'})")

        active_ptp_id = inv.get('active_ptp_id')
        if active_ptp_id:
            installments = await conn.fetch("""
                SELECT * FROM ptp_installments
                WHERE ptp_id = $1 AND status != 'PAID'
                ORDER BY sequence ASC
            """, active_ptp_id)
            remaining_payment = amount
            for inst in installments:
                inst_amt = float(inst['amount'])
                if remaining_payment >= inst_amt:
                    await conn.execute("""
                        UPDATE ptp_installments SET status = 'PAID', actual_paid_amount = $1, paid_at = NOW(), updated_at = NOW()
                        WHERE ptp_id = $2 AND sequence = $3
                    """, inst_amt, active_ptp_id, inst['sequence'])
                    remaining_payment -= inst_amt

            await conn.execute("""
                UPDATE ptp_headers SET total_received_amount = COALESCE(total_received_amount, 0) + $1, updated_at = NOW()
                WHERE ptp_id = $2
            """, amount, active_ptp_id)

            await close_ptp_if_complete(active_ptp_id)

        print(f"💰 Confirmed payment received for {invoice_id}: ₹{amount}")
        return {"status": "success", "invoice_id": invoice_id, "amount": amount}


# ---------- INBOUND CUSTOMER INTENT PROCESSING ----------
async def process_customer_response(invoice_id: str, message_text: str, channel: str = "email") -> dict:
    """
    NLP Intent Classifier & Inbound Response Router:
    Processes customer replies (Email/WhatsApp/SMS) and routes to Pay Now, PTP Tracker, Dispute, or Inquiry.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        invoice_data = await conn.fetchrow("SELECT * FROM invoices WHERE invoice_id = $1", invoice_id)
        if not invoice_data:
            return {"status": "error", "message": f"Invoice {invoice_id} not found."}

        company_id = invoice_data['company_id']
        company_data = await conn.fetchrow("SELECT * FROM companies WHERE company_id = $1", company_id)

        context = {
            "invoice_id": invoice_id,
            "amount": float(invoice_data['amount']),
            "company_name": company_data['name'] if company_data else company_id
        }

        # 1. AI/NLP Intent Classification
        nlp_result = await classify_customer_intent(message_text, context)
        intent = nlp_result.get("intent", "general_inquiry")
        reasoning = nlp_result.get("reasoning", "")
        suggested_reply = nlp_result.get("suggested_reply", "")

        new_status = "processing"
        last_action = ""
        promised_date = None
        schedule_next = None

        if intent == "dispute":
            new_status = "halted"
            last_action = f"🛑 Inbound dispute raised: '{message_text[:80]}'. Automation halted."
            await conn.execute("UPDATE companies SET dispute_flag = TRUE WHERE company_id = $1", company_id)
            await conn.execute("UPDATE invoices SET recovery_status = 'halted', updated_at = NOW() WHERE invoice_id = $1", invoice_id)
            await escalate_to_rm(company_id, "customer_dispute", f"Customer raised dispute via {channel}: '{message_text}'")

        elif intent == "promise_to_pay":
            # Wire PTP tracker (Fix Bug 2)
            ptp_data = await extract_ptp_from_message(message_text, invoice_id)
            installments = ptp_data.get("installments", [])
            ptp_reasoning = ptp_data.get("llm_reasoning", reasoning)

            ptp_id = await create_ptp(invoice_id, company_id, installments, ptp_reasoning)
            approval_res = await approve_ptp(ptp_id)

            if approval_res.get("status") == "active":
                new_status = "promise_to_pay"
                last_action = f"🤝 PTP {ptp_id} active ({len(installments)} installments)."
            else:
                new_status = "pending_approval"
                last_action = f"⏳ PTP {ptp_id} submitted; pending human approval."

            if installments:
                first_due = installments[0]['due_date']
                try:
                    promised_date = datetime.fromisoformat(first_due) if isinstance(first_due, str) else datetime.combine(first_due, datetime.min.time())
                except Exception:
                    promised_date = datetime.utcnow() + timedelta(days=7)
            else:
                promised_date = datetime.utcnow() + timedelta(days=7)

            schedule_next = promised_date
            await conn.execute("UPDATE invoices SET recovery_status = $1, updated_at = NOW() WHERE invoice_id = $2", new_status, invoice_id)

        elif intent == "pay_now":
            # Fix Bug 3: Do NOT resolve on customer word -- set payment_claimed awaiting webhook confirmation
            new_status = "payment_claimed"
            last_action = "💳 Customer claimed payment / requested checkout link. Awaiting confirmed payment webhook."
            if not suggested_reply or "http" not in suggested_reply:
                suggested_reply += f"\n\nPlease complete your payment via secure portal: https://pay.company.com/invoice/{invoice_id}"
            await conn.execute("UPDATE invoices SET recovery_status = 'payment_claimed', updated_at = NOW() WHERE invoice_id = $1", invoice_id)

        else: # general_inquiry
            new_status = "inquiry_received"
            last_action = f"💬 Customer inquiry received via {channel}."
            await escalate_to_rm(company_id, "customer_inquiry", f"Inquiry from customer: '{message_text}'")

        # 2. Update cases audit record with customer intent & response
        await conn.execute(
            """
            INSERT INTO cases (invoice_id, company_id, status, root_cause, amount, last_action,
                               llm_reasoning, customer_intent, customer_response, promised_date,
                               scheduled_next_action_at, updated_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, NOW())
            ON CONFLICT (invoice_id) DO UPDATE SET
                status = EXCLUDED.status,
                last_action = EXCLUDED.last_action,
                llm_reasoning = EXCLUDED.llm_reasoning,
                customer_intent = EXCLUDED.customer_intent,
                customer_response = EXCLUDED.customer_response,
                promised_date = EXCLUDED.promised_date,
                scheduled_next_action_at = EXCLUDED.scheduled_next_action_at,
                updated_at = NOW()
            """,
            invoice_id, company_id, new_status, intent, invoice_data['amount'],
            last_action, reasoning, intent, message_text, promised_date, schedule_next
        )

        # 3. Send automated acknowledgment reply
        contact = (company_data.get('ap_contact') if company_data else {}) or {}
        if isinstance(contact, str):
            try:
                contact = json.loads(contact)
            except Exception:
                contact = {}
        if not isinstance(contact, dict):
            contact = {}

        if channel == "whatsapp" and contact.get('phone'):
            await send_whatsapp(contact['phone'], suggested_reply)
        elif contact.get('email'):
            await send_email(contact['email'], f"RE: Invoice {invoice_id} Update", suggested_reply)

        print(f"📩 Processed customer response for {invoice_id} -> Intent: {intent} (Status: {new_status})")
        return {
            "invoice_id": invoice_id,
            "intent": intent,
            "status": new_status,
            "promised_date": promised_date.isoformat() if promised_date else None,
            "suggested_reply": suggested_reply
        }


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
    Re-enters pipeline for cases due follow-up.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        cases = await conn.fetch(
            """
            SELECT c.invoice_id, i.company_id, c.status
            FROM cases c
            JOIN invoices i ON i.invoice_id = c.invoice_id
            WHERE c.status IN ('reminding', 'plan_offered', 'promise_to_pay', 'pending_approval')
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


async def process_due_ptp_installments():
    pool = await get_pool()
    async with pool.acquire() as conn:
        installments = await conn.fetch("""
            SELECT ptp_id, sequence FROM ptp_installments 
            WHERE due_date <= CURRENT_DATE AND status IN ('PENDING', 'MISSED')
        """)
        for inst in installments:
            await check_ptp_installment(inst['ptp_id'], inst['sequence'])