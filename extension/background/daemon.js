import { DEFAULT_DAEMON_URL, STORAGE_KEYS } from '../shared/constants.js';
import { logger } from '../shared/logger.js';
import { safeEACall } from './ea-call.js';

const LOOPBACK_HOSTS = new Set(['127.0.0.1', 'localhost']);
const REQUEST_TIMEOUT_MS = 15000;

let pollController = null;
let activePoll = null;
let status = {
  connected: false,
  serverUrl: DEFAULT_DAEMON_URL,
  lastSeenAt: null,
  lastError: null,
};

export function normalizeDaemonUrl(rawValue) {
  const value = String(rawValue || DEFAULT_DAEMON_URL).trim();
  let url;
  try {
    url = new URL(value);
  } catch {
    throw new Error('FC27 server address must be a valid loopback HTTP URL.');
  }
  if (
    url.protocol !== 'http:'
    || !LOOPBACK_HOSTS.has(url.hostname)
    || url.username
    || url.password
    || (url.pathname && url.pathname !== '/')
    || url.search
    || url.hash
  ) {
    throw new Error('FC27 server address must use http://127.0.0.1:<port> or http://localhost:<port>.');
  }
  return url.origin;
}

export async function getDaemonUrl() {
  const stored = await chrome.storage.local.get(STORAGE_KEYS.daemonUrl);
  return normalizeDaemonUrl(stored[STORAGE_KEYS.daemonUrl] || DEFAULT_DAEMON_URL);
}

export async function setDaemonUrl(value) {
  const serverUrl = normalizeDaemonUrl(value);
  await chrome.storage.local.set({ [STORAGE_KEYS.daemonUrl]: serverUrl });
  status = { ...status, connected: false, serverUrl, lastError: null };
  pollController?.abort();
  return serverUrl;
}

function setConnected(serverUrl) {
  const wasConnected = status.connected;
  status = {
    ...status,
    connected: true,
    serverUrl,
    lastSeenAt: new Date().toISOString(),
    lastError: null,
  };
  return !wasConnected;
}

function setDisconnected(serverUrl, error) {
  status = {
    ...status,
    connected: false,
    serverUrl,
    lastError: {
      at: new Date().toISOString(),
      message: String(error?.message || error),
    },
  };
}

async function fetchWithTimeout(url, options = {}, timeoutMs = REQUEST_TIMEOUT_MS, trackPoll = false) {
  const controller = new AbortController();
  if (trackPoll) pollController = controller;
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, { ...options, signal: controller.signal });
  } finally {
    clearTimeout(timeout);
    if (trackPoll && pollController === controller) pollController = null;
  }
}

async function postJson(serverUrl, path, payload) {
  const response = await fetchWithTimeout(`${serverUrl}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  }, 10000);
  if (!response.ok) throw new Error(`fc27d returned HTTP ${response.status}.`);
  return response.json();
}

export async function notifyDaemon(type, data = {}) {
  const serverUrl = await getDaemonUrl();
  try {
    const result = await postJson(serverUrl, '/browser/event', {
      event_id: globalThis.crypto?.randomUUID?.() || `fc27-${Date.now()}-${Math.random().toString(16).slice(2)}`,
      type,
      observed_at: new Date().toISOString(),
      data,
    });
    setConnected(serverUrl);
    return result;
  } catch (error) {
    setDisconnected(serverUrl, error);
    await logger.warn('fc27d event delivery failed', { type, message: String(error?.message || error) });
    return { ok: false, error: String(error?.message || error) };
  }
}

async function announceCurrentSession() {
  const session = await safeEACall('getSessionStatus', {});
  if (session.ok && session.data?.authenticated) {
    await notifyDaemon('session_authenticated', {
      webAppConnected: session.data.webAppConnected,
      authenticated: session.data.authenticated,
      apiHost: session.data.apiHost,
      gameVersion: session.data.gameVersion,
      capturedAt: session.data.capturedAt,
    });
  }
}

async function runPoll() {
    const serverUrl = await getDaemonUrl();
    status.serverUrl = serverUrl;
    try {
      const response = await fetchWithTimeout(`${serverUrl}/browser/poll`, {
        cache: 'no-store',
        headers: { Accept: 'application/json' },
      }, REQUEST_TIMEOUT_MS, true);
      const newlyConnected = setConnected(serverUrl);
      if (newlyConnected) announceCurrentSession().catch(() => {});
      if (response.status === 204) return { connected: true, request: false };
      if (!response.ok) throw new Error(`fc27d poll returned HTTP ${response.status}.`);
      const envelope = await response.json();
      if (envelope?.type !== 'fc27-request' || !envelope.request_id || !envelope.method) {
        return { connected: true, request: false };
      }
      const payload = await safeEACall(envelope.method, envelope.params || {});
      await postJson(serverUrl, '/browser/respond', {
        request_id: envelope.request_id,
        payload,
      });
      setConnected(serverUrl);
      return { connected: true, request: true };
    } catch (error) {
      setDisconnected(serverUrl, error);
      return { connected: false, error: String(error?.message || error) };
    }
}

export function pollDaemonOnce() {
  if (!activePoll) {
    activePoll = runPoll()
      .catch((error) => {
        logger.error('fc27d direct bridge poll failed', { message: String(error?.message || error) });
        return { connected: false, error: String(error?.message || error) };
      })
      .finally(() => { activePoll = null; });
  }
  return activePoll;
}

export async function getDaemonStatus() {
  const serverUrl = await getDaemonUrl();
  let health = null;
  try {
    const response = await fetchWithTimeout(`${serverUrl}/health`, {
      cache: 'no-store',
      headers: { Accept: 'application/json' },
    }, 3000);
    if (response.ok) {
      health = await response.json();
      setConnected(serverUrl);
    }
  } catch (error) {
    setDisconnected(serverUrl, error);
  }
  return { ...status, serverUrl, health };
}
