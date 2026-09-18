import { STORAGE_KEYS, TARGET_GAME } from '../shared/constants.js';

const freshSession = () => ({
  webAppConnected: false,
  authenticated: false,
  sidCaptured: false,
  phishingTokenCaptured: false,
  apiBaseUrl: null,
  apiHost: null,
  gameVersion: null,
  lastSeenAt: null,
  lastError: null,
});

let state = freshSession();
let initialized = false;

async function init() {
  if (initialized) return;
  initialized = true;
  const stored = await chrome.storage.local.get(STORAGE_KEYS.session);
  state = { ...freshSession(), ...(stored[STORAGE_KEYS.session] || {}) };
}

async function persist() {
  await chrome.storage.local.set({ [STORAGE_KEYS.session]: state });
}

export async function updateSession(patch) {
  await init();
  state = { ...state, ...patch, lastSeenAt: new Date().toISOString() };
  state.authenticated = Boolean(
    state.sidCaptured &&
    state.phishingTokenCaptured &&
    state.apiBaseUrl &&
    state.gameVersion === TARGET_GAME
  );
  await persist();
  return { ...state };
}

export async function markWebAppDisconnected() {
  await init();
  state = { ...state, webAppConnected: false, authenticated: false };
  await persist();
}

export async function markSessionError(error) {
  await init();
  state.lastError = {
    at: new Date().toISOString(),
    status: error?.status ?? null,
    code: error?.code ?? null,
    message: String(error?.message || error),
  };
  if (Number(error?.status) === 401) state.authenticated = false;
  await persist();
}

export async function getSessionStatus() {
  await init();
  return { ...state };
}
