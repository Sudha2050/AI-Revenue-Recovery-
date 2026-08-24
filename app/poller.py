# poller.py
import asyncio
from datetime import datetime, timedelta

async def fetch_overdue_invoices_from_erp():
    # Simulate hitting an old ERP REST API
    # In reality, you hit SAP/Odoo API here.
    mock_overdue = [
        {"invoice_id": "INV-101", "customer_id": "cus_456", "amount": 500.00, "days_overdue": 15}
    ]
    return mock_overdue

async def poll_billing_system():
    while True:
        print(f"Polling for overdue invoices at {datetime.utcnow()}")
        overdue_list = await fetch_overdue_invoices_from_erp()
        
        for inv in overdue_list:
            canonical = RevenueEvent(
                event_id = f"inv_{inv['invoice_id']}",
                customer_id = inv['customer_id'],
                event_type = "invoice_overdue",
                amount_usd = inv['amount'],
                raw_error_message = f"Overdue by {inv['days_overdue']} days"
            )
            # Insert into raw_events
            # ... (same insert logic as the webhook)
        
        await asyncio.sleep(3600)  # Poll every 1 hour

# Run this in a background thread/worker alongside FastAPI