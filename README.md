# 🏦 B2B Revenue Recovery Agent `[Buildathon]`

[![Buildathon](https://img.shields.io/badge/Project-Razorpay%20Buildathon-blueviolet?style=for-the-badge&logo=razorpay)](https://razorpay.com)
[![Python](https://img.shields.io/badge/Python-3.13-blue?style=for-the-badge&logo=python)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115.6-009688?style=for-the-badge&logo=fastapi)](https://fastapi.tiangolo.com)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-asyncpg-336791?style=for-the-badge&logo=postgresql)](https://github.com/MagicStack/asyncpg)
[![Gemini](https://img.shields.io/badge/AI-Google%20Gemini-orange?style=for-the-badge&logo=google)](https://ai.google.dev)

> 🚀 **[Buildathon] Project Submission**  
> An autonomous, compliance-first B2B Receivables Recovery & Intelligent Dunning Agent engineered for Payments Banks and enterprise B2B merchants. It manages multi-rail payment failures (NEFT, RTGS, UPI Autopay, NACH), enforces strict regulatory guardrails (**RBI Fair Practices Code** & **NPCI UPI Guidelines**), uses a hybrid Rules + Google Gemini LLM reasoning engine, and delivers automated dunning, payment installment plans, and relationship manager (RM) escalations.

---

## 🌟 Key Highlights & Innovations

- 🛡️ **Zero Compliance Violations (Hard Guardrails)**:
  - **RBI Fair Practices Code**: Maximum 2 automated contact attempts per 7-day rolling window per invoice.
  - **NPCI UPI Autopay**: Strict cap of 3 retries for UPI rails before mandatory human handoff.
  - **Payment Plan Bounds**: Auto-approval restricted to invoices ≤60 days overdue and ≤2 installments; larger plans require RM sign-off.
  - **Hard Stops**: Immediate automation freeze for open disputes, willful defaults, and frozen accounts.
- 🧠 **Hybrid Diagnosis Engine (Rules + Gemini LLM)**:
  - Fast, deterministic rules for rail failures (`insufficient_funds`, `mandate_expired`, `account_closed`) and liquidity signals.
  - Google Gemini LLM fallback for complex, ambiguous, or mixed-signal B2B cases.
- 🔍 **Transparent Audit Trail & Policy Overrides**:
  - Distinguishes between what AI suggested and what regulatory policy enforced.
  - Full case history tracked in PostgreSQL with reasonings and scheduled follow-ups.
- 📬 **Action Channels & Smart Dunning**:
  - Automated Accounts Payable (AP) email dunning with dynamic overdue context.
  - Formal installment plan document generation.
  - High-priority Slack alerts for Relationship Manager (RM) escalation queues.
- 📊 **Real-Time Operations Dashboard**:
  - Live KPIs: **₹ At Risk**, **₹ Recovered**, **₹ Promised**, **RM Escalations**, and **Compliance Violations (0)**.
  - Real-time case feed and manual orchestrator execution triggers.

---

## 🏗️ System Architecture

```text
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│                                     DATA SOURCES                                        │
│  ┌─────────────────────────┐   ┌──────────────────────────┐   ┌──────────────────────┐  │
│  │   ERP / Billing System  │   │   Banking & Payment Rail │   │  Company Context     │  │
│  │  (Invoices, Due Dates,  │   │   (NEFT, RTGS, UPI,      │   │  (LTV, Balance Trend,│  │
│  │   Amounts, Overdue Days)│   │    NACH Failure Codes)   │   │   Disputes, AP Info) │  │
│  └────────────┬────────────┘   └────────────┬─────────────┘   └──────────┬───────────┘  │
│               │                             │                            │              │
│               └─────────────────────────────┼────────────────────────────┘              │
│                                             ▼                                           │
│                       ┌───────────────────────────────────────────┐                     │
│                       │          FASTAPI WEBHOOK INGESTION        │                     │
│                       │        POST /webhooks/b2b_invoice         │                     │
│                       └─────────────────────┬─────────────────────┘                     │
│                                             │                                           │
│                                             ▼                                           │
│                       ┌───────────────────────────────────────────┐                     │
│                       │       PostgreSQL (State & Audit DB)       │                     │
│                       │   • companies   • invoices                │                     │
│                       │   • raw_events  • cases                   │                     │
│                       └─────────────────────┬─────────────────────┘                     │
│                                             │                                           │
│                                             ▼                                           │
│  ┌───────────────────────────────────────────────────────────────────────────────────┐  │
│  │                           ORCHESTRATION PIPELINE                                  │  │
│  │                                                                                   │  │
│  │   [Step 1: DETECT]        Fetch unhandled raw events (FOR UPDATE SKIP LOCKED)     │  │
│  │          │                                                                        │  │
│  │   [Step 2: CONTEXT]       Fetch Invoice + Company Risk Profile & Payment History  │  │
│  │          │                                                                        │  │
│  │   [Step 3: DIAGNOSE]      Deterministic Rules ──► Gemini 1.5 Flash LLM Fallback   │  │
│  │          │                                                                        │  │
│  │   [Step 4: COMPLIANCE]    Policy Engine (RBI 2/wk Cap, NPCI UPI 3-Retries,        │  │
│  │          │                Plan Limits, Dispute/Freeze Hard-Stops)                 │  │
│  │          │                                                                        │  │
│  │   [Step 5: EXECUTE]       Email Dunning | Plan Generation | Slack RM Escalation   │  │
│  │          │                                                                        │  │
│  │   [Step 6: AUDIT]         Upsert Cases Table with Status & Overridden Reasonings   │  │
│  └──────────────────────────────────────────┬────────────────────────────────────────┘  │
│                                             │                                           │
│                                             ▼                                           │
│  ┌───────────────────────────────────────────────────────────────────────────────────┐  │
│  │                       LIVE WEB DASHBOARD & REST API                               │  │
│  │   • GET /dashboard          Interactive HTML5/CSS3 Dashboard                      │  │
│  │   • GET /dashboard/stats    Financial KPI Aggregations (₹ At Risk, Recovered)     │  │
│  │   • GET /dashboard/cases    Case Feed & Reasoning Audit Log                       │  │
│  │   • POST /admin/process     Manual Orchestrator Pipeline Trigger                  │  │
│  └───────────────────────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 🛡️ Regulatory Compliance & Policy Engine

The **Policy Engine** (`app/policy_engine.py`) acts as the single authoritative gatekeeper for compliance. Even if diagnosis suggests automated dunning, the policy engine intercepts and overrides actions when boundaries are met:

| Rule / Regulation | Condition | Enforced Policy Action |
| :--- | :--- | :--- |
| **Dispute Flag** | `company.dispute_flag == True` | `halt` (0 automated contact permitted, notify RM) |
| **Willful Default** | `company.willful_default == True` | `halt` (Immediate freeze on dunning, credit risk review) |
| **Account Frozen** | `company.account_frozen == True` | `rm_handoff` (Mandatory RM check before action) |
| **RBI Contact Cap** | ≥ 2 contacts in last 7 days | `rm_handoff` (`contact_cap_reached`) |
| **NPCI UPI Autopay** | `rail == 'UPI'` and retries ≥ 3 | `rm_handoff` (`max_retries_exhausted`) |
| **Plan Auto-Approve**| Overdue > 60 days OR installments > 2 | `rm_handoff` (`plan_exceeds_auto_approve`) |

---

## 🛠️ Tech Stack

- **Backend Framework**: [FastAPI](https://fastapi.tiangolo.com/) (Python 3.13) + [Uvicorn](https://www.uvicorn.org/)
- **Database Engine**: PostgreSQL with async connection pooling via [asyncpg](https://github.com/MagicStack/asyncpg)
- **AI / LLM Engine**: [Google Generative AI (Gemini 1.5 Flash)](https://ai.google.dev/)
- **Communication Channels**: SendGrid (Email), Slack SDK (RM Alerts), Twilio (SMS)
- **Frontend Dashboard**: Vanilla HTML5, Modern CSS Design System, Vanilla JS (Auto-polling every 10s)

---

## 📂 Project Structure

```text
Revenue-recovery-agent/
├── app/
│   ├── actions.py         # Multi-channel execution (Email, Slack alerts, Plan generation)
│   ├── db.py              # Async PostgreSQL pool lifecycle & auto-schema migration
│   ├── llm_client.py      # Google Gemini 1.5 Flash B2B diagnosis with mock fallbacks
│   ├── main.py            # FastAPI routes, webhooks, lifespan background worker & dashboard
│   ├── orchestrator.py    # 6-step recovery pipeline with concurrency locking & workers
│   ├── policy_engine.py   # RBI Fair Practices Code & NPCI compliance guardrails
│   ├── poller.py          # Background ERP polling integration template
│   ├── schemas.py         # Pydantic data schemas
│   └── seed_data.py       # Database schema initialization & demo dataset seeder
├── static/
│   ├── css/
│   │   └── style.css      # Premium dark-mode dashboard styling
│   ├── js/
│   │   └── app.js         # Real-time dashboard KPI & case feed updater
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
- *(Optional)* `GEMINI_API_KEY`, `SLACK_WEBHOOK_URL`, `SENDGRID_API_KEY`

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
DRY_RUN=true
```

> **Note**: Setting `DRY_RUN=true` enables safe simulation of emails, plan document creation, and Slack alerts without sending live traffic.

### 5. Seed Database Schema & Demo Records

Initialize tables and seed sample B2B companies, invoices, and failure events:

```bash
python -m app.seed_data
```

### 6. Start the FastAPI Application

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Access the dashboard at: **[http://localhost:8000/dashboard](http://localhost:8000/dashboard)**

---

## 📡 API Reference

### Health & Core
- `GET /` — Health check endpoint.
- `GET /dashboard` — Web Dashboard UI.

### Webhook Ingestion
- `POST /webhooks/b2b_invoice` — Ingest payment failures and overdue invoices from ERP or payment rails.
  ```json
  {
    "invoice_id": "INV-101",
    "company_id": "comp_001",
    "amount": 250000,
    "due_date": "2026-08-01T00:00:00",
    "payment_rail": "NEFT",
    "failure_code": "insufficient_funds"
  }
  ```

### Orchestrator & Dashboard
- `POST /admin/process` — Manually triggers event processing and scheduled case follow-ups.
- `GET /dashboard/stats` — Returns financial recovery stats:
  ```json
  {
    "total_cases": 4,
    "at_risk": 780000.0,
    "recovered": 0.0,
    "promised": 0.0,
    "escalated": 0.0
  }
  ```
- `GET /dashboard/cases?limit=20` — Retrieves recent case feeds, statuses, actions, and audit logs.

---

## 🧪 Testing with cURL / PowerShell

```powershell
# Trigger the orchestrator pipeline (Windows PowerShell)
curl.exe -X POST http://localhost:8000/admin/process

# Fetch Dashboard KPI Stats
curl.exe http://localhost:8000/dashboard/stats

# Fetch Case Records
curl.exe http://localhost:8000/dashboard/cases
```

---

## 🏆 Razorpay Build-a-thon Submission

This solution tackles the multi-billion dollar problem of B2B payment failure & involuntary receivables delays. By aligning payment rail intelligence with strict regulatory standards (RBI Fair Practices & NPCI UPI rules), it ensures high recovery rates without compliance risks.