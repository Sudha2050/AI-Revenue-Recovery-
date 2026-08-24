# app/main.py
import json
import asyncio
from datetime import datetime, timedelta
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse

from app.db import init_db, get_pool
from app.orchestrator import process_pending_events, process_scheduled_cases


# --- Lifespan Manager (Startup / Shutdown) ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Initialize DB and start background workers
    await init_db()
    
    async def background_worker():
        print("🔄 Background Orchestrator started. Checking for new events every 30 seconds...")
        while True:
            try:
                await process_pending_events()
                await process_scheduled_cases()
            except Exception as e:
                print(f"⚠️ Background worker error: {e}")
            await asyncio.sleep(30)  # Run every 30 seconds
    
    # Start the background task
    task = asyncio.create_task(background_worker())
    
    yield  # Server runs here
    
    # Shutdown: Clean up
    task.cancel()
    print("🛑 Background Orchestrator stopped.")


# --- FastAPI App ---
app = FastAPI(
    title="Revenue Recovery Agent",
    version="1.0.0",
    description="AI-powered revenue recovery with Stripe, Email, Slack, and Dashboard.",
    lifespan=lifespan
)


# --- Health Check ---
@app.get("/")
async def root():
    return {
        "message": "Revenue Recovery Agent is running!",
        "status": "healthy",
        "version": "1.0.0"
    }


# --- 1. Webhook: PSP (Stripe / Razorpay) ---
@app.post("/webhooks/psp")
async def psp_webhook(request: Request):
    """Endpoint for Stripe/Razorpay payment failure webhooks."""
    try:
        raw_body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    
    # Extract data (Stripe format example)
    event_type = raw_body.get('type', 'unknown')
    customer_id = raw_body.get('data', {}).get('object', {}).get('customer')
    
    if not customer_id:
        # Try Razorpay format
        customer_id = raw_body.get('payload', {}).get('payment', {}).get('entity', {}).get('customer_id')
    
    if not customer_id:
        raise HTTPException(status_code=400, detail="Customer ID missing in webhook")
    
    # Extract amount (Stripe amount is in cents)
    amount = raw_body.get('data', {}).get('object', {}).get('amount', 0) / 100
    failure_code = raw_body.get('data', {}).get('object', {}).get('failure_code')
    failure_message = raw_body.get('data', {}).get('object', {}).get('failure_message')
    
    event_id = raw_body.get('id', f"webhook_{int(datetime.utcnow().timestamp())}")
    
    canonical_event = {
        "event_id": event_id,
        "customer_id": customer_id,
        "event_type": "payment_failed" if "fail" in event_type.lower() else "payment_declined",
        "amount_usd": amount,
        "currency": raw_body.get('data', {}).get('object', {}).get('currency', 'USD'),
        "raw_error_code": failure_code,
        "raw_error_message": failure_message
    }
    
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO raw_events (event_id, event_type, customer_id, payload, canonical_event) 
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (event_id) DO NOTHING
            """,
            event_id,
            canonical_event["event_type"],
            customer_id,
            json.dumps(raw_body),
            json.dumps(canonical_event)
        )
    
    return {"status": "ingested", "event_id": event_id}


# --- 2. Webhook: Internal Billing System (AR / Invoicing) ---
@app.post("/webhooks/billing")
async def billing_webhook(request: Request):
    """Endpoint for internal Billing System (overdue invoices)."""
    try:
        raw_body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    
    customer_id = raw_body.get('customer_id')
    invoice_id = raw_body.get('invoice_id')
    amount = raw_body.get('amount_due', 0)
    days_overdue = raw_body.get('days_overdue', 0)
    
    if not customer_id or not invoice_id:
        raise HTTPException(status_code=400, detail="customer_id and invoice_id are required")
    
    canonical_event = {
        "event_id": f"inv_{invoice_id}",
        "customer_id": customer_id,
        "event_type": "invoice_overdue",
        "amount_usd": amount,
        "currency": "USD",
        "raw_error_code": None,
        "raw_error_message": f"Invoice overdue by {days_overdue} days"
    }
    
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO raw_events (event_id, event_type, customer_id, payload, canonical_event) 
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (event_id) DO NOTHING
            """,
            canonical_event["event_id"],
            canonical_event["event_type"],
            customer_id,
            json.dumps(raw_body),
            json.dumps(canonical_event)
        )
    
    return {"status": "ingested"}


