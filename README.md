# 💰 Revenue Recovery Agent `[Buildathon]`

[![Buildathon](https://img.shields.io/badge/Project-Razorpay%20Buildathon-blueviolet?style=for-the-badge&logo=razorpay)](https://razorpay.com)
[![Python](https://img.shields.io/badge/Python-3.13-blue?style=for-the-badge&logo=python)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115.6-009688?style=for-the-badge&logo=fastapi)](https://fastapi.tiangolo.com)
[![Gemini](https://img.shields.io/badge/AI-Google%20Gemini-orange?style=for-the-badge&logo=google)](https://ai.google.dev)

> 🚀 **[Buildathon] Project Submission**  
> This project was developed and built for the **Razorpay Buildathon Challenge**. It is an autonomous, AI-driven Revenue Recovery & Intelligent Dunning Agent engineered to detect payment failures, diagnose root causes with rich customer context, decide optimal recovery strategies using a hybrid Rules + Gemini LLM engine, execute multi-channel communications, and prevent involuntary churn.

---

## 🌟 Key Highlights & Innovations

- 🧠 **Context-Enriched AI Diagnosis (Gemini 1.5 Flash)**: Incorporates real-time customer context (LTV, Plan, Segment, 3-Month Failure History, Repeat Offender status) to personalize recovery paths.
- ⚡ **Rules-First + LLM Fallback (Hybrid Architecture)**: Fast, deterministic rule resolution for known error codes (`insufficient_funds`, `card_expired`, `suspected_fraud`) with instant fallback to Gemini LLM for rare/complex failure patterns.
- 🛡️ **Adaptive Policy Engine & Safety Guardrails**: Dynamic retry limits (5 retries for High-LTV, 1 retry for Free Trial) and automated human escalation for high-risk or high-value anomalies.
- 📲 **Multi-Channel Orchestration**: Generates smart payment recovery links with automated Email (SendGrid), SMS (Twilio), and internal Slack escalations for human review.
- 🔄 **Async State Machine & Background Workers**: Event ingestion, automated retry scheduling, and state audits backed by PostgreSQL (`asyncpg`) and background pollers.

---

## 🏗️ Architecture Diagram

```text
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│                                     DATA SOURCES                                        │
│  ┌─────────────┐  ┌──────────────┐  ┌──────────────┐  ┌─────────────────────────────┐   │
│  │ PSP Webhooks│  │ Billing Sys  │  │  CRM / AR    │  │  Customer Contact History   │   │
│  │ (Stripe/    │  │ (Overdue     │  │  (Salesforce,│  │  (Email, Phone, LTV,        │   │
│  │  Razorpay)  │  │  Invoices)   │  │  HubSpot)    │  │  Segment, Plan)             │   │
│  └──────┬──────┘  └──────┬───────┘  └──────┬───────┘  └───────────────┬─────────────┘   │
│         │                │                 │                          │                 │
│         └────────────────┼─────────────────┼──────────────────────────┘                 │
│                          │                 │                                            │
│                          ▼                 ▼                                            │
│                  ┌─────────────────────────────────────┐                                │
│                  │         FASTAPI WEBHOOK SERVER      │                                │
│                  │   /webhooks/psp   /webhooks/billing │                                │
│                  └──────────────────┬──────────────────┘                                │
│                                     │                                                   │
│                                     ▼                                                   │
│                  ┌─────────────────────────────────────┐                                │
│                  │       PostgreSQL (State Database)    │                               │
│                  │  ┌─────────────┐  ┌───────────────┐ │                                │
│                  │  │ raw_events  │  │    cases      │ │                                │
│                  │  │ (ingested   │  │ (state        │ │                                │
│                  │  │  webhooks)  │  │  machine)     │ │                                │
│                  │  └─────────────┘  └───────────────┘ │                                │
│                  │  ┌───────────────────────────────┐  │                                │
│                  │  │  customers (LTV, segment,     │  │                                │
│                  │  │  plan, contact info)          │  │                                │
│                  │  └───────────────────────────────┘  │                                │
│                  └──────────────────┬──────────────────┘                                │
│                                     │                                                   │
│                                     ▼                                                   │
│  ┌──────────────────────────────────────────────────────────────────────────────────┐   │
│  │                       AGENT CORE (Orchestrator)                                  │   │
│  │  ┌────────────────────────────────────────────────────────────────────────────┐ │   │
│  │  │  STEP 1: DETECT → Background worker (every 30s) picks unprocessed events  │ │   │
│  │  │  STEP 2: FETCH CONTEXT → LTV, Segment, Failure History                    │ │   │
│  │  │  STEP 3: DIAGNOSE → Rules-First + Gemini LLM (for unknown errors)         │ │   │
│  │  │  STEP 4: DECIDE → Policy Engine applies caps, max retries, opt-outs       │ │   │
│  │  │  STEP 5: EXECUTE → Stripe link, Email (SendGrid), SMS (Twilio), Slack     │ │   │
│  │  │  STEP 6: AUDIT → Update cases table with status, reasoning, schedule      │ │   │
│  │  │  STEP 7: LOOP → If retry scheduled, re-enter at STEP 3 after delay        │ │   │
│  │  └────────────────────────────────────────────────────────────────────────────┘ │   │
│  └──────────────────────────────────────────────────────────────────────────────────┘   │
│                                     │                                                   │
│                                     ▼                                                   │
│  ┌──────────────────────────────────────────────────────────────────────────────────┐   │
│  │                         ACTION CHANNELS                                          │   │
│  │  ┌────────────┐  ┌────────────┐  ┌────────────┐  ┌────────────┐                  │   │
│  │  │  Stripe /  │  │  SendGrid  │  │   Twilio   │  │   Slack    │                  │   │
│  │  │  Razorpay  │  │  (Email)   │  │  (SMS)     │  │  (Human    │                  │   │
│  │  │  (PayLink) │  │            │  │            │  │   Handoff) │                  │   │
│  │  └────────────┘  └────────────┘  └────────────┘  └────────────┘                  │   │
│  └──────────────────────────────────────────────────────────────────────────────────┘   │
│                                     │                                                   │
│                                     ▼                                                   │
│  ┌──────────────────────────────────────────────────────────────────────────────────┐   │
│  │                     DASHBOARD + AUDIT LOG                                        │   │
│  │  ┌─────────────────────────────┐  ┌───────────────────────────────────────────┐  │   │
│  │  │  /dashboard/stats (API)     │  │  /dashboard (HTML + Chart.js)             │  │   │
│  │  │  - $ At Risk                │  │  - KPI Cards                              │  │   │
│  │  │  - $ Recovered              │  │  - Case Feed                              │  │   │
│  │  │  - Recovery Rate            │  │  - Audit Trail Timeline                   │  │   │
│  │  │  - Escalated Count          │  │  - Auto-refresh every 10s                 │  │   │
│  │  └─────────────────────────────┘  └───────────────────────────────────────────┘  │   │
│  └──────────────────────────────────────────────────────────────────────────────────┘   │
│                                                                                         │
└─────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 📋 Features & Customer Segmentation

| Segment | LTV Threshold / Plan | Strategy | Max Retries |
| :--- | :--- | :--- | :--- |
| **High LTV** | > $5,000 / ₹4,00,000 | Priority 72-hour payday retry, white-glove email, concierge recovery link | Up to 5 |
| **Standard** | Regular Monthly / Annual | 24-48h automated recovery with email/SMS payment links | Up to 3 |
| **Free Trial** | Trial Plan | 1 gentle retry in 4 hours; no spam on subsequent failure | 1 |
| **Suspected Fraud / Dispute** | Any | Immediate Slack escalation to human operations team | 0 (Immediate Escalate) |

---

## 🛠️ Tech Stack

- **Framework**: [FastAPI](https://fastapi.tiangolo.com/) + [Uvicorn](https://www.uvicorn.org/)
- **AI / LLM**: [Google Generative AI (Gemini 1.5 Flash)](https://ai.google.dev/)
- **Database**: PostgreSQL with high-performance asynchronous [asyncpg](https://github.com/MagicStack/asyncpg)
- **Payment Providers**: Razorpay & Stripe Webhook & Payment Link APIs
- **Notifications**: SendGrid (Email), Twilio (SMS), Slack SDK (Human Handoff)
- **Validation**: Pydantic v2 & Pydantic Settings

---

## 📂 Project Structure

```text
Revenue-recovery-agent/
├── app/
│   ├── actions.py         # Multi-channel actions (Payment links, Email, SMS, Slack)
│   ├── db.py              # Async PostgreSQL connection pool management
│   ├── llm_client.py      # Google Gemini client with enriched context prompts
│   ├── main.py            # FastAPI entrypoint, webhooks, and live dashboard
│   ├── orchestrator.py    # Core state machine (Detect → Diagnose → Decide → Execute)
│   ├── poller.py          # Background polling for scheduled retries
│   ├── schemas.py         # Pydantic models and request validation
│   └── seed_data.py       # Database schema initialization & mock data seeder
├── .env.example           # Environment variable template
├── .gitignore             # Git ignore rules (protects credentials and virtual env)
├── requirements.txt       # Project dependencies
└── README.md              # Documentation
```

---

## 🚀 Getting Started

### 1. Prerequisites
- Python 3.10+ (Tested on Python 3.13)
- PostgreSQL database instance
- (Optional) API keys for Google Gemini, SendGrid, Twilio, Slack, Stripe/Razorpay

### 2. Clone the Repository
```bash
git clone https://github.com/Sudha2050/AI-Revenue-Recovery-.git
cd AI-Revenue-Recovery-
```

### 3. Create & Activate Virtual Environment
```bash
# Windows
python -m venv venv
.\venv\Scripts\activate

# Linux / macOS
python3 -m venv venv
source venv/bin/activate
```

### 4. Install Dependencies
```bash
pip install -r requirements.txt
```

### 5. Configure Environment Variables
Copy `.env.example` to `.env` and fill in your configuration:
```bash
cp .env.example .env
```
Example `.env`:
```ini
DATABASE_URL=postgresql://postgres:password@localhost:5432/revenue_db
GEMINI_API_KEY=your_gemini_api_key
SENDGRID_API_KEY=your_sendgrid_key
STRIPE_API_KEY=your_stripe_key
STRIPE_WEBHOOK_SECRET=your_webhook_secret
DRY_RUN=true
```

### 6. Initialize Database & Seed Data
```bash
python -m app.seed_data
```

### 7. Run the Application
```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```
Open your browser at `http://localhost:8000` to access the Revenue Recovery dashboard and health check.

---

## 📡 API Endpoints

- `GET /` — Health check and agent status
- `POST /webhooks/psp` — Ingest payment failure webhooks (Stripe / Razorpay)
- `GET /api/cases` — Retrieve all active and resolved recovery cases
- `POST /api/cases/{case_id}/retry` — Manually trigger an immediate retry

---

## 🏆 Razorpay Build-a-thon Submission

This project addresses involuntary payment failure recovery in the Indian and global payment ecosystem, combining payment intelligence with real-world dunning communication channels to protect MRR and maximize customer retention.