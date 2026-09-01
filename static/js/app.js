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
    // 1. Recovery Stats
    const statsRes = await fetch('/dashboard/stats');
    if (statsRes.ok) {
      const stats = await statsRes.json();
      if (document.getElementById('totalCases')) document.getElementById('totalCases').innerText = stats.total_cases || 0;
      if (document.getElementById('atRisk')) document.getElementById('atRisk').innerText = formatMoney(stats.at_risk);
      if (document.getElementById('recovered')) document.getElementById('recovered').innerText = formatMoney(stats.recovered);
      if (document.getElementById('promised')) document.getElementById('promised').innerText = formatMoney(stats.promised);
      if (document.getElementById('escalated')) document.getElementById('escalated').innerText = stats.escalated || 0;
    }

    // 2. PTP Stats
    const ptpRes = await fetch('/dashboard/ptp_stats');
    if (ptpRes.ok) {
      const ptp = await ptpRes.json();
      const promised = Number(ptp.promised_amount || 0);
      const recoveredPtp = Number(ptp.recovered_via_ptp || 0);
      const rate = promised > 0 ? ((recoveredPtp / promised) * 100) : 0;

      if (document.getElementById('totalPTPs')) document.getElementById('totalPTPs').innerText = ptp.total_ptps || 0;
      if (document.getElementById('activePTPs')) document.getElementById('activePTPs').innerText = ptp.active || 0;
      if (document.getElementById('completedPTPs')) document.getElementById('completedPTPs').innerText = ptp.completed || 0;
      if (document.getElementById('brokenPTPs')) document.getElementById('brokenPTPs').innerText = ptp.broken || 0;
      if (document.getElementById('ptpRecoveryRate')) document.getElementById('ptpRecoveryRate').innerText = rate.toFixed(1) + '%';
      if (document.getElementById('ptpRecoverySub')) document.getElementById('ptpRecoverySub').innerText = `${formatMoney(recoveredPtp)} recovered of ${formatMoney(promised)} promised`;
    }

    // 3. Activity / Case Feed
    const activityRes = await fetch('/dashboard/activity');
    let activity = [];
    if (activityRes.ok) {
      activity = await activityRes.json();
    } else {
      const casesRes = await fetch('/dashboard/cases?limit=20');
      if (casesRes.ok) {
        const cases = await casesRes.json();
        activity = cases.map(c => ({
          type: 'case',
          id: c.case_id,
          company_id: c.company_id,
          invoice_id: c.invoice_id,
          amount: c.amount,
          status: c.status,
          last_action: c.last_action
        }));
      }
    }

    const tbody = document.getElementById('caseFeed');
    if (!tbody) return;

    if (!activity || activity.length === 0) {
      tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;color:#64748B;padding:30px;">No activity found yet. Ingest an invoice webhook or trigger the orchestrator.</td></tr>';
      return;
    }

    tbody.innerHTML = activity.map(item => {
      const typeStr = (item.type || 'case').toUpperCase();
      const typeTag = typeStr === 'PTP' ? '<span class="type-tag type-ptp">PTP</span>' : '<span class="type-tag type-case">CASE</span>';
      const status = item.status || 'new';
      let statusClass = 'badge-reminding';
      if (status === 'ACTIVE') statusClass = 'badge-ptp-active';
      if (status === 'COMPLETED' || status === 'resolved') statusClass = 'badge-ptp-completed';
      if (status === 'BROKEN' || status === 'halted') statusClass = 'badge-ptp-broken';
      if (status === 'escalated') statusClass = 'badge-escalated';

      return `
        <tr>
          <td>${typeTag}</td>
          <td class="mono">#${item.id || '-'}</td>
          <td class="mono"><strong>${item.company_id || '-'}</strong></td>
          <td class="mono">${item.invoice_id || '-'}</td>
          <td class="mono"><strong>${formatMoney(item.amount)}</strong></td>
          <td><span class="badge ${statusClass}">${status.replace('_', ' ')}</span></td>
          <td><small>${item.last_action || 'Awaiting action...'}</small></td>
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
