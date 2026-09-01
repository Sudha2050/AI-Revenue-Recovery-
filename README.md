# 🏦 B2B Revenue Recovery Agent `[Buildathon]`

[![Buildathon](https://img.shields.io/badge/Project-Razorpay%20Buildathon-blueviolet?style=for-the-badge&logo=razorpay)](https://razorpay.com)
[![Python](https://img.shields.io/badge/Python-3.13-blue?style=for-the-badge&logo=python)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115.6-009688?style=for-the-badge&logo=fastapi)](https://fastapi.tiangolo.com)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-asyncpg-336791?style=for-the-badge&logo=postgresql)](https://github.com/MagicStack/asyncpg)
[![Gemini](https://img.shields.io/badge/AI-Google%20Gemini-orange?style=for-the-badge&logo=google)](https://ai.google.dev)

> 🚀 **[Buildathon] Project Submission**  
> An autonomous, compliance-first B2B Receivables Recovery & Intelligent Dunning Agent engineered for Payments Banks and enterprise B2B merchants. It manages multi-rail payment failures (NEFT, RTGS, UPI Autopay, NACH), enforces strict regulatory guardrails (**RBI Fair Practices Code** & **NPCI UPI Guidelines**), uses a hybrid Rules + Google Gemini LLM reasoning engine, and delivers closed-loop recovery with conversational **AI/NLP Inbound Intent Classification**, structured **Promise-to-Pay (PTP) Installment Tracking**, authenticated payment webhooks, multi-channel communications (Email, WhatsApp, Slack), and real-time dashboard analytics.

---

## 🌟 Key Highlights & Innovations

- 🤝 **Comprehensive Promise-To-Pay (PTP) Engine**:
  - **Multi-Installment Extraction**: Parses customer messages via LLM/NLP into structured installment schedules (amount + due date).
  - **Automated Approval Gating**: Auto-approves PTP plans $\le \text{₹100,000}$ with $\le 2$ installments (`ACTIVE`); routes larger promises to human RM approval (`PENDING_APPROVAL`).
  - **Breach Recovery Workflow**: Monitors due dates in background (`process_due_ptp_installments`). Missed payments trigger a 3-step bounded reminder sequence (`MISSED`), escalating to `BROKEN` and alerting RMs if unfulfilled.
- 💳 **Verified Payment Recovery Safeguards**:
  - Eliminates unverified revenue inflation: customer claims of `pay_now` transition cases to `payment_claimed` and issue payment links.
  - Invoices are **only** marked `resolved`/`recovered` upon authentic bank or payment gateway webhook confirmation (`/webhooks/payment_received`).
- 🔒 **Authenticated Webhook Security (HMAC SHA256)**:
  - Signature verification (`X-Signature` / `X-Webhook-Secret`) on all inbound webhook routes prevents spoofed response or dispute events.
- 🛡️ **Zero Compliance Violations (Hard Guardrails)**:
  - **RBI Fair Practices Code**: Maximum 2 automated contact attempts per 7-day rolling window per invoice.
  - **NPCI UPI Autopay**: Strict cap of 3 retries for UPI rails before mandatory human handoff.
  - **Payment Plan Bounds**: Auto-approval restricted to invoices ≤60 days overdue and ≤2 installments; larger plans require RM sign-off.
  - **Hard Stops**: Immediate automation freeze for open disputes, willful defaults, and frozen accounts.
- 🧠 **Hybrid Diagnosis Engine (Rules + Gemini LLM)**:
  - Fast, deterministic rules for rail failures (`insufficient_funds`, `mandate_expired`, `account_closed`) and liquidity signals.
  - Google Gemini LLM fallback for complex, ambiguous, or mixed-signal B2B cases.
- 💬 **Conversational AI / NLP Inbound Intent Classifier**:
  - Ingests customer replies via **Email** & **WhatsApp** webhooks (`/webhooks/customer_response`, `/webhooks/whatsapp`).
  - Classifies customer intent into:
    - **`pay_now`**: Issues checkout links and transitions case to `payment_claimed` (awaiting payment webhook).
    - **`promise_to_pay` (PTP)**: Extracts installment schedule, creates PTP record, applies approval gating, and links active PTP monitoring.
    - **`dispute`**: Flags company/invoice, immediately halts dunning, and escalates to Relationship Manager with dispute context.
    - **`general_inquiry`**: Routes to RM and returns context-aware automated response.
- 📲 **Multi-Channel Orchestration**:
  - Automated Accounts Payable (AP) **Email** dunning.
  - Direct **WhatsApp** reminders and interactive message parsing.
  - High-priority **Slack** alerts for Relationship Manager (RM) escalation queues.
- 📊 **Real-Time Operations Dashboard**:
  - Partitioned, non-overlapping KPIs: **₹ At Risk**, **₹ Recovered**, **₹ Promised**, **₹ Payment Claimed**, **₹ Escalated**, and **Total Cases**.

---

## 🏗️ End-to-End Workflow Diagram

![Autonomous B2B Revenue Recovery AI Agent Workflow Architecture](static/images/workflow_diagram.jpg)

```text
                  ┌──────────────────┐
                  │ Invoice System   │ (ERP, Billing, Bank Rails)
                  └────────┬─────────┘
                           ↓
                  ┌──────────────────┐
                  │ Invoice Database │ (invoices, companies, ptp_headers, raw_events)
                  └────────┬─────────┘
                           ↓
                  ┌──────────────────┐
                  │ Receivables      │
                  │ Monitoring Agent │ (process_pending_events & process_due_ptp_installments)
                  └────────┬─────────┘
                           ↓
                 Is payment confirmed?
                     /          \
                   YES           NO
                   ↓              ↓
              Close Case     Check Due Date & Rail Context
                                  ↓
                         ┌─────────────────┐
                         │ Choose Strategy │ (Rules + Gemini LLM)
                         └────────┬────────┘
                                  ↓
                    ┌─────────────┼─────────────┐
                    ↓             ↓             ↓
                 Reminder      Overdue       Escalate
                    ↓             ↓             ↓
                  Email        WhatsApp      Slack RM
                    \             |             /
                     \            |            /
                      └───────────┼───────────┘
                                  ↓
                           Customer Response (HMAC Authenticated Webhook)
                                  ↓
                          ┌───────────────┐
                          │ AI/NLP Agent  │ (Gemini Intent Engine)
                          └───────┬───────┘
                                  ↓
                      Understand Customer Intent
                                  ↓
          ┌──────────────┬────────┴────────────┬─────────────┐
          ↓              ↓                     ↓             ↓
       Pay now       Promise to Pay         Dispute      No response
          ↓              ↓                     ↓             ↓
       Payment       PTP Engine &          Halt & RM      Follow-up
       Link Sent    Installment Schedule   Escalate          ↓
          ↓              ↓                                Escalate
     Awaiting      Approval Gating
     Webhook     (Auto <= 100k, else RM)
          ↓              ↓
   Confirmed Pay?   Check Due Dates
    /         \      /         \
  YES          NO  PAID      MISSED
   ↓            ↓   ↓          ↓
 Resolved  Wait/Retry Close  3x Retry -> BROKEN -> RM
```

---

## 🛡️ Regulatory Compliance & Policy Engine

The **Policy Engine** (`app/policy_engine.py`) acts as the single authoritative gatekeeper for compliance. Even if diagnosis suggests automated dunning, the policy engine intercepts and overrides actions when boundaries are met:

| Rule / Regulation | Condition | Enforced Policy Action |
| :--- | :--- | :--- |
| **Dispute Flag** | `company.dispute_flag == True` OR Customer replies with dispute | `halt` (0 automated contact permitted, notify RM) |
| **Willful Default** | `company.willful_default == True` | `halt` (Immediate freeze on dunning, credit risk review) |
| **Account Frozen** | `company.account_frozen == True` | `rm_handoff` (Mandatory RM check before action) |
| **RBI Contact Cap** | ≥ 2 contacts in last 7 days | `rm_handoff` (`contact_cap_reached`) |
| **NPCI UPI Autopay** | `rail == 'UPI'` and retries ≥ 3 | `rm_handoff` (`max_retries_exhausted`) |
| **Plan Auto-Approve**| Overdue > 60 days OR installments > 2 | `rm_handoff` (`plan_exceeds_auto_approve`) |

---

## 🛠️ Tech Stack

- **Backend Framework**: [FastAPI](https://fastapi.tiangolo.com/) (Python 3.13) + [Uvicorn](https://www.uvicorn.org/)
- **Database Engine**: PostgreSQL with async connection pooling via [asyncpg](https://github.com/MagicStack/asyncpg)
- **AI / NLP Engine**: [Google Generative AI (Gemini 1.5 Flash)](https://ai.google.dev/)
- **Security**: HMAC SHA256 Webhook Signature Verification (`X-Signature` / `X-Webhook-Secret`)
- **Communication Channels**: Email (SendGrid/SMTP), WhatsApp Business API / Twilio, Slack SDK (RM Alerts)
- **Frontend Dashboard**: Vanilla HTML5, Modern CSS Design System, Vanilla JS (Auto-polling every 10s)

---

## 📂 Project Structure

```text
Revenue-recovery-agent/
├── app/
│   ├── actions.py         # Multi-channel execution (Email, WhatsApp, Slack RM, Plan generation)
│   ├── db.py              # Async PostgreSQL pool lifecycle & PTP table migrations
│   ├── llm_client.py      # Gemini 1.5 Flash B2B diagnosis & NLP Customer Intent Classifier
│   ├── main.py            # FastAPI routes, HMAC security, webhooks, lifespan worker & dashboard APIs
│   ├── orchestrator.py    # 6-step recovery pipeline, PTP state machine, breach recovery & intent router
│   ├── policy_engine.py   # RBI Fair Practices Code & NPCI compliance guardrails
│   ├── poller.py          # Background ERP polling integration template
│   ├── schemas.py         # Pydantic data schemas
│   └── seed_data.py       # Database schema initialization & demo dataset seeder
├── static/
│   ├── css/
│   │   └── style.css      # Premium dark-mode dashboard styling
│   ├── js/
│   │   └── app.js         # Real-time dashboard KPI, case feed & intent simulator
│   └── index.html         # Live Web Dashboard
├── .env.example           # Environment variable template
├── .gitignore             # Git ignore configuration
├── requirements.txt       # Project dependencies
└── README.md              # Project documentation
```

---

## 🚀 Getting Started

### 1. Prerequisites
- Python 3.10+ (Tested on Python 3.13)
- PostgreSQL database instance
- *(Optional)* `GEMINI_API_KEY`, `SLACK_WEBHOOK_URL`, `WEBHOOK_SECRET`

### 2. Clone & Setup Virtual Environment

```bash
git clone https://github.com/Sudha2050/AI-Revenue-Recovery-.git
cd AI-Revenue-Recovery-

# Windows
python -m venv venv
.\venv\Scripts\activate

# Linux / macOS
python3 -m venv venv
source venv/bin/activate
```

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure Environment Variables

Create a `.env` file in the project root:

```ini
DATABASE_URL=postgresql://postgres:password@localhost:5432/revenue_db
GEMINI_API_KEY=your_gemini_api_key
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...
WEBHOOK_SECRET=dev-webhook-secret
DRY_RUN=true
```

> **Note**: Setting `DRY_RUN=true` enables safe simulation of emails, WhatsApp messages, plan document creation, and Slack alerts without sending live traffic.

### 5. Seed Database Schema & Demo Records

Initialize tables (including `ptp_headers` & `ptp_installments`) and seed sample B2B companies, invoices, and failure events:

```bash
python -m app.seed_data
```

### 6. Start the FastAPI Application

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Access the dashboard at: **[http://localhost:8000/](http://localhost:8000/)** (or **[http://localhost:8000/dashboard](http://localhost:8000/dashboard)**)

---

## 📡 API Reference

### Web Dashboard
- `GET /` or `GET /dashboard` — Web Dashboard UI.
- `GET /dashboard/stats` — Partitioned financial recovery stats (`at_risk`, `recovered`, `promised`, `payment_claimed`, `escalated`, `total_cases`, `total_amount`).
- `GET /dashboard/cases?limit=20` — Case feed with customer intent, status, and last action.

### Webhook Ingestion (HMAC Authenticated)
- `POST /webhooks/b2b_invoice` — Ingest payment failures and overdue invoices from ERP or payment rails.
- `POST /webhooks/customer_response` — Ingest inbound customer email or portal reply for AI/NLP intent classification and PTP processing.
- `POST /webhooks/whatsapp` — Ingest inbound WhatsApp messages.
- `POST /webhooks/ptp_commit` — Directly commit a structured Promise-to-Pay (PTP) installment schedule.
- `POST /webhooks/payment_received` — Ingest confirmed payment notifications from bank or payment gateway webhooks.
- `POST /admin/process` — Manually triggers event processing, PTP installment checks, and scheduled case follow-ups.

---

## 🧪 Testing with PowerShell & cURL

```powershell
# 1. Trigger the orchestrator pipeline
curl.exe -X POST http://localhost:8000/admin/process

# 2. Simulate Direct PTP Commit Webhook
$body = @{ invoice_id = "INV-001"; company_id = "comp_001"; installments = @( @{ amount = 50000; due_date = "2026-09-15" }, @{ amount = 50000; due_date = "2026-09-22" } ); reasoning = "Customer promises two installments" } | ConvertTo-Json -Depth 3
Invoke-RestMethod -Uri http://localhost:8000/webhooks/ptp_commit -Method POST -Body $body -ContentType "application/json" -Headers @{ "X-Webhook-Secret" = "dev-webhook-secret" }

# 3. Simulate Inbound Pay-Now Claim
$body = @{ invoice_id = "INV-004"; message = "I want to pay now, please send payment link"; channel = "email" } | ConvertTo-Json
Invoke-RestMethod -Uri http://localhost:8000/webhooks/customer_response -Method POST -Body $body -ContentType "application/json" -Headers @{ "X-Webhook-Secret" = "dev-webhook-secret" }

# 4. Confirm Payment via Gateway Webhook (Resolves Invoice)
$body = @{ invoice_id = "INV-004"; amount = 100000; payment_reference = "TXN-998877" } | ConvertTo-Json
Invoke-RestMethod -Uri http://localhost:8000/webhooks/payment_received -Method POST -Body $body -ContentType "application/json" -Headers @{ "X-Webhook-Secret" = "dev-webhook-secret" }

# 5. Simulate Inbound Dispute via WhatsApp
$body = @{ invoice_id = "INV-002"; message = "Please hold, we dispute this invoice amount. Deliverables incomplete."; channel = "whatsapp" } | ConvertTo-Json
Invoke-RestMethod -Uri http://localhost:8000/webhooks/whatsapp -Method POST -Body $body -ContentType "application/json" -Headers @{ "X-Webhook-Secret" = "dev-webhook-secret" }
```

---

## 🏆 Razorpay Build-a-thon Submission

This solution tackles the multi-billion dollar problem of B2B payment failure & involuntary receivables delays. By combining multi-rail intelligence, strict compliance guardrails (RBI & NPCI), multi-channel dunning (Email, WhatsApp, Slack), structured PTP installment tracking, HMAC webhook security, and verified closed-loop recovery, it maximizes cash recovery while eliminating regulatory and operational risk.