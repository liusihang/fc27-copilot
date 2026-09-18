const $ = (id) => document.getElementById(id);

function badge(el, ok, yes, no) {
  el.textContent = ok ? yes : no;
  el.classList.toggle('ok', ok);
  el.classList.toggle('bad', !ok);
}

async function loadStatus() {
  const response = await chrome.runtime.sendMessage({ type: 'FC27_UI_STATUS' });
  if (!response?.ok) throw new Error(response?.error || 'Failed to load extension status.');
  const value = response.value;
  const session = value.session || {};
  badge($('webapp'), Boolean(value.webApp?.connected), 'Connected', 'Not connected');
  badge($('session'), Boolean(session.authenticated), 'Authenticated', 'Not authenticated');
  $('game').textContent = session.gameVersion || '—';
  $('host').textContent = session.apiHost || '—';
  $('base').textContent = session.apiBaseUrl || '—';
  $('sid').textContent = session.sidCaptured ? 'yes' : 'no';
  $('phishing').textContent = session.phishingTokenCaptured ? 'yes' : 'no';
  $('lastError').textContent = session.lastError
    ? `Last error: ${session.lastError.status || session.lastError.code || ''} ${session.lastError.message || ''}`.trim()
    : '';
}

$('refresh').addEventListener('click', () => loadStatus().catch((error) => { $('lastError').textContent = error.message; }));

loadStatus().catch((error) => { $('lastError').textContent = error.message; });
