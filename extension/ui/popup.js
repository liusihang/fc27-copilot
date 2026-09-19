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
  const daemon = value.daemon || {};
  badge($('webapp'), Boolean(value.webApp?.connected), 'Connected', 'Not connected');
  badge($('session'), Boolean(session.authenticated), 'Authenticated', 'Not authenticated');
  badge($('daemon'), Boolean(daemon.connected), 'Connected', 'Not connected');
  $('game').textContent = session.gameVersion || '—';
  $('host').textContent = session.apiHost || '—';
  $('base').textContent = session.apiBaseUrl || '—';
  $('sid').textContent = session.sidCaptured ? 'yes' : 'no';
  $('phishing').textContent = session.phishingTokenCaptured ? 'yes' : 'no';
  $('serverUrl').value = daemon.serverUrl || 'http://127.0.0.1:3926';
  const autoSync = daemon.health?.auto_sync || {};
  $('autoSync').textContent = autoSync.running
    ? 'running'
    : autoSync.pending
      ? 'pending'
      : autoSync.last_success_at
        ? `synced ${autoSync.last_success_at}`
        : 'waiting for login';
  const error = daemon.lastError || session.lastError;
  $('lastError').textContent = error
    ? `Last error: ${error.status || error.code || ''} ${error.message || ''}`.trim()
    : '';
}

$('refresh').addEventListener('click', () => loadStatus().catch((error) => { $('lastError').textContent = error.message; }));

$('saveServer').addEventListener('click', async () => {
  try {
    const response = await chrome.runtime.sendMessage({
      type: 'FC27_DAEMON_URL_SET',
      serverUrl: $('serverUrl').value,
    });
    if (!response?.ok) throw new Error(response?.error || 'Failed to save FC27 server address.');
    await loadStatus();
  } catch (error) {
    $('lastError').textContent = error.message;
  }
});

loadStatus().catch((error) => { $('lastError').textContent = error.message; });
