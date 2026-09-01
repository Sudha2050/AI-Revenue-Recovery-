# app/ptp_handlers.py
import json
from datetime import datetime, timedelta, date
from app.db import get_pool
from app.actions import send_email, escalate_to_rm, send_slack_alert

# ---------- CREATE PTP ----------
async def create_ptp(invoice_id: str, company_id: str, installments: list, reasoning: str = "Customer promise") -> str:
    """
    Creates a PTP header and installments.
    installments: [{"amount": 50000, "due_date": "2026-09-15"}, ...]
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        ptp_id = f"PTP-{invoice_id}-{int(datetime.utcnow().timestamp())}"
        total_amount = sum(i['amount'] for i in installments)

        # 1. Insert PTP header
        await conn.execute("""
            INSERT INTO ptp_headers (ptp_id, invoice_id, company_id, status, total_promised_amount, llm_reasoning)
            VALUES ($1, $2, $3, 'PENDING_APPROVAL', $4, $5)
        """, ptp_id, invoice_id, company_id, total_amount, reasoning)

        # 2. Insert installments
        for seq, inst in enumerate(installments, 1):
            await conn.execute("""
                INSERT INTO ptp_installments (ptp_id, sequence, amount, due_date, status)
                VALUES ($1, $2, $3, $4, 'PENDING')
            """, ptp_id, seq, inst['amount'], inst['due_date'])

        # 3. Update company PTP behavior
        await conn.execute("""
            UPDATE companies SET ptp_behavior = jsonb_set(
                COALESCE(ptp_behavior, '{"total_promises":0, "completed":0, "broken":0, "avg_delay_days":0, "on_time_rate":0}'::jsonb),
                '{total_promises}',
                ((COALESCE(ptp_behavior->>'total_promises', '0')::int) + 1)::text::jsonb
            ) WHERE company_id = $1
        """, company_id)

        # 4. Trigger approval logic
        await auto_or_human_approve(ptp_id)

        return ptp_id


# ---------- APPROVE PTP ----------
async def auto_or_human_approve(ptp_id: str):
    """
    Auto-approve if total ≤ ₹1,00,000 and installments ≤ 2.
    Else, send Slack alert for human approval.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        ptp = await conn.fetchrow("SELECT * FROM ptp_headers WHERE ptp_id = $1", ptp_id)
        installments = await conn.fetch("SELECT * FROM ptp_installments WHERE ptp_id = $1", ptp_id)

        if ptp['total_promised_amount'] <= 100000 and len(installments) <= 2:
            # Auto-approve
            await conn.execute("""
                UPDATE ptp_headers SET status = 'ACTIVE', approved_by = 'system', approved_at = NOW(), updated_at = NOW()
                WHERE ptp_id = $1
            """, ptp_id)
            await conn.execute("UPDATE invoices SET active_ptp_id = $1 WHERE invoice_id = $2", ptp_id, ptp['invoice_id'])
            print(f"✅ PTP {ptp_id} auto-approved.")
            # Schedule monitoring
            await schedule_ptp_monitoring(ptp_id)
        else:
            # Needs human approval -> Slack alert
            await send_slack_alert(
                f"🚨 PTP {ptp_id} requires approval.\n"
                f"Company: {ptp['company_id']}\n"
                f"Amount: ₹{ptp['total_promised_amount']}\n"
                f"Installments: {len(installments)}\n"
                f"Reason: {ptp['llm_reasoning']}"
            )
            print(f"⏳ PTP {ptp_id} pending human approval.")


# ---------- SCHEDULE MONITORING ----------
async def schedule_ptp_monitoring(ptp_id: str):
    """
    Creates raw_events for each installment due date.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        installments = await conn.fetch("SELECT * FROM ptp_installments WHERE ptp_id = $1", ptp_id)
        for inst in installments:
            event_id = f"ptp_monitor_{ptp_id}_{inst['sequence']}"
            await conn.execute("""
                INSERT INTO raw_events (event_id, event_type, payload) VALUES ($1, 'ptp_due', $2)
                ON CONFLICT (event_id) DO NOTHING
            """, event_id, json.dumps({"ptp_id": ptp_id, "sequence": inst['sequence']}))


# ---------- MONITOR INSTALLMENT ----------
async def check_ptp_installment(ptp_id: str, sequence: int):
    """
    Checks if payment for this installment has been received.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        inst = await conn.fetchrow("SELECT * FROM ptp_installments WHERE ptp_id = $1 AND sequence = $2", ptp_id, sequence)
        if not inst or inst['status'] in ['PAID', 'COMPLETED']:
            return

        # Simulate checking payment system (mock)
        # In production, you'd check your payment gateway/ledger.
        # For demo, we'll assume a function `check_payment_for_invoice`
        paid_amount = await check_payment_for_invoice(inst['invoice_id'], inst['amount'], inst['due_date'])

        if paid_amount >= inst['amount']:
            # PAID!
            await conn.execute("""
                UPDATE ptp_installments SET status = 'PAID', actual_paid_amount = $1, paid_at = NOW(), updated_at = NOW()
                WHERE ptp_id = $2 AND sequence = $3
            """, paid_amount, ptp_id, sequence)
            # Update header
            await conn.execute("""
                UPDATE ptp_headers SET total_received_amount = total_received_amount + $1, updated_at = NOW()
                WHERE ptp_id = $2
            """, paid_amount, ptp_id)
            # Update company behavior: completed
            await conn.execute("""
                UPDATE companies SET ptp_behavior = jsonb_set(
                    ptp_behavior, '{completed}', ((ptp_behavior->>'completed')::int + 1)::text::jsonb
                ) WHERE company_id = $1
            """, inst['company_id'])
            # Check if PTP is complete
            await close_ptp_if_complete(ptp_id)
            print(f"✅ PTP {ptp_id} installment {sequence} PAID.")

        elif datetime.utcnow().date() > inst['due_date']:
            # MISSED!
            await conn.execute("""
                UPDATE ptp_installments SET status = 'MISSED', updated_at = NOW()
                WHERE ptp_id = $1 AND sequence = $2
            """, ptp_id, sequence)
            # Update company behavior: broken
            await conn.execute("""
                UPDATE companies SET ptp_behavior = jsonb_set(
                    ptp_behavior, '{broken}', ((ptp_behavior->>'broken')::int + 1)::text::jsonb
                ) WHERE company_id = $1
            """, inst['company_id'])
            # Handle breach
            await handle_ptp_breach(ptp_id, sequence)
            print(f"⚠️ PTP {ptp_id} installment {sequence} MISSED.")


