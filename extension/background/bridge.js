const WEB_APP_PREFIX = 'https://www.ea.com/ea-sports-fc/ultimate-team/web-app';

async function findWebAppTab() {
  const tabs = await chrome.tabs.query({ url: `${WEB_APP_PREFIX}/*` });
  if (!tabs.length) return null;
  const active = tabs.find((tab) => tab.active) || tabs[0];
  return active;
}

export async function pingWebApp() {
  const tab = await findWebAppTab();
  if (!tab?.id) return { connected: false, tabId: null };
  try {
    const response = await chrome.tabs.sendMessage(tab.id, { type: 'FC27_WEBAPP_PING' });
    return { connected: Boolean(response?.ok), tabId: tab.id, href: response?.href || tab.url };
  } catch {
    return { connected: false, tabId: tab.id, href: tab.url };
  }
}

export async function callEA(method, params = {}) {
  const tab = await findWebAppTab();
  if (!tab?.id) {
    const error = new Error('FC27 Web App tab not found. Open the Ultimate Team Web App in Chrome.');
    error.code = 'WEBAPP_TAB_NOT_FOUND';
    throw error;
  }

  let response;
  try {
    response = await chrome.tabs.sendMessage(tab.id, {
      type: 'FC27_EA_CALL',
      method,
      params,
    });
  } catch (cause) {
    const error = new Error('Could not reach the FC27 content bridge. Refresh the Web App and retry.');
    error.code = 'CONTENT_BRIDGE_UNREACHABLE';
    error.cause = cause;
    throw error;
  }

  if (!response?.ok) {
    const error = new Error(response?.error?.message || 'EA request failed.');
    error.status = response?.error?.status ?? null;
    error.code = response?.error?.code ?? null;
    error.payload = response?.error?.payload ?? null;
    throw error;
  }
  return response.result;
}
