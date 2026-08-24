# schemas.py
from pydantic import BaseModel
from enum import Enum
from datetime import datetime

class EventType(str, Enum):
    PAYMENT_FAILED = "payment_failed"
    INVOICE_OVERDUE = "invoice_overdue"
    CART_ABANDONED = "cart_abandoned"
    SUBSCRIPTION_CANCELED = "subscription_canceled"

class RevenueEvent(BaseModel):
    event_id: str          # Unique ID to prevent duplicate processing
    customer_id: str
    event_type: EventType
    amount_usd: float
    currency: str = "USD"
    # The "Why" - raw error codes from PSP or Billing system
    raw_error_code: str | None = None  
    raw_error_message: str | None = None
    # Metadata to pass extra stuff
    metadata: dict = {}
    occurred_at: datetime = datetime.utcnow()