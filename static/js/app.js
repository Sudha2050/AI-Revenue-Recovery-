function formatMoney(n) {
  const num = Number(n) || 0;
  return '₹' + num.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

async function triggerProcess() {
  const btn = document.getElementById('processBtn');
  if (btn) {
    btn.disabled = true;
    btn.textContent = '⏳ Processing...';
  }
  try {
    await fetch('/admin/process', { method: 'POST' });
    await loadData();
  } catch (err) {
    console.error('Failed to trigger orchestrator:', err);
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = '⚡ Run Orchestrator';
    }
  }
}

async function handleSimulateReply(e) {
  e.preventDefault();
  const invoiceId = document.getElementById('simInvoiceId').value.trim();
  const message = document.getElementById('simMessage').value.trim();
  const channel = document.getElementById('simChannel').value;
  const feedback = document.getElementById('simFeedback');
  const btn = document.getElementById('simSubmitBtn');

  if (!invoiceId || !message) return;

  btn.disabled = true;
  btn.textContent = 'Analyzing...';
  feedback.style.display = 'block';
  feedback.style.color = '#94A3B8';
  feedback.textContent = '🧠 Analyzing customer response intent with NLP engine...';

  try {
    const endpoint = channel === 'whatsapp' ? '/webhooks/whatsapp' : '/webhooks/customer_response';
    const res = await fetch(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ invoice_id: invoiceId, message: message, channel: channel })
    });
    const data = await res.json();
    if (res.ok) {
      const intentLabel = (data.intent || 'processed').toUpperCase().replace('_', ' ');
      feedback.style.color = '#10B981';
      feedback.innerHTML = `✅ <strong>Intent: ${intentLabel}</strong> | New Status: <code>${data.status}</code> ${data.promised_date ? `| Promised Date: ${data.promised_date.slice(0,10)}` : ''}<br><span style="color:#CBD5E1;">💬 Reply: "${data.suggested_reply || ''}"</span>`;
      await loadData();
    } else {
      feedback.style.color = '#EF4444';
      feedback.textContent = `❌ Error: ${data.detail || data.message || 'Processing failed'}`;
    }
  } catch (err) {
    feedback.style.color = '#EF4444';
    feedback.textContent = `❌ Error: ${err.message}`;
  } finally {
    btn.disabled = false;
    btn.textContent = '🧠 Classify & Process';
  }
}

async function loadData() {
  try {
    const statsRes = await fetch('/dashboard/stats');
    const stats = await statsRes.json();
    
    document.getElementById('atRisk').innerText = formatMoney(stats.at_risk);
    document.getElementById('recovered').innerText = formatMoney(stats.recovered);
    document.getElementById('promised').innerText = formatMoney(stats.promised);
    document.getElementById('escalated').innerText = stats.escalated || 0;

    const casesRes = await fetch('/dashboard/cases?limit=20');
    const cases = await casesRes.json();
    const tbody = document.getElementById('caseFeed');
    
    if (!cases || cases.length === 0) {
      tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;color:#64748B;padding:30px;">No cases found yet. Ingest an invoice webhook or trigger the orchestrator.</td></tr>';
      return;
    }
    
    tbody.innerHTML = cases.map(c => {
      const status = c.status || 'new';
      let intentBadge = '<span style="color:#64748B;">Pending Reply</span>';
      if (c.customer_intent) {
        let color = '#3B82F6';
        if (c.customer_intent === 'dispute') color = '#EF4444';
        if (c.customer_intent === 'pay_now') color = '#10B981';
        if (c.customer_intent === 'promise_to_pay') color = '#8B5CF6';
        intentBadge = `<span style="background:${color}22; color:${color}; padding: 2px 8px; border-radius: 4px; font-weight:600; font-size:11px;">${c.customer_intent.replace('_', ' ')}</span>`;
        if (c.promised_date) {
          intentBadge += `<br><small style="color:#A78BFA; font-size:10px;">📅 Due: ${c.promised_date.slice(0,10)}</small>`;
        }
      }
      return `
        <tr>
          <td class="mono">#${c.case_id || '-'}</td>
          <td class="mono"><strong>${c.company_id || '-'}</strong></td>
          <td class="mono">${c.invoice_id || '-'}</td>
          <td class="mono"><strong>${formatMoney(c.amount)}</strong></td>
          <td>${intentBadge}</td>
          <td><span class="badge badge-${status}">${status.replace('_', ' ')}</span></td>
          <td><small>${c.last_action || 'Awaiting action...'}</small></td>
        </tr>
      `;
    }).join('');
  } catch (err) {
    console.error('Failed to load dashboard data:', err);
  }
}

document.getElementById('refreshBtn')?.addEventListener('click', loadData);
document.getElementById('processBtn')?.addEventListener('click', triggerProcess);
document.getElementById('replyForm')?.addEventListener('submit', handleSimulateReply);

loadData();
setInterval(loadData, 10000);
