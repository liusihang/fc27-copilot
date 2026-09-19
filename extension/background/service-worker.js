import { getSessionStatus, markWebAppDisconnected, updateSession } from './session-monitor.js';
import { pingWebApp } from './bridge.js';
import { STORAGE_KEYS } from '../shared/constants.js';
import {
  getDaemonStatus,
  getDaemonUrl,
  notifyDaemon,
  pollDaemonOnce,
  setDaemonUrl,
} from './daemon.js';

async function uiStatus() {
  const [webApp, session, daemon, stored] = await Promise.all([
    pingWebApp(),
    getSessionStatus(),
    getDaemonStatus(),
    chrome.storage.local.get(STORAGE_KEYS.logs),
  ]);
  if (!webApp.connected && session.webAppConnected) await markWebAppDisconnected();
  return {
    webApp,
    daemon,
    session: { ...(await getSessionStatus()), webAppConnected: webApp.connected },
    logs: (stored[STORAGE_KEYS.logs] || []).slice(-30).reverse(),
  };
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message?.type === 'FC27_SESSION_UPDATE') {
    updateSession({ ...message.session, webAppConnected: true })
      .then(async (session) => {
        if (session.authenticated) {
          await notifyDaemon('session_authenticated', {
            webAppConnected: session.webAppConnected,
            authenticated: session.authenticated,
            apiHost: session.apiHost,
            gameVersion: session.gameVersion,
            capturedAt: session.capturedAt,
          });
        }
        sendResponse({ ok: true, session });
      })
      .catch((error) => sendResponse({ ok: false, error: String(error?.message || error) }));
    return true;
  }

  if (message?.type === 'FC27_ACCOUNT_CHANGED') {
    notifyDaemon('account_changed', message.change || {})
      .then((value) => sendResponse({ ok: true, value }))
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

  if (message?.type === 'FC27_DAEMON_URL_GET') {
    getDaemonUrl().then((serverUrl) => sendResponse({ ok: true, serverUrl }))
      .catch((error) => sendResponse({ ok: false, error: String(error?.message || error) }));
    return true;
  }

  if (message?.type === 'FC27_DAEMON_URL_SET') {
    setDaemonUrl(message.serverUrl).then((serverUrl) => sendResponse({ ok: true, serverUrl }))
      .catch((error) => sendResponse({ ok: false, error: String(error?.message || error) }));
    return true;
  }

  if (message?.type === 'FC27_BRIDGE_POLL') {
    pollDaemonOnce().then((value) => sendResponse({ ok: true, value }))
      .catch((error) => sendResponse({ ok: false, error: String(error?.message || error) }));
    return true;
  }

  return false;
});
