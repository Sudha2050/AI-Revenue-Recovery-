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
      return `
        <tr>
          <td class="mono">#${c.case_id || '-'}</td>
          <td class="mono"><strong>${c.company_id || '-'}</strong></td>
          <td class="mono">${c.invoice_id || '-'}</td>
          <td class="mono"><strong>${formatMoney(c.amount)}</strong></td>
          <td>${c.root_cause || 'N/A'}</td>
          <td><span class="badge badge-${status}">${status.replace('_', ' ')}</span></td>
          <td>${c.last_action || 'Awaiting action...'}</td>
        </tr>
      `;
    }).join('');
  } catch (err) {
    console.error('Failed to load dashboard data:', err);
  }
}

document.getElementById('refreshBtn')?.addEventListener('click', loadData);
document.getElementById('processBtn')?.addEventListener('click', triggerProcess);

loadData();
setInterval(loadData, 10000);
