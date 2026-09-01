# app/llm_client.py
import os
import json
import asyncio
from dotenv import load_dotenv

load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if GEMINI_API_KEY:
    import google.generativeai as genai
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel('gemini-1.5-flash')
    print("✅ Gemini 1.5 Flash initialized.")
else:
    model = None
    print("⚠️ GEMINI_API_KEY not set. LLM will use mock reasoning.")

async def llm_diagnose(invoice_data: dict, account_signals: dict) -> dict:
    """
    Call Gemini API to diagnose complex B2B payment failures.
    If API key missing, return a mock diagnosis.
    """
    if not model:
        # Mock diagnosis for fallback
        if invoice_data.get('days_overdue', 0) > 60:
            return {"action": "rm_handoff", "root_cause": "chronic_late",
                    "reasoning": "Mock LLM: overdue >60 days, escalate to RM."}
        return {"action": "send_email", "root_cause": "process_breakdown",
                "reasoning": "Mock LLM: generic case, send reminder."}

    prompt = f"""
You are a B2B receivables recovery assistant for a Payments Bank.
Analyze this case and provide a diagnosis.

Invoice Data:
- Amount: ₹{invoice_data.get('amount', 0)}
- Days Overdue: {invoice_data.get('days_overdue', 0)}
- Payment Rail: {invoice_data.get('payment_rail', 'unknown')}
- Failure Code: {invoice_data.get('failure_code', 'none')}

Account Signals:
- Balance Trend: {account_signals.get('balance_trend', 'healthy')}
- Dispute Flag: {account_signals.get('dispute_flag', False)}
- Mandate Revoked: {account_signals.get('mandate_revoked', False)}
- Account Frozen: {account_signals.get('account_frozen', False)}

Choose one root cause: process_breakdown, liquidity_issue, dispute, chronic_late, willful_default.
Choose one action: send_email, offer_plan, rm_handoff, halt.

Respond in JSON: {{"action": "...", "root_cause": "...", "reasoning": "..."}}
"""
    try:
        def _sync():
            response = model.generate_content(
                prompt,
                generation_config={"response_mime_type": "application/json", "temperature": 0.2}
            )
            return json.loads(response.text)
        result = await asyncio.to_thread(_sync)
        return {
            "action": result.get("action", "send_email"),
            "root_cause": result.get("root_cause", "process_breakdown"),
            "reasoning": result.get("reasoning", "LLM diagnosis completed.")
        }
    except Exception as e:
        print(f"⚠️ Gemini API error: {e}. Falling back to mock.")
        return {"action": "send_email", "root_cause": "process_breakdown",
                "reasoning": f"LLM unavailable: {str(e)}. Escalating to manual review."}


async def classify_customer_intent(message_text: str, context: dict = None) -> dict:
    """
    AI/NLP Agent: Analyze inbound customer response (Email/WhatsApp/SMS) and classify intent.
    Intents:
      - 'pay_now': customer says they paid, want link, or are initiating payment now.
      - 'promise_to_pay': customer promises to pay by a specific date / timeframe.
      - 'dispute': customer disputes the invoice, goods, services, or pricing.
      - 'general_inquiry': customer asks a question or requests invoice copy.
    """
    context = context or {}
    text_lower = (message_text or '').lower()

    # Rule-based fallback heuristic
    def _rule_fallback():
        import re
        from datetime import datetime, timedelta

        # 1. Dispute keywords
        if any(w in text_lower for w in ["dispute", "wrong invoice", "incorrect amount", "not received", "fraud", "faulty", "overcharge", "cancel order"]):
            return {
                "intent": "dispute",
                "promised_date": None,
                "reasoning": "Customer indicated an invoice dispute or dissatisfaction with goods/services.",
                "suggested_reply": "We apologize for the inconvenience. Our Relationship Manager will contact you immediately to review this dispute."
            }

        # 2. Pay now / Already paid keywords
        if any(w in text_lower for w in ["paid", "already transferred", "receipt attached", "payment link", "pay now", "cleared", "processed payment"]):
            return {
                "intent": "pay_now",
                "promised_date": None,
                "reasoning": "Customer confirmed payment or requested instant payment link.",
                "suggested_reply": "Thank you for confirming. We are verifying the payment with our banking rail."
            }

        # 3. Promise to Pay (PTP) keywords & date parsing
        if any(w in text_lower for w in ["will pay", "promise", "by next week", "tomorrow", "arranging funds", "clear this by", "within", "remit on"]):
            # Infer date
            inferred_date = (datetime.utcnow() + timedelta(days=7)).strftime("%Y-%m-%d")
            if "tomorrow" in text_lower:
                inferred_date = (datetime.utcnow() + timedelta(days=1)).strftime("%Y-%m-%d")
            elif "next week" in text_lower:
                inferred_date = (datetime.utcnow() + timedelta(days=7)).strftime("%Y-%m-%d")
            
            # Simple regex for date YYYY-MM-DD or DD/MM/YYYY
            date_match = re.search(r'\b(\d{4}-\d{2}-\d{2})\b', message_text)
            if date_match:
                inferred_date = date_match.group(1)

            return {
                "intent": "promise_to_pay",
                "promised_date": inferred_date,
                "reasoning": f"Customer promised to pay by {inferred_date}.",
                "suggested_reply": f"Thank you for the update. We have logged your promise to pay on {inferred_date}."
            }

        return {
            "intent": "general_inquiry",
            "promised_date": None,
            "reasoning": "General inquiry or ambiguous response.",
            "suggested_reply": "Thank you for your message. We have forwarded your inquiry to our finance operations team."
        }

    if not model:
        return _rule_fallback()

    prompt = f"""
You are an AI/NLP Receivables Assistant analyzing an inbound customer response about an overdue invoice.

Customer Message:
\"\"\"{message_text}\"\"\"

Invoice Context:
- Invoice ID: {context.get('invoice_id', 'N/A')}
- Amount: ₹{context.get('amount', 'N/A')}
- Company: {context.get('company_name', 'N/A')}

Analyze the customer's intent and choose exactly ONE intent:
1. "pay_now" - Customer states they have paid, are paying now, or ask for payment link.
2. "promise_to_pay" - Customer promises to pay on or by a specific date. Extract that date as YYYY-MM-DD.
3. "dispute" - Customer disputes the charge, invoice amount, deliverables, or claims error.
4. "general_inquiry" - Asking for invoice copy, PO number, contact info, etc.

Respond strictly in JSON:
{{
  "intent": "pay_now" | "promise_to_pay" | "dispute" | "general_inquiry",
  "promised_date": "YYYY-MM-DD" or null,
  "reasoning": "brief explanation",
  "suggested_reply": "polite professional reply message to send to the customer"
}}
"""
    try:
        def _sync():
            response = model.generate_content(
                prompt,
                generation_config={"response_mime_type": "application/json", "temperature": 0.1}
            )
            return json.loads(response.text)
        result = await asyncio.to_thread(_sync)
        return {
            "intent": result.get("intent", "general_inquiry"),
            "promised_date": result.get("promised_date"),
            "reasoning": result.get("reasoning", "LLM classified intent."),
            "suggested_reply": result.get("suggested_reply", "Thank you for your update.")
        }
    except Exception as e:
        print(f"⚠️ Gemini intent classification error: {e}. Using fallback heuristic.")
        return _rule_fallback()