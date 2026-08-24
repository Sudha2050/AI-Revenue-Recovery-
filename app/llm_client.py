# app/llm_client.py
import os
import json
import asyncio
import google.generativeai as genai
from dotenv import load_dotenv

load_dotenv()

# Configure Gemini
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    # Use Flash for speed and cost (supports JSON mode)
    model = genai.GenerativeModel('gemini-1.5-flash')
else:
    model = None
    print("⚠️ WARNING: GEMINI_API_KEY not set. LLM will fallback to rules.")

async def llm_diagnose(
    error_code: str, 
    error_message: str, 
    amount: float, 
    event_type: str,
    context: dict
):
    """
    Calls Gemini with enriched Customer Context.
    """
    if not model:
        return {
            "action": "human_handoff",
            "delay_hours": 0,
            "reasoning": "LLM not configured. Escalating to human."
        }

    # Build a prompt that tells Gemini WHO the customer is
    prompt = f"""
    You are an AI revenue recovery assistant for a payment gateway in India.
    
    --- CUSTOMER CONTEXT (CRITICAL) ---
    Customer Segment: {context.get('segment', 'standard')}
    Customer LTV: ${context.get('ltv', 0)}
    Plan: {context.get('plan', 'monthly')}
    Total payment attempts (last 3 months): {context.get('total_attempts', 0)}
    Failed attempts (last 3 months): {context.get('failed_attempts', 0)}
    Is this their first failure? {"Yes" if context.get('is_first_failure') else "No"}
    Is this a repeat offender? {"Yes" if context.get('is_repeat_offender') else "No"}
    
    --- PAYMENT FAILURE CONTEXT ---
    Event Type: {event_type}
    Amount: ₹{amount} (INR)
    Error Code: {error_code}
    Error Message: {error_message}
    
    --- YOUR TASK ---
    Based on the customer context and the error, choose the best recovery action:
    1. retry_payment (wait X hours before retrying)
    2. send_email (ask customer to update payment method or switch billing date)
    3. send_sms (send a reminder with a payment link)
    4. human_handoff (escalate to a human agent)
    
    --- GUIDELINES (Rule-Based Context) ---
    - First-time failures for High-LTV customers: Retry in 72 hours (payday aligned). Send a friendly heads-up email.
    - Repeat insufficient-funds failures (2+ times): Offer to switch billing date or update payment method instead of blind retry.
    - Free trial users: Only 1 automated retry, then stop (no spam).
    - Fraud/Dispute: Always human handoff.
    - For standard customers: Retry in 24 hours, then escalate if it fails again.
    
    Respond ONLY in valid JSON format:
    {{"action": "action_name", "delay_hours": 24, "reasoning": "brief explanation including why this action matches this customer context"}}
    """
    
    try:
        def _sync_call():
            response = model.generate_content(
                prompt,
                generation_config={
                    "response_mime_type": "application/json",
                    "temperature": 0.3  # Slightly higher for nuanced reasoning
                }
            )
            return response.text
        
        result_text = await asyncio.to_thread(_sync_call)
        result = json.loads(result_text)
        
        action = result.get("action", "human_handoff")
        if action not in ["retry_payment", "send_email", "send_sms", "human_handoff"]:
            action = "human_handoff"
        
        return {
            "action": action,
            "delay_hours": result.get("delay_hours", 24),
            "reasoning": result.get("reasoning", "LLM decided based on context.")
        }
        
    except Exception as e:
        print(f"⚠️ Gemini API error: {e}")
        return {
            "action": "human_handoff",
            "delay_hours": 0,
            "reasoning": f"Gemini API error: {str(e)}. Escalating to human."
        }