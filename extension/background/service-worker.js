import { getSessionStatus, markWebAppDisconnected, updateSession } from './session-monitor.js';
import { pingWebApp } from './bridge.js';
import { safeEACall } from './ea-call.js';
import { logger } from '../shared/logger.js';
import { STORAGE_KEYS } from '../shared/constants.js';

const ALLOWED_BRIDGE_ORIGINS = new Set(['http://127.0.0.1:3926', 'http://localhost:3926']);

async function uiStatus() {
  const [webApp, session, stored] = await Promise.all([
    pingWebApp(),
    getSessionStatus(),
    chrome.storage.local.get(STORAGE_KEYS.logs),
  ]);
  if (!webApp.connected && session.webAppConnected) await markWebAppDisconnected();
  return {
    webApp,
    session: { ...(await getSessionStatus()), webAppConnected: webApp.connected },
    logs: (stored[STORAGE_KEYS.logs] || []).slice(-30).reverse(),
  };
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message?.type === 'FC27_SESSION_UPDATE') {
    updateSession({ ...message.session, webAppConnected: true })
      .then((session) => sendResponse({ ok: true, session }))
      .catch((error) => sendResponse({ ok: false, error: String(error?.message || error) }));
    return true;
  }

  if (message?.type === 'FC27_WEBAPP_CONNECTED') {
    updateSession({ webAppConnected: true }).then(() => sendResponse({ ok: true }));
    return true;
  }

  if (message?.type === 'FC27_UI_STATUS') {
    uiStatus().then((value) => sendResponse({ ok: true, value }))
      .catch((error) => sendResponse({ ok: false, error: String(error?.message || error) }));
    return true;
  }

  return false;
});

chrome.runtime.onMessageExternal.addListener((message, sender, sendResponse) => {
  let origin = null;
  try { origin = new URL(sender.url || '').origin; } catch { origin = null; }
  if (!ALLOWED_BRIDGE_ORIGINS.has(origin)) {
    sendResponse({ ok: false, error: { code: 'BRIDGE_ORIGIN_DENIED', message: 'External request origin is not the local fc27d bridge.' } });
    return false;
  }

  if (message?.type === 'FC27_DAEMON_PING') {
    sendResponse({ ok: true });
    return false;
  }
  if (message?.type !== 'FC27_DAEMON_CALL' || typeof message.method !== 'string') {
    sendResponse({ ok: false, error: { code: 'INVALID_BRIDGE_MESSAGE', message: 'Expected FC27_DAEMON_CALL with a method.' } });
    return false;
  }

  safeEACall(message.method, message.params || {})
    .then((response) => sendResponse(response))
    .catch((error) => {
      logger.error('External daemon request failed', { message: String(error?.message || error) }).catch(() => {});
      sendResponse({ ok: false, error: { code: 'EXTENSION_ERROR', message: String(error?.message || error) } });
    });
  return true;
});