# --- 3. Admin: Manually Trigger Orchestrator ---
@app.post("/admin/process")
async def manual_process():
    """Manually trigger the orchestrator to process pending events."""
    await process_pending_events()
    await process_scheduled_cases()
    return {"status": "processing_triggered"}


# --- 4. Dashboard API: Stats ---
@app.get("/dashboard/stats")
async def dashboard_stats():
    """Returns real-time revenue recovery statistics."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        stats = await conn.fetchrow("""
            SELECT 
                COUNT(*) AS total_cases,
                COALESCE(SUM(CASE WHEN status IN ('new', 'diagnosing', 'retrying', 'awaiting_input') THEN amount_usd ELSE 0 END), 0) AS at_risk,
                COALESCE(SUM(CASE WHEN status = 'resolved' THEN amount_usd ELSE 0 END), 0) AS recovered,
                COALESCE(SUM(CASE WHEN status = 'escalated' THEN amount_usd ELSE 0 END), 0) AS escalated,
                COALESCE(ROUND((SUM(CASE WHEN status = 'resolved' THEN amount_usd ELSE 0 END) / 
                       NULLIF(SUM(CASE WHEN status IN ('new', 'diagnosing', 'retrying', 'awaiting_input', 'resolved', 'escalated') THEN amount_usd ELSE 0 END), 0) * 100), 2), 0) AS recovery_rate
            FROM cases
        """)
        return dict(stats)


# --- 5. Dashboard API: Recent Cases ---
@app.get("/dashboard/cases")
async def dashboard_cases(limit: int = 20):
    """Returns recent cases for the dashboard feed."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        cases = await conn.fetch("""
            SELECT 
                case_id,
                customer_id,
                case_type,
                status,
                amount_usd,
                last_action,
                llm_reasoning,
                created_at,
                updated_at
            FROM cases
            ORDER BY updated_at DESC
            LIMIT $1
        """, limit)
        return [dict(case) for case in cases]


# --- 6. Dashboard: HTML Page ---
@app.get("/dashboard")
async def dashboard_page():
    """Serves the beautiful HTML dashboard connected to real data."""
    html_content = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Ledger — Revenue Recovery Agent</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,400;9..144,500;9..144,600&family=Inter:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
  :root{
    --paper:#F5F3EC;
    --paper-2:#EFEBDF;
    --card:#FFFFFF;
    --ink:#1C2321;
    --ink-soft:#5B6360;
    --hair:#DAD5C6;
    --hair-strong:#C6C0AC;
    --emerald:#0B6E4F;
    --emerald-100:#DCEBE3;
    --amber:#9C6B1F;
    --amber-100:#F3E6CC;
    --brick:#8C2F2F;
    --brick-100:#F1DCDA;
    --ledger:#2B4C7E;
    --ledger-100:#DCE4EF;
    --mono:'IBM Plex Mono',monospace;
    --serif:'Fraunces',serif;
    --sans:'Inter',sans-serif;
  }
  *{box-sizing:border-box;}
  body{
    margin:0;
    background:var(--paper);
    color:var(--ink);
    font-family:var(--sans);
    -webkit-font-smoothing:antialiased;
  }
  .wrap{max-width:1180px;margin:0 auto;padding:36px 28px 80px;}

  header{
    display:flex;justify-content:space-between;align-items:flex-end;
    border-bottom:1.5px solid var(--ink);
    padding-bottom:18px;margin-bottom:26px;
    gap:20px;flex-wrap:wrap;
  }
  .brand-eyebrow{
    font-family:var(--mono);font-size:11px;letter-spacing:.14em;text-transform:uppercase;
    color:var(--ink-soft);margin-bottom:6px;
  }
  h1{
    font-family:var(--serif);font-weight:500;font-size:34px;margin:0;
    letter-spacing:-.01em;
  }
  .run-controls{display:flex;gap:10px;align-items:center;}
  button.run{
    font-family:var(--sans);font-size:13px;padding:9px 14px;border-radius:3px;
    border:1px solid var(--ink);background:var(--ink);color:var(--paper);cursor:pointer;
    font-weight:500;
  }
  button.run:disabled{opacity:.4;cursor:default;}
  button.run:hover:not(:disabled){background:#333c39;}

  .ledger-strip{
    display:grid;grid-template-columns:repeat(5,1fr);
    border:1px solid var(--hair-strong);border-radius:4px;overflow:hidden;
    background:var(--card);margin-bottom:28px;
  }
  .metric{padding:16px 18px;border-right:1px solid var(--hair);}
  .metric:last-child{border-right:none;}
  .metric-label{font-family:var(--mono);font-size:10.5px;text-transform:uppercase;letter-spacing:.08em;color:var(--ink-soft);margin-bottom:8px;}
  .metric-value{font-family:var(--serif);font-size:26px;font-weight:500;}
  .metric-value.emerald{color:var(--emerald);}
  .metric-value.brick{color:var(--brick);}
  .metric-sub{font-family:var(--mono);font-size:11px;color:var(--ink-soft);margin-top:4px;}

  .grid{display:grid;grid-template-columns:1.15fr 1fr;gap:24px;align-items:start;}
  @media (max-width:960px){.grid{grid-template-columns:1fr;}}

  .panel{background:var(--card);border:1px solid var(--hair-strong);border-radius:4px;}
  .panel-head{
    display:flex;justify-content:space-between;align-items:center;
    padding:13px 16px;border-bottom:1px solid var(--hair);
  }
  .panel-head h2{font-family:var(--serif);font-size:16px;font-weight:500;margin:0;}
  .panel-head .count{font-family:var(--mono);font-size:11px;color:var(--ink-soft);}

  .feed{max-height:640px;overflow-y:auto;}
  .case-row{
    padding:12px 16px;border-bottom:1px solid var(--hair);
    display:grid;grid-template-columns:auto 1fr auto;gap:12px;align-items:center;
    cursor:pointer;position:relative;
  }
  .case-row:hover{background:var(--paper-2);}
  .case-row.selected{background:var(--ledger-100);}
  .case-id{font-family:var(--mono);font-size:11px;color:var(--ink-soft);white-space:nowrap;}
  .case-main .case-cust{font-size:13.5px;font-weight:500;}
  .case-main .case-meta{font-family:var(--mono);font-size:11px;color:var(--ink-soft);margin-top:2px;}
  .amount{font-family:var(--mono);font-size:13px;text-align:right;white-space:nowrap;}
  .stamp{
    font-family:var(--mono);font-weight:600;font-size:10.5px;letter-spacing:.09em;
    padding:3px 8px;border-radius:2px;border:1.4px solid;transform:rotate(-3deg);
    display:inline-block;text-transform:uppercase;
    animation:stampIn .28s cubic-bezier(.2,1.6,.4,1);
  }
  @media (prefers-reduced-motion:reduce){.stamp{animation:none;}}
  @keyframes stampIn{from{opacity:0;transform:rotate(-3deg) scale(1.7);}to{opacity:1;transform:rotate(-3deg) scale(1);}}
  .stamp.recovered{color:var(--emerald);border-color:var(--emerald);background:var(--emerald-100);}
  .stamp.promised{color:var(--amber);border-color:var(--amber);background:var(--amber-100);}
  .stamp.escalated{color:var(--ledger);border-color:var(--ledger);background:var(--ledger-100);}
  .stamp.stopped{color:var(--brick);border-color:var(--brick);background:var(--brick-100);}
  .stamp.lost{color:var(--ink-soft);border-color:var(--hair-strong);background:var(--paper-2);}
  .stamp.retrying{color:#92400e;border-color:#f59e0b;background:#fef3c7;}
  .pending-tag{font-family:var(--mono);font-size:10.5px;color:var(--ink-soft);}

  .drawer{padding:16px;font-size:13px;}
  .drawer-empty{padding:40px 16px;text-align:center;color:var(--ink-soft);font-size:13px;}
  .drawer h3{font-family:var(--serif);font-size:18px;font-weight:500;margin:0 0 4px;}
  .drawer .sub{font-family:var(--mono);font-size:11px;color:var(--ink-soft);margin-bottom:16px;}
  .kv{display:grid;grid-template-columns:120px 1fr;gap:6px 10px;font-size:12.5px;margin-bottom:16px;}
  .kv div:nth-child(odd){color:var(--ink-soft);font-family:var(--mono);font-size:11px;text-transform:uppercase;letter-spacing:.04em;padding-top:1px;}
  .timeline{border-left:2px solid var(--hair-strong);margin-left:6px;padding-left:16px;}
  .tl-item{position:relative;padding-bottom:16px;}
  .tl-item:last-child{padding-bottom:0;}
  .tl-item::before{content:'';position:absolute;left:-21px;top:3px;width:8px;height:8px;border-radius:50%;background:var(--ledger);border:2px solid var(--card);}
  .tl-item.stop::before{background:var(--brick);}
  .tl-item.win::before{background:var(--emerald);}
  .tl-time{font-family:var(--mono);font-size:10.5px;color:var(--ink-soft);}
  .tl-label{font-size:13px;font-weight:500;margin:2px 0;}
  .tl-detail{font-size:12.5px;color:var(--ink-soft);line-height:1.5;}
  .badge{display:inline-block;font-family:var(--mono);font-size:10px;padding:2px 6px;border-radius:2px;text-transform:uppercase;letter-spacing:.05em;margin-right:4px;}
  .badge.ledger{background:var(--ledger-100);color:var(--ledger);}
  .badge.brick{background:var(--brick-100);color:var(--brick);}
  .badge.emerald{background:var(--emerald-100);color:var(--emerald);}
  .badge.amber{background:var(--amber-100);color:var(--amber);}

  .footnote{font-family:var(--mono);font-size:11px;color:var(--ink-soft);margin-top:22px;line-height:1.6;}
  .footnote a{color:var(--ledger);}

  ::-webkit-scrollbar{width:9px;}
  ::-webkit-scrollbar-thumb{background:var(--hair-strong);border-radius:5px;}
</style>
</head>
<body>
<div class="wrap">

  <header>
    <div>
      <div class="brand-eyebrow">Revenue recovery agent — live console</div>
      <h1>Ledger</h1>
    </div>
    <div class="run-controls">
      <button class="run" id="refreshBtn">🔄 Refresh</button>
    </div>
  </header>

  <div class="ledger-strip" id="metricStrip">
    <div class="metric">
      <div class="metric-label">Revenue at risk</div>
      <div class="metric-value" id="mAtRisk">$0</div>
      <div class="metric-sub" id="mCases">0 cases</div>
    </div>
    <div class="metric">
      <div class="metric-label">Recovered</div>
      <div class="metric-value emerald" id="mRecovered">$0</div>
      <div class="metric-sub" id="mRecoveredPct">0% of at-risk</div>
    </div>
    <div class="metric">
      <div class="metric-label">In Progress</div>
      <div class="metric-value" id="mInProgress">0</div>
      <div class="metric-sub">retrying / awaiting</div>
    </div>
    <div class="metric">
      <div class="metric-label">Escalated</div>
      <div class="metric-value" id="mEscalated">0</div>
      <div class="metric-sub" id="mEscalatedPct">0% of batch</div>
    </div>
    <div class="metric">
      <div class="metric-label">Recovery Rate</div>
      <div class="metric-value" id="mRecoveryRate">0%</div>
      <div class="metric-sub">of resolved cases</div>
    </div>
  </div>

  <div class="grid">
    <div class="panel">
      <div class="panel-head">
        <h2>Case feed</h2>
        <span class="count" id="feedCount">0 cases</span>
      </div>
      <div class="feed" id="feed">
        <div style="padding:40px;text-align:center;color:var(--ink-soft);">Loading cases...</div>
      </div>
    </div>

    <div style="display:flex;flex-direction:column;gap:24px;">
      <div class="panel">
        <div class="panel-head">
          <h2>Audit trail</h2>
          <span class="count">click a case</span>
        </div>
        <div id="drawer"><div class="drawer-empty">Select a case in the feed to see its full detect → diagnose → decide → execute → guardrail trail.</div></div>
      </div>
    </div>
  </div>

  <div class="footnote">
    Live data from your PostgreSQL database. Cases are processed by the AI Revenue Recovery Agent. 
    Stamps reflect actual statuses: <strong>Recovered</strong> (payment succeeded), 
    <strong>Escalated</strong> (human handoff), <strong>Retrying</strong> (scheduled), 
    <strong>Awaiting</strong> (customer action needed).
  </div>

</div>

<script>
const feedEl = document.getElementById('feed');
const drawerEl = document.getElementById('drawer');
let allCases = [];
let selectedId = null;

function money(n){ return '$' + Number(n).toFixed(2); }
function fmtDate(d){ return new Date(d).toLocaleString(); }

function statusToStamp(status){
  const map = {
    'resolved': 'recovered',
    'escalated': 'escalated',
    'retrying': 'retrying',
    'awaiting_input': 'promised',
    'new': 'promised',
    'diagnosing': 'promised',
    'closed': 'lost'
  };
  return map[status] || 'lost';
}

function statusLabel(status){
  const map = {
    'resolved': 'Recovered',
    'escalated': 'Escalated',
    'retrying': 'Retrying',
    'awaiting_input': 'Awaiting',
    'new': 'New',
    'diagnosing': 'Diagnosing',
    'closed': 'Closed'
  };
  return map[status] || status;
}

function typeLabel(type){
  const map = {
    'payment_failed': 'Payment decline',
    'payment_declined': 'Payment decline',
    'invoice_overdue': 'Invoice overdue',
    'subscription_canceled': 'Subscription fail',
    'cart_abandoned': 'Checkout abandon'
  };
  return map[type] || type;
}

async function loadData(){
  try {
    const statsRes = await fetch('/dashboard/stats');
    const stats = await statsRes.json();
    
    document.getElementById('mAtRisk').textContent = money(stats.at_risk || 0);
    document.getElementById('mCases').textContent = stats.total_cases + ' cases';
    document.getElementById('mRecovered').textContent = money(stats.recovered || 0);
    document.getElementById('mRecoveredPct').textContent = (stats.recovered && stats.at_risk) ? ((stats.recovered / stats.at_risk) * 100).toFixed(1) + '%' : '0%';
    document.getElementById('mInProgress').textContent = (stats.total_cases - stats.recovered - stats.escalated) || 0;
    document.getElementById('mEscalated').textContent = stats.escalated || 0;
    document.getElementById('mEscalatedPct').textContent = stats.total_cases ? ((stats.escalated / stats.total_cases) * 100).toFixed(1) + '%' : '0%';
    document.getElementById('mRecoveryRate').textContent = (stats.recovery_rate || 0) + '%';

    const casesRes = await fetch('/dashboard/cases?limit=20');
    const cases = await casesRes.json();
    allCases = cases;
    document.getElementById('feedCount').textContent = cases.length + ' cases';
    
    feedEl.innerHTML = '';
    if (cases.length === 0) {
      feedEl.innerHTML = '<div style="padding:40px;text-align:center;color:var(--ink-soft);">No cases found. Wait for webhooks or seed data.</div>';
      return;
    }

    cases.forEach(c => {
      const stamp = statusToStamp(c.status);
      const label = statusLabel(c.status);
      const row = document.createElement('div');
      row.className = 'case-row' + (c.case_id === selectedId ? ' selected' : '');
      row.dataset.id = c.case_id;
      row.innerHTML = `
        <div class="case-id">#${c.case_id}</div>
        <div class="case-main">
          <div class="case-cust">${c.customer_id} <span class="pending-tag">· ${typeLabel(c.case_type)}</span></div>
          <div class="case-meta">${c.llm_reasoning || 'Processing...'}</div>
        </div>
        <div style="text-align:right;">
          <div class="amount">${money(c.amount_usd)}</div>
          <div class="stamp ${stamp}">${label}</div>
        </div>`;
      row.addEventListener('click', ()=>selectCase(c.case_id));
      feedEl.prepend(row);
    });

    if (!selectedId && cases.length > 0) {
      selectCase(cases[0].case_id);
    }

  } catch(e) {
    console.error('Failed to load dashboard data:', e);
    feedEl.innerHTML = '<div style="padding:40px;text-align:center;color:var(--brick);">⚠️ Failed to connect to API. Make sure the server is running.</div>';
  }
}

function selectCase(id){
  selectedId = id;
  document.querySelectorAll('.case-row').forEach(r => r.classList.toggle('selected', parseInt(r.dataset.id) === id));
  const c = allCases.find(x => x.case_id === id);
  if (c) renderDrawer(c);
}

function renderDrawer(c){
  const stamp = statusToStamp(c.status);
  const label = statusLabel(c.status);
  
  const items = [
    { time: fmtDate(c.created_at), label: 'Detected', detail: `${typeLabel(c.case_type)} event ingested. Amount: ${money(c.amount_usd)}.`, cls: '' },
    { time: fmtDate(c.updated_at), label: 'Diagnosed', detail: c.llm_reasoning || 'Rules-based diagnosis applied.', cls: '' },
    { time: fmtDate(c.updated_at), label: 'Action Executed', detail: c.last_action || 'Awaiting action.', cls: '' },
  ];

  if (c.status === 'resolved') {
    items.push({ time: fmtDate(c.updated_at), label: '✅ Recovered', detail: `Payment succeeded. ${money(c.amount_usd)} recovered. Case closed.`, cls: 'win' });
  } else if (c.status === 'escalated') {
    items.push({ time: fmtDate(c.updated_at), label: '👨‍💼 Escalated', detail: `Case exceeded auto-resolve bounds. Full brief handed to human team.`, cls: 'stop' });
  } else if (c.status === 'retrying') {
    items.push({ time: fmtDate(c.updated_at), label: '⏳ Retry Scheduled', detail: c.last_action || 'Payment retry scheduled.', cls: '' });
  } else {
    items.push({ time: fmtDate(c.updated_at), label: '⏳ In Progress', detail: c.last_action || 'Awaiting customer action or next step.', cls: '' });
  }

  let tlHtml = items.map(t => `
    <div class="tl-item ${t.cls}">
      <div class="tl-time">${t.time}</div>
      <div class="tl-label">${t.label}</div>
      <div class="tl-detail">${t.detail}</div>
    </div>`).join('');

  drawerEl.innerHTML = `
    <div class="drawer">
      <h3>${c.customer_id}</h3>
      <div class="sub">#${c.case_id} · ${typeLabel(c.case_type)} · ${money(c.amount_usd)}</div>
      <div class="kv">
        <div>Status</div><div><span class="stamp ${stamp}" style="animation:none;">${label}</span></div>
        <div>LLM Reasoning</div><div>${c.llm_reasoning || 'Rules-based diagnosis'}</div>
        <div>Last Action</div><div>${c.last_action || 'None'}</div>
      </div>
      <div class="timeline">${tlHtml}</div>
    </div>`;
}

document.getElementById('refreshBtn').addEventListener('click', loadData);
loadData();
setInterval(loadData, 10000);
</script>
</body>
</html>
    """
    return HTMLResponse(content=html_content, status_code=200)