# ---------- MOCK PAYMENT CHECK ----------
async def check_payment_for_invoice(invoice_id: str, expected_amount: float, due_date: date) -> float:
    """
    Mock: In production, query your payment ledger.
    For demo, assume payment is received if we manually set a flag or use a mock table.
    """
    # For testing, you can manually update a payment_log table or just return 0.
    # We'll check a simple `payment_log` table (you can create one).
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Try to get a payment record for this invoice on or after due_date
        row = await conn.fetchrow("""
            SELECT SUM(amount) as total FROM payment_log 
            WHERE invoice_id = $1 AND paid_at >= $2
        """, invoice_id, due_date)
        if row and row['total']:
            return float(row['total'])
    return 0.0


# ---------- HANDLE PTP BREACH ----------
async def handle_ptp_breach(ptp_id: str, sequence: int):
    """
    Bounded recovery sequence: Reminders -> Escalation.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        inst = await conn.fetchrow("SELECT * FROM ptp_installments WHERE ptp_id = $1 AND sequence = $2", ptp_id, sequence)
        if not inst:
            return

        new_reminder_count = inst['reminder_count'] + 1
        await conn.execute("""
            UPDATE ptp_installments SET reminder_count = $1, last_reminder_at = NOW(), updated_at = NOW()
            WHERE ptp_id = $2 AND sequence = $3
        """, new_reminder_count, ptp_id, sequence)

        # Get company contact
        company = await conn.fetchrow("SELECT * FROM companies WHERE company_id = $1", inst['company_id'])
        contact = company['ap_contact'] if isinstance(company['ap_contact'], dict) else json.loads(company['ap_contact'])

        max_reminders = 3  # Configurable
        if new_reminder_count <= max_reminders:
            # Send reminder
            email = contact.get('email')
            if email:
                subject = f"⏰ Payment Reminder: PTP {ptp_id} Installment {sequence}"
                body = f"Dear {company['name']},\n\nYou promised ₹{inst['amount']} on {inst['due_date']}. This payment is now overdue. Please make the payment immediately.\n\nRegards,\nFinance Team"
                await send_email(email, subject, body)
                print(f"📧 Reminder {new_reminder_count} sent to {email} for PTP {ptp_id}.")
        else:
            # Escalate to RM
            await escalate_to_rm(
                inst['company_id'],
                "ptp_breach",
                f"PTP {ptp_id} installment {sequence} missed after {max_reminders} reminders."
            )
            await conn.execute("""
                UPDATE ptp_headers SET status = 'BROKEN', updated_at = NOW() WHERE ptp_id = $1
            """, ptp_id)
            print(f"👨‍💼 PTP {ptp_id} escalated to RM.")


# ---------- CLOSE PTP IF COMPLETE ----------
async def close_ptp_if_complete(ptp_id: str):
    pool = await get_pool()
    async with pool.acquire() as conn:
        header = await conn.fetchrow("SELECT * FROM ptp_headers WHERE ptp_id = $1", ptp_id)
        if header['total_received_amount'] >= header['total_promised_amount']:
            await conn.execute("UPDATE ptp_headers SET status = 'COMPLETED', updated_at = NOW() WHERE ptp_id = $1", ptp_id)
            # Update invoice and case status
            await conn.execute("UPDATE invoices SET recovery_status = 'resolved' WHERE invoice_id = $1", header['invoice_id'])
            await conn.execute("UPDATE cases SET status = 'resolved' WHERE invoice_id = $1", header['invoice_id'])
            print(f"🎉 PTP {ptp_id} COMPLETED and resolved.")


# ---------- BACKGROUND POLLER FOR PTP ----------
async def process_due_ptp_installments():
    """
    Called by background worker to check due installments.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Find installments where due_date <= today and status in PENDING or MISSED
        installments = await conn.fetch("""
            SELECT ptp_id, sequence FROM ptp_installments 
            WHERE due_date <= CURRENT_DATE AND status IN ('PENDING', 'MISSED')
        """)
        for inst in installments:
            await check_ptp_installment(inst['ptp_id'], inst['sequence'])