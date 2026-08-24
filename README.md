# 💰 Revenue Recovery Agent

> **Built for the Build-a-thon Challenge by Razorpay** 🚀

An autonomous, AI-driven Revenue Recovery & Intelligent Dunning Agent designed to detect payment failures, diagnose root causes with customer context, decide optimal recovery strategies using a hybrid Rules + Gemini LLM engine, execute multi-channel communications, and prevent involuntary churn.

---

## 🌟 Key Highlights & Innovations

- 🧠 **Context-Enriched AI Diagnosis (Gemini 1.5 Flash)**: Incorporates real-time customer context (LTV, Plan, Segment, 3-Month Failure History, Repeat Offender status) to personalize recovery paths.
- ⚡ **Rules-First + LLM Fallback (Hybrid Architecture)**: Fast, deterministic rule resolution for known error codes (`insufficient_funds`, `card_expired`, `suspected_fraud`) with instant fallback to Gemini LLM for rare/complex failure patterns.
- 🛡️ **Adaptive Policy Engine & Safety Guardrails**: Dynamic retry limits (5 retries for High-LTV, 1 retry for Free Trial) and automated human escalation for high-risk or high-value anomalies.
- 📲 **Multi-Channel Orchestration**: Generates smart payment recovery links with automated Email (SendGrid), SMS (Twilio), and internal Slack escalations for human review.
- 🔄 **Async State Machine & Background Workers**: Event ingestion, automated retry scheduling, and state audits backed by PostgreSQL (`asyncpg`) and background pollers.

---

## 🏗️ Architecture & Workflow

```mermaid
graph TD
    A[Payment Failure Webhook / Razorpay / Stripe] --> B[1. Ingest & Detect: raw_events]
    B --> C[2. Fetch Customer Context: CRM + 3-Month History]
    C --> D{3. Hybrid Diagnosis Engine}
    D -->|Deterministic Rules| E[Rule Outcome]
    D -->|Complex / Unknown| F[Gemini LLM with Context]
    E --> G[4. Policy Engine & Guardrails]
    F --> G
    G --> H{5. Multi-Channel Execution}
    H -->|Retry / Payment Link| I[Stripe / Razorpay Smart Link]
    H -->|Customer Notice| J[SendGrid Email / Twilio SMS]
    H -->|Risk / Cap Exceeded| K[Slack Escalation to Human Agent]
    I --> L[6. State Machine & Audit in PostgreSQL]
    J --> L
    K --> L
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
git clone <YOUR_REPO_URL>
cd Revenue-recovery-agent
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
