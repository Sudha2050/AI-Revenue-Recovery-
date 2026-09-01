# app/db.py
import asyncpg
import os
from dotenv import load_dotenv

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise ValueError("DATABASE_URL not set in .env file!")

pool = None

async def init_db():
    global pool
    pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=10)
    print("✅ Database connection pool created.")
    async with pool.acquire() as conn:
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
                contact_timestamps JSONB DEFAULT '[]'::jsonb,
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
                invoice_id TEXT UNIQUE REFERENCES invoices(invoice_id),
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
            ALTER TABLE invoices ADD COLUMN IF NOT EXISTS contact_timestamps JSONB DEFAULT '[]'::jsonb;
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint WHERE conname = 'cases_invoice_id_key'
                ) THEN
                    ALTER TABLE cases ADD CONSTRAINT cases_invoice_id_key UNIQUE (invoice_id);
                END IF;
            END $$;
        """)
    return pool

async def get_pool():
    return pool