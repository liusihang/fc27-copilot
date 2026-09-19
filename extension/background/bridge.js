const WEB_APP_PREFIX = 'https://www.ea.com/ea-sports-fc/ultimate-team/web-app';

async function findWebAppTabs() {
  const tabs = await chrome.tabs.query({ url: `${WEB_APP_PREFIX}/*` });
  return tabs.sort((left, right) => {
    const activeDifference = Number(Boolean(right.active)) - Number(Boolean(left.active));
    if (activeDifference) return activeDifference;
    return Number(right.lastAccessed || 0) - Number(left.lastAccessed || 0);
  });
}

export async function pingWebApp() {
  const tabs = await findWebAppTabs();
  if (!tabs.length) return { connected: false, tabId: null };
  for (const tab of tabs) {
    if (!tab?.id) continue;
    try {
      const response = await chrome.tabs.sendMessage(tab.id, { type: 'FC27_WEBAPP_PING' });
      if (response?.ok) {
        return { connected: true, tabId: tab.id, href: response.href || tab.url };
      }
    } catch {
      // Continue to another matching Web App tab.
    }
  }
  return { connected: false, tabId: tabs[0]?.id ?? null, href: tabs[0]?.url ?? null };
}

export async function callEA(method, params = {}) {
  const tabs = await findWebAppTabs();
  if (!tabs.length) {
    const error = new Error('FC27 Web App tab not found. Open the Ultimate Team Web App in Chrome.');
    error.code = 'WEBAPP_TAB_NOT_FOUND';
    throw error;
  }

  let response = null;
  let cause = null;
  for (const tab of tabs) {
    if (!tab?.id) continue;
    try {
      response = await chrome.tabs.sendMessage(tab.id, {
        type: 'FC27_EA_CALL',
        method,
        params,
      });
      break;
    } catch (error) {
      cause = error;
    }
  }
  if (response === null) {
    const error = new Error('Could not reach any FC27 content bridge. Refresh the Web App and retry.');
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
