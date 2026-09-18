(() => {
  'use strict';

  if (window.__FC27_COPILOT_INJECTED__) return;
  window.__FC27_COPILOT_INJECTED__ = true;

  const SOURCE_PAGE = 'fc27-copilot-page';
  const SOURCE_CONTENT = 'fc27-copilot-content';
  const TARGET_GAME = 'fc27';

  const state = {
    sid: null,
    phishingToken: null,
    apiBaseUrl: null,
    apiHost: null,
    gameVersion: null,
    capturedAt: null,
  };

  let lastAnnouncement = '';

  function isAllowedEaHost(hostname) {
    const host = String(hostname || '').toLowerCase();
    return host === 'ea.com' || host.endsWith('.ea.com');
  }

  function detectApiBase(rawUrl) {
    try {
      const url = new URL(rawUrl, window.location.href);
      if (url.protocol !== 'https:' || !isAllowedEaHost(url.hostname)) return null;
      const match = url.pathname.match(/^(.*\/ut\/game\/(fc\d+))(?:\/|$)/i);
      if (!match) return null;
      const gameVersion = match[2].toLowerCase();
      if (gameVersion !== TARGET_GAME) return null;
      return {
        apiBaseUrl: `${url.origin}${match[1]}`.replace(/\/$/, ''),
        apiHost: url.host,
        gameVersion,
      };
    } catch {
      return null;
    }
  }

  function publicSession() {
    return {
      webAppConnected: true,
      authenticated: Boolean(
        state.sid &&
        state.phishingToken &&
        state.apiBaseUrl &&
        state.gameVersion === TARGET_GAME
      ),
      sidCaptured: Boolean(state.sid),
      phishingTokenCaptured: Boolean(state.phishingToken),
      apiBaseUrl: state.apiBaseUrl,
      apiHost: state.apiHost,
      gameVersion: state.gameVersion,
      capturedAt: state.capturedAt,
    };
  }

  function announceSession(force = false) {
    const payload = publicSession();
    const serialized = JSON.stringify(payload);
    if (!force && serialized === lastAnnouncement) return;
    lastAnnouncement = serialized;
    window.postMessage({
      source: SOURCE_PAGE,
      type: 'FC27_SESSION_UPDATE',
      session: payload,
    }, '*');
  }

  function normalizeHeaders(headersLike) {
    const headers = new Map();
    if (!headersLike) return headers;
    try {
      const h = new Headers(headersLike);
      h.forEach((value, key) => headers.set(key.toLowerCase(), String(value)));
    } catch {
      if (typeof headersLike === 'object') {
        for (const [key, value] of Object.entries(headersLike)) {
          headers.set(String(key).toLowerCase(), String(value));
        }
      }
    }
    return headers;
  }

  function capture(rawUrl, headersLike) {
    let changed = false;
    const detected = detectApiBase(rawUrl);
    if (detected) {
      for (const key of ['apiBaseUrl', 'apiHost', 'gameVersion']) {
        if (state[key] !== detected[key]) {
          state[key] = detected[key];
          changed = true;
        }
      }
    }

    const headers = normalizeHeaders(headersLike);
    const sid = headers.get('x-ut-sid');
    const phishing = headers.get('x-ut-phishing-token');
    if (sid && sid !== state.sid) {
      state.sid = sid;
      changed = true;
    }
    if (phishing && phishing !== state.phishingToken) {
      state.phishingToken = phishing;
      changed = true;
    }
    if (changed) {
      state.capturedAt = new Date().toISOString();
      announceSession();
    }
  }

  function observeFetch() {
    const original = window.fetch.bind(window);
    window.fetch = function fc27ObservedFetch(input, init = {}) {
      try {
        const url = typeof input === 'string' || input instanceof URL ? String(input) : input?.url;
        const baseHeaders = input instanceof Request ? input.headers : undefined;
        const merged = new Headers(baseHeaders || {});
        new Headers(init.headers || {}).forEach((value, key) => merged.set(key, value));
        capture(url, merged);
      } catch {
        // Observation failures must never break the EA web app request.
      }
      return original(input, init);
    };
  }

  function observeXhr() {
    const originalOpen = XMLHttpRequest.prototype.open;
    const originalSetHeader = XMLHttpRequest.prototype.setRequestHeader;
    const originalSend = XMLHttpRequest.prototype.send;

    XMLHttpRequest.prototype.open = function fc27Open(method, url, ...rest) {
      this.__fc27Url = url;
      this.__fc27Headers = {};
      return originalOpen.call(this, method, url, ...rest);
    };

    XMLHttpRequest.prototype.setRequestHeader = function fc27SetHeader(name, value) {
      try {
        this.__fc27Headers ||= {};
        this.__fc27Headers[name] = value;
        capture(this.__fc27Url, this.__fc27Headers);
      } catch {
        // No-op.
      }
      return originalSetHeader.call(this, name, value);
    };

    XMLHttpRequest.prototype.send = function fc27Send(...args) {
      try {
        capture(this.__fc27Url, this.__fc27Headers || {});
      } catch {
        // No-op.
      }
      return originalSend.apply(this, args);
    };
  }

  function requireSession() {
    if (!state.apiBaseUrl || state.gameVersion !== TARGET_GAME) {
      const error = new Error('FC27 API base has not been detected yet. Open/navigate the FC27 Web App to trigger a UTAS request.');
      error.code = 'FC27_API_NOT_DETECTED';
      throw error;
    }
    if (!state.sid || !state.phishingToken) {
      const error = new Error('FC27 session headers have not been captured yet. Navigate in the Web App and retry.');
      error.code = 'FC27_SESSION_NOT_CAPTURED';
      throw error;
    }
  }

  function buildUrl(path, query = {}) {
    requireSession();
    const safePath = String(path || '').startsWith('/') ? String(path) : `/${path}`;
    const url = new URL(`${state.apiBaseUrl}${safePath}`);
    for (const [key, value] of Object.entries(query || {})) {
      if (value === undefined || value === null || value === '') continue;
      if (Array.isArray(value)) url.searchParams.set(key, value.join(','));
      else url.searchParams.set(key, String(value));
    }
    return url;
  }

  async function eaRequest(path, { method = 'GET', query, body } = {}) {
    const url = buildUrl(path, query);
    const headers = {
      Accept: 'application/json',
      'Content-Type': 'application/json',
      'X-UT-SID': state.sid,
      'X-UT-PHISHING-TOKEN': state.phishingToken,
    };

    const response = await fetch(url.href, {
      method,
      headers,
      credentials: 'include',
      body: body === undefined ? undefined : JSON.stringify(body),
    });

    let payload = null;
    const text = await response.text();
    if (text) {
      try { payload = JSON.parse(text); }
      catch { payload = { raw: text.slice(0, 5000) }; }
    }

    if (!response.ok) {
      const message = payload?.reason || payload?.message || payload?.error || `${response.status} ${response.statusText}`;
      const error = new Error(String(message));
      error.status = response.status;
      error.code = payload?.code || payload?.reason || null;
      error.payload = payload;
      throw error;
    }
    return payload ?? {};
  }

  function readValue(object, propertyNames, methodNames = []) {
    for (const methodName of methodNames) {
      if (typeof object?.[methodName] === 'function') {
        const value = object[methodName]();
        if (value !== undefined && value !== null) return value;
      }
    }
    for (const propertyName of propertyNames) {
      const value = object?.[propertyName];
      if (value !== undefined && value !== null) return value;
    }
    return null;
  }

  function serializeItem(item) {
    return {
      item_id: Number(readValue(item, ['id', 'itemId'], ['getId'])),
      card_ea_id: Number(readValue(item, ['definitionId', 'defId'], ['getDefinitionId'])),
      rating: readValue(item, ['rating']),
      preferred_position: readValue(item, ['preferredPosition']),
      nation_id: readValue(item, ['nationId']),
      league_id: readValue(item, ['leagueId']),
      club_id: readValue(item, ['teamId', 'clubId']),
      rarity_id: readValue(item, ['rareflag', 'rarityId']),
      tradeable: typeof item?.isTradeable === 'function' ? Boolean(item.isTradeable()) : !Boolean(item?.untradeable),
      loan_uses_remaining: readValue(item, ['loans']),
      acquisition_cost: readValue(item, ['lastSalePrice']),
      item_type: readValue(item, ['itemType', 'type']),
    };
  }

  function observeOnce(observable) {
    return new Promise((resolve, reject) => {
      if (!observable?.observe) {
        reject(Object.assign(new Error('EA Web App service did not return an observable.'), { code: 'EA_SERVICE_UNAVAILABLE' }));
        return;
      }
      observable.observe(undefined, (sender, response) => {
        if (response?.success === false) {
          reject(Object.assign(new Error(`EA Web App service failed with status ${response.status ?? 'unknown'}.`), {
            status: response.status ?? null,
            code: response?.error?.code || 'EA_SERVICE_FAILED',
            payload: response?.error || null,
          }));
          return;
        }
        resolve(response || {});
      });
    });
  }

  function requireWebAppServices() {
    if (!globalThis.services) {
      throw Object.assign(new Error('FC27 Web App services are not initialized yet.'), { code: 'EA_SERVICES_NOT_READY' });
    }
    return globalThis.services;
  }

  function marketQuery(params = {}) {
    const q = {
      num: Math.max(1, Math.min(Number(params.limit ?? params.num ?? 21), 21)),
      start: Math.max(0, Number(params.start ?? 0)),
      type: params.type || 'player',
    };
    const map = {
      masked_def_id: 'maskedDefId',
      maskedDefId: 'maskedDefId',
      definition_id: 'definitionId',
      definitionId: 'definitionId',
      rarity_id: 'rarityIds',
      rarityIds: 'rarityIds',
      min_buy_now: 'minb',
      max_buy_now: 'maxb',
      min_bid: 'micr',
      max_bid: 'macr',
      position: 'pos',
      level: 'lev',
      nation_id: 'nat',
      league_id: 'leag',
      club_id: 'team',
    };
    for (const [inputKey, apiKey] of Object.entries(map)) {
      if (params[inputKey] !== undefined && params[inputKey] !== null) q[apiKey] = params[inputKey];
    }
    if (params.raw_query && typeof params.raw_query === 'object') Object.assign(q, params.raw_query);
    return q;
  }

  const methods = {
    async getSessionStatus() {
      return publicSession();
    },

    async getIdentity() {
      const appServices = requireWebAppServices();
      const user = appServices.User?.getUser?.();
      const persona = user?.getSelectedPersona?.();
      const club = persona?.getCurrentClub?.();
      if (!persona) {
        throw Object.assign(new Error('No selected EA Persona is available.'), { code: 'EA_PERSONA_NOT_FOUND' });
      }
      return {
        user_id: readValue(user, ['id', 'userId']),
        persona_id: String(readValue(persona, ['id', 'personaId', 'personaIdStr']) ?? ''),
        platform: persona.isPC ? 'pc' : 'ps5',
        club_id: readValue(club, ['id', 'clubId']),
        club_name: readValue(club, ['name', 'clubName']),
      };
    },

    async getCoinBalance() {
      return eaRequest('/user/credits');
    },

    async keepalive() {
      try { return await eaRequest('/user/accountinfo'); }
      catch (error) {
        if (Number(error.status) !== 404) throw error;
        return eaRequest('/user/credits');
      }
    },

    async searchTransferMarket(params) {
      return eaRequest('/transfermarket', { query: marketQuery(params) });
    },

    async buyNow(params) {
      return eaRequest(`/trade/${encodeURIComponent(params.trade_id)}/bid`, {
        method: 'PUT',
        body: { bid: Number(params.buy_now_price) },
      });
    },

    async placeBid(params) {
      return eaRequest(`/trade/${encodeURIComponent(params.trade_id)}/bid`, {
        method: 'PUT',
        body: { bid: Number(params.bid) },
      });
    },

    async listOnMarket(params) {
      return eaRequest('/auctionhouse', {
        method: 'POST',
        body: {
          itemData: { id: Number(params.item_id) },
          startingBid: Number(params.starting_bid),
          buyNowPrice: Number(params.buy_now_price),
          duration: Number(params.duration ?? 3600),
        },
      });
    },

    async getClubPage(params = {}) {
      return eaRequest('/club', {
        query: {
          type: 'player',
          start: Math.max(0, Number(params.start ?? 0)),
          count: Math.max(1, Math.min(Number(params.count ?? 100), 100)),
          defId: params.definition_id,
        },
      });
    },

    async getUnassigned() {
      return eaRequest('/purchased/items');
    },

    async getStoragePage(params = {}) {
      const appServices = requireWebAppServices();
      if (typeof globalThis.UTBucketedItemSearchViewModel !== 'function') {
        throw Object.assign(new Error('FC27 storage search model is unavailable.'), { code: 'EA_STORAGE_MODEL_UNAVAILABLE' });
      }
      const model = new globalThis.UTBucketedItemSearchViewModel();
      const criteria = model.searchCriteria;
      criteria.offset = Math.max(0, Number(params.offset ?? 0));
      criteria.count = Math.max(1, Math.min(Number(params.count ?? 100), 100));
      const response = await observeOnce(appServices.Item.searchStorageItems(criteria));
      const payload = response.response || response.data || {};
      const items = Array.isArray(payload.items) ? payload.items.map(serializeItem) : [];
      return {
        items,
        offset: criteria.offset,
        count: criteria.count,
        end_of_list: Boolean(payload.endOfList || items.length < criteria.count),
        status: response.status ?? null,
      };
    },

    async sendToTradepile(params) {
      return eaRequest('/item', {
        method: 'PUT',
        body: { itemData: [{ id: Number(params.item_id), pile: 'trade' }] },
      });
    },

    async sendToClub(params) {
      return eaRequest('/item', {
        method: 'PUT',
        body: { itemData: [{ id: Number(params.item_id), pile: 'club' }] },
      });
    },

    async getTradepile() {
      return eaRequest('/tradepile');
    },

    async getWatchlist() {
      return eaRequest('/watchlist');
    },

    async relistAll() {
      return eaRequest('/auctionhouse/relist', { method: 'PUT', body: {} });
    },

    async clearSold() {
      return eaRequest('/tradepile', { method: 'DELETE' });
    },

    async getSbcSets() {
      try { return await eaRequest('/sbs/sets'); }
      catch (error) {
        if (Number(error.status) !== 404) throw error;
        return eaRequest('/sbs/challenge');
      }
    },

    async getSbcChallenge(params) {
      const id = encodeURIComponent(params.challenge_id);
      try { return await eaRequest(`/sbs/challenge/${id}`); }
      catch (error) {
        if (Number(error.status) !== 404) throw error;
        return eaRequest('/sbs/challenge', { query: { challengeId: params.challenge_id } });
      }
    },
  };

  async function executeMethod(method, params) {
    const fn = methods[method];
    if (!fn) {
      const error = new Error(`Unknown page method: ${method}`);
      error.code = 'METHOD_NOT_FOUND';
      throw error;
    }
    return fn(params || {});
  }

  window.addEventListener('message', (event) => {
    if (event.source !== window) return;
    const message = event.data;
    if (!message || message.source !== SOURCE_CONTENT || message.type !== 'FC27_EA_REQUEST') return;

    Promise.resolve()
      .then(() => executeMethod(message.method, message.params || {}))
      .then((result) => {
        window.postMessage({
          source: SOURCE_PAGE,
          type: 'FC27_EA_RESPONSE',
          requestId: message.requestId,
          ok: true,
          result,
        }, '*');
      })
      .catch((error) => {
        window.postMessage({
          source: SOURCE_PAGE,
          type: 'FC27_EA_RESPONSE',
          requestId: message.requestId,
          ok: false,
          error: {
            message: String(error?.message || error),
            status: error?.status ?? null,
            code: error?.code ?? null,
            payload: error?.payload ?? null,
          },
        }, '*');
      });
  });

  // A document_start injection normally sees subsequent requests. Also scan
  // existing performance entries in case Chrome scheduled a resource before
  // the bridge finished installing; this can recover the API base URL (not headers).
  try {
    for (const entry of performance.getEntriesByType('resource')) capture(entry.name, null);
  } catch {
    // No-op.
  }

  observeFetch();
  observeXhr();
  announceSession(true);
})();
