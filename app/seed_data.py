# app/seed_data.py
import asyncio
import asyncpg
import json
import os
from dotenv import load_dotenv

# Load credentials from .env file
load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise ValueError("DATABASE_URL not set in .env file!")

async def seed():
    print(f"🔗 Connecting to database...")
    conn = await asyncpg.connect(DATABASE_URL)
    
    print("✅ Connected to database successfully!")
    
    # --- 1. Create Tables ---
    print("📦 Creating tables if they don't exist...")
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS customers (
            customer_id TEXT PRIMARY KEY,
            email TEXT,
            phone TEXT,
            crm_data JSONB
        );
    """)
    
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS raw_events (
            id SERIAL PRIMARY KEY,
            event_id TEXT UNIQUE NOT NULL,
            event_type TEXT NOT NULL,
            customer_id TEXT NOT NULL,
            payload JSONB NOT NULL,
            canonical_event JSONB NOT NULL,
            ingested_at TIMESTAMP DEFAULT NOW(),
            is_processed BOOLEAN DEFAULT FALSE
        );
    """)
    
    await conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_raw_events_unprocessed 
        ON raw_events (is_processed, ingested_at);
    """)
    print("✅ Tables and indexes are ready.")

    # --- 2. Insert Mock Customer ---
    print("👤 Inserting mock customer...")
    await conn.execute(
        """
        INSERT INTO customers (customer_id, email, phone, crm_data) 
        VALUES ($1, $2, $3, $4) 
        ON CONFLICT (customer_id) DO NOTHING
        """,
        'cus_123', 
        'ravi@example.com', 
        '+91999999999', 
        json.dumps({"source": "manual_seed"})
    )

    # --- 3. Insert Mock Failed Payment Event ---
    print("💳 Inserting mock failed payment event...")
    canonical_event = {
        "event_id": "sim_001",
        "customer_id": "cus_123",
        "event_type": "payment_failed",
        "amount_usd": 49.99,
        "currency": "USD",
        "raw_error_code": "insufficient_funds",
        "raw_error_message": "Insufficient balance in account"
    }
    
    await conn.execute(
        """
        INSERT INTO raw_events (event_id, event_type, customer_id, payload, canonical_event) 
        VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT (event_id) DO NOTHING
        """,
        'sim_001',
        'payment_failed',
        'cus_123',
        json.dumps({"webhook_payload": "mock_test"}),
        json.dumps(canonical_event)
    )
    
    print("✅ Seed data inserted successfully!")
    
    # --- 4. Verify ---
    count = await conn.fetchval("SELECT COUNT(*) FROM raw_events")
    print(f"📊 Total events in raw_events table: {count}")
    
    await conn.close()
    print("🔒 Database connection closed.")

if __name__ == "__main__":
    asyncio.run(seed())