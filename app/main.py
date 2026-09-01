# app/main.py
import json
import asyncio
from datetime import datetime
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.db import init_db, get_pool
from app.orchestrator import process_pending_events, process_scheduled_cases

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


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


# --- Admin: Trigger ---
@app.post("/admin/process")
async def manual_process():
    await process_pending_events()
    await process_scheduled_cases()
    return {"status": "processing_triggered"}


# --- Dashboard API ---
@app.get("/dashboard/stats")
async def dashboard_stats():
    pool = await get_pool()
    async with pool.acquire() as conn:
        stats = await conn.fetchrow("""
            SELECT
                COUNT(*) AS total_cases,
                COALESCE(SUM(CASE WHEN status IN ('new','reminding','plan_offered') THEN amount ELSE 0 END), 0) AS at_risk,
                COALESCE(SUM(CASE WHEN status = 'resolved' THEN amount ELSE 0 END), 0) AS recovered,
                COALESCE(SUM(CASE WHEN status = 'plan_offered' THEN amount ELSE 0 END), 0) AS promised,
                COALESCE(SUM(CASE WHEN status = 'escalated' THEN amount ELSE 0 END), 0) AS escalated
            FROM cases
        """)
        return dict(stats)


@app.get("/dashboard/cases")
async def dashboard_cases(limit: int = 20):
    pool = await get_pool()
    async with pool.acquire() as conn:
        cases = await conn.fetch("""
            SELECT id AS case_id, invoice_id, company_id, status, root_cause, amount, last_action, llm_reasoning, updated_at
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