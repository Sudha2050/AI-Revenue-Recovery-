# app/policy_engine.py
"""
RBI Fair Practices Code + NPCI UPI Autopay rules.

This is the ONLY place compliance/hard-stop decisions are made. diagnose_root_cause()
in orchestrator.py proposes a root cause + suggested action; this function is the
sole authority on whether that suggestion is actually allowed to execute. Keeping
every guardrail in one file means "0 compliance violations" is a claim you can point
to a single function to verify, instead of two files that could drift apart.
"""
import json
from datetime import datetime, timedelta, timezone

CONTACT_CAP_PER_WEEK = 2
CONTACT_WINDOW_DAYS = 7
UPI_MAX_RETRIES = 3
PLAN_AUTO_APPROVE_MAX_DAYS_OVERDUE = 60
PLAN_AUTO_APPROVE_MAX_INSTALLMENTS = 2


def _decision(action, root_cause, reasoning, diagnosis, **extra):
    """
    Builds the final decision dict and flags whether policy overrode the
    diagnosis's suggested action, so the orchestrator can log it in the
    audit trail instead of silently swapping actions.
    """
    suggested = diagnosis.get('action')
    overridden = suggested is not None and suggested != action
    out = {
        "action": action,
        "root_cause": root_cause,
        "reasoning": reasoning,
        "overridden": overridden,
    }
    if overridden:
        out["diagnosis_action"] = suggested
    out.update(extra)
    return out


def _recent_contact_count(contact_timestamps, window_days=CONTACT_WINDOW_DAYS):
    """
    Counts contacts within the last `window_days`. Returns None if
    contact_timestamps isn't populated, signaling the caller should use
    the total-count fallback instead.
    """
    if not contact_timestamps:
        return None
    if isinstance(contact_timestamps, str):
        try:
            contact_timestamps = json.loads(contact_timestamps)
        except (ValueError, TypeError, json.JSONDecodeError):
            return None
    if not isinstance(contact_timestamps, list):
        return None
    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
    count = 0
    for ts in contact_timestamps:
        try:
            ts_dt = datetime.fromisoformat(ts.replace('Z', '+00:00')) if isinstance(ts, str) else ts
            if ts_dt.tzinfo is None:
                ts_dt = ts_dt.replace(tzinfo=timezone.utc)
            if ts_dt >= cutoff:
                count += 1
        except (ValueError, AttributeError):
            continue
    return count


def apply_compliance_policy(case_data: dict, diagnosis: dict, company_context: dict) -> dict:
    """
    Enforce regulatory bounds. Returns the final decision (action, root_cause,
    reasoning, overridden flag). This always wins over whatever diagnosis suggested.
    """

    # --- 1. Hard stops: dispute, willful default, frozen account ---
    if company_context.get('dispute_flag'):
        return _decision(
            "halt", "dispute",
            "Open dispute on file -- 0 automated contact permitted. Routed to RM for informational awareness.",
            diagnosis
        )
    if company_context.get('willful_default'):
        return _decision(
            "halt", "willful_default",
            "Willful default risk flagged -- halting all automated actions. Routed to RM and credit risk team.",
            diagnosis
        )
    if company_context.get('account_frozen'):
        return _decision(
            "rm_handoff", "account_frozen",
            "Account frozen -- mandatory RM review before any further action.",
            diagnosis
        )

    # --- 2. RBI Fair Practices Code: contact frequency cap ---
    recent_count = _recent_contact_count(case_data.get('contact_timestamps'))
    if recent_count is not None:
        if recent_count >= CONTACT_CAP_PER_WEEK:
            return _decision(
                "rm_handoff", "contact_cap_reached",
                f"Contact cap ({CONTACT_CAP_PER_WEEK} per {CONTACT_WINDOW_DAYS} days) reached "
                f"({recent_count} contacts in window). Escalating to RM.",
                diagnosis
            )
    else:
        total_attempts = case_data.get('current_contact_attempt', 0)
        if total_attempts >= CONTACT_CAP_PER_WEEK:
            return _decision(
                "rm_handoff", "contact_cap_reached",
                f"Contact history unavailable -- enforcing stricter cap of "
                f"{CONTACT_CAP_PER_WEEK} total automated contacts for this invoice "
                f"({total_attempts} used). Escalating to RM.",
                diagnosis
            )

    # --- 3. Auto-approve bounds for payment plans ---
    if diagnosis.get('action') == 'offer_plan':
        days_overdue = case_data.get('days_overdue', 0)
        installments = diagnosis.get('installments', PLAN_AUTO_APPROVE_MAX_INSTALLMENTS)
        if days_overdue > PLAN_AUTO_APPROVE_MAX_DAYS_OVERDUE or installments > PLAN_AUTO_APPROVE_MAX_INSTALLMENTS:
            return _decision(
                "rm_handoff", "plan_exceeds_auto_approve",
                f"Plan exceeds auto-approve bounds: {days_overdue} days overdue "
                f"(max {PLAN_AUTO_APPROVE_MAX_DAYS_OVERDUE}), {installments} installments "
                f"(max {PLAN_AUTO_APPROVE_MAX_INSTALLMENTS}). RM sign-off required.",
                diagnosis
            )

    # --- 4. NPCI UPI Autopay max retries ---
    if case_data.get('payment_rail') == 'UPI':
        retry_count = case_data.get('current_retry_count', 0)
        if retry_count >= UPI_MAX_RETRIES:
            return _decision(
                "rm_handoff", "max_retries_exhausted",
                f"NPCI UPI max retries ({UPI_MAX_RETRIES}) exhausted. Escalating to RM.",
                diagnosis
            )
    # NOTE: NACH/NEFT/RTGS retry caps are intentionally not enforced here yet.

    # --- 5. Pass through: diagnosis's suggestion is within all compliance bounds ---
    extra = {}
    if "installments" in diagnosis:
        extra["installments"] = diagnosis["installments"]
    return _decision(
        diagnosis.get('action'),
        diagnosis.get('root_cause', 'unknown'),
        diagnosis.get('reasoning', 'No reasoning provided.'),
        diagnosis,
        **extra
    )