# app/seed_data.py
import asyncio
import asyncpg
import json
import os
from dotenv import load_dotenv
from datetime import datetime, timedelta

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")

async def seed():
    conn = await asyncpg.connect(DATABASE_URL)
    print("✅ Connected to DB.")

    # 1. Create tables
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS companies (
            company_id TEXT PRIMARY KEY,
            name TEXT,
            sector TEXT,
            ltv DECIMAL,
            balance_trend TEXT,
            account_frozen BOOLEAN DEFAULT FALSE,
            dispute_flag BOOLEAN DEFAULT FALSE,
            mandate_revoked BOOLEAN DEFAULT FALSE,
            willful_default BOOLEAN DEFAULT FALSE,
            ap_contact JSONB,
            payment_history JSONB
        );
        CREATE TABLE IF NOT EXISTS invoices (
            invoice_id TEXT PRIMARY KEY,
            company_id TEXT REFERENCES companies(company_id),
            amount DECIMAL,
            due_date TIMESTAMP,
            payment_rail TEXT,
            failure_code TEXT,
            days_overdue INT DEFAULT 0,
            recovery_status TEXT DEFAULT 'new',
            contact_attempts INT DEFAULT 0,
            retry_count INT DEFAULT 0,
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW()
        );
        CREATE TABLE IF NOT EXISTS raw_events (
            id SERIAL PRIMARY KEY,
            event_id TEXT UNIQUE,
            event_type TEXT,
            payload JSONB,
            is_processed BOOLEAN DEFAULT FALSE,
            ingested_at TIMESTAMP DEFAULT NOW()
        );
        CREATE TABLE IF NOT EXISTS cases (
            id SERIAL PRIMARY KEY,
            invoice_id TEXT REFERENCES invoices(invoice_id),
            company_id TEXT REFERENCES companies(company_id),
            status TEXT,
            root_cause TEXT,
            amount DECIMAL,
            last_action TEXT,
            llm_reasoning TEXT,
            current_contact_attempt INT DEFAULT 0,
            scheduled_next_action_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW()
        );
    """)
    print("✅ Tables created.")

    # 2. Seed companies
    companies = [
        {"company_id": "comp_001", "name": "Acme Corp", "sector": "Manufacturing", "ltv": 500000, "balance_trend": "declining", "ap_contact": {"email": "ap@acme.com", "phone": "9999999999"}},
        {"company_id": "comp_002", "name": "TechNova", "sector": "Technology", "ltv": 1200000, "balance_trend": "healthy", "ap_contact": {"email": "finance@technova.com", "phone": "8888888888"}},
        {"company_id": "comp_003", "name": "Solaris Labs", "sector": "Energy", "ltv": 300000, "balance_trend": "critical", "dispute_flag": True, "ap_contact": {"email": "accounts@solaris.com"}},
        {"company_id": "comp_004", "name": "Fenwick Group", "sector": "Finance", "ltv": 800000, "balance_trend": "healthy", "ap_contact": {"email": "payments@fenwick.com"}},
    ]
    for c in companies:
        await conn.execute("""
            INSERT INTO companies (company_id, name, sector, ltv, balance_trend, dispute_flag, ap_contact)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (company_id) DO NOTHING
        """, c["company_id"], c["name"], c["sector"], c["ltv"], c["balance_trend"], c.get("dispute_flag", False), json.dumps(c["ap_contact"]))
    print("✅ Companies seeded.")

    # 3. Seed invoices (some overdue)
    due_date = datetime.now() - timedelta(days=45)
    invoices = [
        {"invoice_id": "INV-001", "company_id": "comp_001", "amount": 450000, "payment_rail": "NEFT", "failure_code": "insufficient_funds"},
        {"invoice_id": "INV-002", "company_id": "comp_002", "amount": 120000, "payment_rail": "UPI", "failure_code": "mandate_expired"},
        {"invoice_id": "INV-003", "company_id": "comp_003", "amount": 800000, "payment_rail": "RTGS", "failure_code": "dispute"},
        {"invoice_id": "INV-004", "company_id": "comp_004", "amount": 210000, "payment_rail": "NACH", "failure_code": "process_breakdown"},
    ]
    for inv in invoices:
        await conn.execute("""
            INSERT INTO invoices (invoice_id, company_id, amount, due_date, payment_rail, failure_code, days_overdue)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (invoice_id) DO NOTHING
        """, inv["invoice_id"], inv["company_id"], inv["amount"], due_date, inv["payment_rail"], inv["failure_code"], 45)

        # Insert raw events for processing
        await conn.execute("""
            INSERT INTO raw_events (event_id, event_type, payload)
            VALUES ($1, $2, $3) ON CONFLICT (event_id) DO NOTHING
        """, f"seed_{inv['invoice_id']}", "b2b_invoice", json.dumps({"invoice_id": inv["invoice_id"], "company_id": inv["company_id"]}))
    print("✅ Invoices and raw events seeded.")
    print("🎉 Database seeded successfully. Run the server and trigger /admin/process")

    await conn.close()

if __name__ == "__main__":
    asyncio.run(seed())