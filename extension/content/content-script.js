(() => {
  'use strict';

  const SOURCE_PAGE = 'fc27-copilot-page';
  const SOURCE_CONTENT = 'fc27-copilot-content';
  const pending = new Map();
  let bridgePolling = false;

  function makeId() {
    return globalThis.crypto?.randomUUID?.() || `fc27-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  }

  function injectPageScript() {
    const script = document.createElement('script');
    script.src = chrome.runtime.getURL('content/page-inject.js');
    script.async = false;
    script.onload = () => script.remove();
    (document.head || document.documentElement).appendChild(script);
  }

  function wait(milliseconds) {
    return new Promise((resolve) => setTimeout(resolve, milliseconds));
  }

  async function pollDaemon() {
    if (bridgePolling) return;
    bridgePolling = true;
    while (true) {
      try {
        await chrome.runtime.sendMessage({ type: 'FC27_BRIDGE_POLL' });
      } catch {
        // Extension reload or page teardown ends this cycle; the next page load restarts it.
      }
      await wait(250);
    }
  }

  window.addEventListener('message', (event) => {
    if (event.source !== window) return;
    const message = event.data;
    if (!message || message.source !== SOURCE_PAGE) return;

    if (message.type === 'FC27_SESSION_UPDATE') {
      chrome.runtime.sendMessage({
        type: 'FC27_SESSION_UPDATE',
        session: message.session,
      }).catch(() => {});
      return;
    }

    if (message.type === 'FC27_ACCOUNT_CHANGED') {
      chrome.runtime.sendMessage({
        type: 'FC27_ACCOUNT_CHANGED',
        change: message.change || {},
      }).catch(() => {});
      return;
    }

    if (message.type === 'FC27_EA_RESPONSE') {
      const callback = pending.get(message.requestId);
      if (!callback) return;
      pending.delete(message.requestId);
      callback(message);
    }
  });

  chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    if (message?.type === 'FC27_WEBAPP_PING') {
      sendResponse({ ok: true, href: window.location.href });
      return false;
    }

    if (message?.type !== 'FC27_EA_CALL') return false;

    const requestId = makeId();
    const timeout = setTimeout(() => {
      const callback = pending.get(requestId);
      if (!callback) return;
      pending.delete(requestId);
      callback({
        ok: false,
        error: { message: 'Timed out waiting for the FC27 page bridge.', code: 'PAGE_BRIDGE_TIMEOUT' },
      });
    }, 30000);

    pending.set(requestId, (response) => {
      clearTimeout(timeout);
      sendResponse(response);
    });

    window.postMessage({
      source: SOURCE_CONTENT,
      type: 'FC27_EA_REQUEST',
      requestId,
      method: message.method,
      params: message.params || {},
    }, '*');

    return true;
  });

  injectPageScript();
  chrome.runtime.sendMessage({ type: 'FC27_WEBAPP_CONNECTED' }).catch(() => {});
  pollDaemon();
})();
