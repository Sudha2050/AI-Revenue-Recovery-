# app/main.py
import json
import os
import hmac
import hashlib
import asyncio
from datetime import datetime
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.db import init_db, get_pool
from app.orchestrator import (
    process_pending_events,
    process_scheduled_cases,
    process_customer_response,
    process_due_ptp_installments,
    handle_payment_received
)

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


# --- Webhook Authentication / Signature Verification ---
async def verify_webhook_signature(request: Request):
    """
    Verify HMAC SHA256 signature or shared token on inbound webhooks (Fix for Bug 4).
    """
    secret = os.getenv("WEBHOOK_SECRET")
    sig = (
        request.headers.get("X-Signature")
        or request.headers.get("X-Webhook-Secret")
        or request.headers.get("x-signature")
    )

    if secret or sig:
        if not sig:
            raise HTTPException(status_code=401, detail="Missing webhook signature header (X-Signature)")

        valid = False
        if secret and hmac.compare_digest(sig, secret):
            valid = True
        else:
            body = await request.body()
            key = (secret or "").encode("utf-8")
            expected_sig = hmac.new(key, body, hashlib.sha256).hexdigest()
            if hmac.compare_digest(sig, expected_sig):
                valid = True

        if not valid:
            raise HTTPException(status_code=403, detail="Invalid webhook signature")


# --- Lifespan ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    async def worker():
        print("🔄 B2B Orchestrator started. Checking every 30s...")
        while True:
            try:
                await process_pending_events()
                await process_scheduled_cases()
                await process_due_ptp_installments()
            except Exception as e:
                print(f"⚠️ Worker error: {e}")
            await asyncio.sleep(30)
    task = asyncio.create_task(worker())
    yield
    task.cancel()
    print("🛑 Orchestrator stopped.")


app = FastAPI(title="B2B Receivables Agent", lifespan=lifespan)

# Mount static directory
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# --- Health ---
@app.get("/")
async def root():
    return {"message": "B2B Receivables Agent running."}


# --- Webhook: Simulate B2B Invoice + Rail Status ---
@app.post("/webhooks/b2b_invoice")
async def b2b_invoice_webhook(request: Request):
    await verify_webhook_signature(request)
    data = await request.json()
    invoice_id = data.get('invoice_id')
    company_id = data.get('company_id')
    amount = data.get('amount')
    due_date = data.get('due_date')
    payment_rail = data.get('payment_rail', 'NEFT')
    failure_code = data.get('failure_code', '')

    if not all([invoice_id, company_id, amount]):
        raise HTTPException(status_code=400, detail="Missing required fields")

    pool = await get_pool()
    async with pool.acquire() as conn:
        parsed_due_date = datetime.now()
        days_overdue = 0
        if due_date:
            try:
                parsed_due_date = datetime.fromisoformat(due_date)
                days_overdue = max(0, (datetime.now() - parsed_due_date).days)
            except Exception:
                pass

        # Insert/update invoice
        await conn.execute("""
            INSERT INTO invoices (invoice_id, company_id, amount, due_date, payment_rail, failure_code, days_overdue)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (invoice_id) DO UPDATE SET
                amount = EXCLUDED.amount,
                payment_rail = EXCLUDED.payment_rail,
                failure_code = EXCLUDED.failure_code,
                days_overdue = EXCLUDED.days_overdue
        """, invoice_id, company_id, amount, parsed_due_date, payment_rail, failure_code, days_overdue)

        # Insert raw event for orchestrator
        await conn.execute("""
            INSERT INTO raw_events (event_id, event_type, payload)
            VALUES ($1, $2, $3) ON CONFLICT (event_id) DO NOTHING
        """, f"webhook_{invoice_id}", "b2b_invoice", json.dumps({"invoice_id": invoice_id, "company_id": company_id}))

    return {"status": "ingested", "invoice_id": invoice_id}


# --- Webhook: Inbound Customer Response (Email / SMS / Web form) ---
@app.post("/webhooks/customer_response")
async def customer_response_webhook(request: Request):
    """
    Ingest customer response and classify intent with AI/NLP (Pay now, Promise to Pay, Dispute, Inquiry).
    """
    await verify_webhook_signature(request)
    data = await request.json()
    invoice_id = data.get('invoice_id')
    message = data.get('message') or data.get('text') or data.get('body')
    channel = data.get('channel', 'email')

    if not invoice_id or not message:
        raise HTTPException(status_code=400, detail="Missing invoice_id or message")

    result = await process_customer_response(invoice_id, message, channel=channel)
    return result


