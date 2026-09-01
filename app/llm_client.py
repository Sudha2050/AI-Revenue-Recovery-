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