# --- Webhook: Inbound WhatsApp Message (Twilio / Meta WhatsApp API format) ---
@app.post("/webhooks/whatsapp")
async def whatsapp_inbound_webhook(request: Request):
    """
    Ingest WhatsApp customer responses. Supports form-data or JSON payload.
    """
    await verify_webhook_signature(request)
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        data = await request.json()
        invoice_id = data.get("invoice_id")
        message = data.get("message") or data.get("Body")
    else:
        form = await request.form()
        message = form.get("Body") or form.get("message") or ""
        invoice_id = form.get("invoice_id")

    # If invoice_id not provided in body, attempt to extract from text (e.g. "INV-001")
    if not invoice_id and message:
        import re
        match = re.search(r'\b(INV-\d+)\b', message, re.IGNORECASE)
        if match:
            invoice_id = match.group(1).upper()

    if not invoice_id or not message:
        raise HTTPException(status_code=400, detail="Missing invoice_id or WhatsApp message text")

    result = await process_customer_response(invoice_id, message, channel="whatsapp")
    return result


# --- Webhook: Confirmed Payment Receipt (Razorpay / Bank Webhook) ---
@app.post("/webhooks/payment_received")
async def payment_received_webhook(request: Request):
    """
    Confirmed payment receipt from payment rail / bank webhook (Fix for Bug 3).
    """
    await verify_webhook_signature(request)
    data = await request.json()
    invoice_id = data.get("invoice_id")
    amount = data.get("amount")
    ref = data.get("payment_reference") or data.get("transaction_id")

    if not invoice_id or not amount:
        raise HTTPException(status_code=400, detail="Missing invoice_id or amount")

    return await handle_payment_received(invoice_id, float(amount), payment_reference=ref)


# --- Admin: Trigger ---
@app.post("/admin/process")
async def manual_process():
    await process_pending_events()
    await process_scheduled_cases()
    await process_due_ptp_installments()
    return {"status": "processing_triggered"}


# --- Dashboard API ---
@app.get("/dashboard/stats")
async def dashboard_stats():
    """
    Partitioned dashboard metrics (Fix for Bug 5).
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        stats = await conn.fetchrow("""
            SELECT
                COUNT(*) AS total_cases,
                COALESCE(SUM(amount), 0) AS total_amount,
                COALESCE(SUM(CASE WHEN status IN ('reminding', 'inquiry_received', 'processing', 'new', 'unknown') THEN amount ELSE 0 END), 0) AS at_risk,
                COALESCE(SUM(CASE WHEN status IN ('resolved', 'completed') THEN amount ELSE 0 END), 0) AS recovered,
                COALESCE(SUM(CASE WHEN status IN ('plan_offered', 'promise_to_pay', 'pending_approval') THEN amount ELSE 0 END), 0) AS promised,
                COALESCE(SUM(CASE WHEN status IN ('escalated', 'halted') THEN amount ELSE 0 END), 0) AS escalated,
                COALESCE(SUM(CASE WHEN status = 'payment_claimed' THEN amount ELSE 0 END), 0) AS payment_claimed
            FROM cases
        """)
        return dict(stats)


@app.get("/dashboard/cases")
async def dashboard_cases(limit: int = 20):
    pool = await get_pool()
    async with pool.acquire() as conn:
        cases = await conn.fetch("""
            SELECT id AS case_id, invoice_id, company_id, status, root_cause, amount, last_action,
                   llm_reasoning, customer_intent, customer_response, promised_date, updated_at
            FROM cases
            ORDER BY updated_at DESC
            LIMIT $1
        """, limit)
        return [dict(c) for c in cases]


# --- Dashboard HTML Page ---
@app.get("/dashboard")
async def dashboard_page():
    index_file = STATIC_DIR / "index.html"
    if not index_file.exists():
        raise HTTPException(status_code=404, detail="Dashboard frontend not found")
    return FileResponse(index_file)