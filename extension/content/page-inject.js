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

  function serializeAuction(item) {
    const auction = item?.getAuctionData?.() || item?._auction || item?.auctionData || {};
    return {
      tradeId: readValue(auction, ['tradeId', 'id']),
      startingBid: readValue(auction, ['startingBid']),
      buyNowPrice: readValue(auction, ['buyNowPrice']),
      currentBid: readValue(auction, ['currentBid']),
      tradeState: readValue(auction, ['tradeState', 'bidState']),
      expires: readValue(auction, ['expires', 'expiresAt']),
      itemData: serializeItem(item),
    };
  }

  function resultItems(response) {
    const payload = response?.data ?? response?.response ?? response ?? {};
    for (const value of [payload?.items, payload?.data?.items, payload?._collection]) {
      if (Array.isArray(value)) return value;
    }
    return [];
  }

  function plainSbcSet(set) {
    return {
      id: readValue(set, ['id', 'setId']),
      name: readValue(set, ['name', 'displayName']),
      status: readValue(set, ['status']),
      expires: readValue(set, ['expires', 'endTime']),
      repeatable: Boolean(readValue(set, ['repeatable', 'isRepeatable'])),
      challenge_count: Array.isArray(set?.challenges) ? set.challenges.length : null,
    };
  }

  function plainValue(value, depth = 0, seen = new WeakSet()) {
    if (value === null || value === undefined || ['string', 'number', 'boolean'].includes(typeof value)) return value ?? null;
    if (typeof value === 'function' || depth > 3) return undefined;
    if (Array.isArray(value)) return value.map((entry) => plainValue(entry, depth + 1, seen)).filter((entry) => entry !== undefined);
    if (typeof value !== 'object' || seen.has(value)) return undefined;
    seen.add(value);
    const output = {};
    for (const key of Object.keys(value)) {
      const normalized = plainValue(value[key], depth + 1, seen);
      if (normalized !== undefined) output[key] = normalized;
    }
    return output;
  }

  function plainSbcChallenge(challenge) {
    const requirements = challenge?.eligibilityRequirements || challenge?.requirements || [];
    return {
      id: readValue(challenge, ['id', 'challengeId']),
      set_id: readValue(challenge, ['setId']),
      name: readValue(challenge, ['name', 'displayName']),
      status: readValue(challenge, ['status']),
      repeatable: Boolean(readValue(challenge, ['repeatable', 'isRepeatable'])),
      completed: typeof challenge?.isCompleted === 'function' ? Boolean(challenge.isCompleted()) : Boolean(challenge?.completed),
      requirements: plainValue(requirements),
    };
  }

  async function loadSbcSets() {
    const appServices = requireWebAppServices();
    const response = await observeOnce(appServices.SBC.requestSets());
    const payload = response.data ?? response.response ?? {};
    return {
      appServices,
      status: response.status ?? null,
      sets: Array.isArray(payload.sets) ? payload.sets : Array.isArray(payload) ? payload : [],
    };
  }

  async function loadSbcChallenges(setId) {
    const loaded = await loadSbcSets();
    const set = loaded.sets.find((entry) => String(readValue(entry, ['id', 'setId'])) === String(setId));
    if (!set) throw Object.assign(new Error(`SBC set ${setId} was not found.`), { code: 'SBC_SET_NOT_FOUND' });
    return requestSbcChallenges(loaded.appServices, set);
  }

  async function requestSbcChallenges(appServices, set) {
    const response = await observeOnce(appServices.SBC.requestChallengesForSet(set));
    const payload = response.data ?? response.response ?? {};
    const challenges = Array.isArray(payload.challenges) ? payload.challenges : Array.isArray(payload) ? payload : [];
    return { set, challenges, status: response.status ?? null };
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

  async function findMarketItem(params) {
    const appServices = requireWebAppServices();
    const model = new globalThis.UTBucketedItemSearchViewModel();
    const criteria = model.searchCriteria;
    criteria.defId = [Number(params.definition_id)];
    criteria.count = 21;
    criteria.maxBuy = Number(params.price);
    appServices.Item.clearTransferMarketCache?.();
    const response = await observeOnce(appServices.Item.searchTransferMarket(criteria, 1));
    const item = resultItems(response).find((entry) => {
      const auction = entry?.getAuctionData?.() || entry?._auction || entry?.auctionData || {};
      return String(readValue(auction, ['tradeId', 'id'])) === String(params.trade_id);
    });
    if (!item) {
      throw Object.assign(new Error(`Trade ${params.trade_id} is no longer available at the requested price.`), { code: 'TRADE_NOT_FOUND' });
    }
    return { appServices, item };
  }

  async function loadItemById(itemId) {
    const appServices = requireWebAppServices();
    const response = await observeOnce(appServices.Item.requestItemsById([Number(itemId)]));
    const item = resultItems(response).find(
      (entry) => Number(readValue(entry, ['id', 'itemId'], ['getId'])) === Number(itemId)
    );
    if (!item) {
      throw Object.assign(new Error(`Owned item ${itemId} was not found.`), { code: 'ITEM_NOT_FOUND' });
    }
    return { appServices, item };
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
      const appServices = requireWebAppServices();
      if (typeof appServices.User?.requestCurrencies === 'function') {
        await observeOnce(appServices.User.requestCurrencies());
      }
      const user = appServices.User?.getUser?.();
      const credits = readValue(user?.coins, ['amount']) ?? readValue(user?.credits, ['amount']) ?? readValue(user, ['coinBalance', 'credits']);
      if (credits === null) {
        throw Object.assign(new Error('FC27 user currency state does not expose a coin balance.'), { code: 'EA_COIN_SCHEMA_INVALID' });
      }
      return { credits: Number(credits) };
    },

    async keepalive() {
      try { return await eaRequest('/user/accountinfo'); }
      catch (error) {
        if (Number(error.status) !== 404) throw error;
        return eaRequest('/user/credits');
      }
    },

    async searchTransferMarket(params) {
      const appServices = requireWebAppServices();
      if (typeof globalThis.UTBucketedItemSearchViewModel !== 'function') {
        throw Object.assign(new Error('FC27 transfer search model is unavailable.'), { code: 'EA_MARKET_MODEL_UNAVAILABLE' });
      }
      const model = new globalThis.UTBucketedItemSearchViewModel();
      const criteria = model.searchCriteria;
      criteria.defId = [Number(params.definition_id ?? params.masked_def_id)];
      criteria.count = Math.max(1, Math.min(Number(params.limit ?? 21), 21));
      if (params.min_buy_now != null) criteria.minBuy = Number(params.min_buy_now);
      if (params.max_buy_now != null) criteria.maxBuy = Number(params.max_buy_now);
      appServices.Item.clearTransferMarketCache?.();
      const response = await observeOnce(appServices.Item.searchTransferMarket(criteria, 1));
      return { auctionInfo: resultItems(response).map(serializeAuction) };
    },

    async buyNow(params) {
      const loaded = await findMarketItem({
        trade_id: params.trade_id,
        definition_id: params.definition_id,
        price: params.buy_now_price,
      });
      const response = await observeOnce(
        loaded.appServices.Item.bid(loaded.item, Number(params.buy_now_price))
      );
      return { status: response.status ?? null, trade_id: String(params.trade_id) };
    },

    async placeBid(params) {
      const loaded = await findMarketItem({
        trade_id: params.trade_id,
        definition_id: params.definition_id,
        price: params.bid,
      });
      const response = await observeOnce(
        loaded.appServices.Item.bid(loaded.item, Number(params.bid))
      );
      return { status: response.status ?? null, trade_id: String(params.trade_id) };
    },

    async listOnMarket(params) {
      const loaded = await loadItemById(params.item_id);
      const response = await observeOnce(
        loaded.appServices.Item.list(
          loaded.item,
          Number(params.starting_bid),
          Number(params.buy_now_price),
          Number(params.duration ?? 3600)
        )
      );
      return {
        status: response.status ?? null,
        success: readValue(response, ['success'], ['getSuccess']),
        error: plainValue(response.error ?? response.errors ?? null),
        data: plainValue(response.data ?? response.response ?? null),
        item_id: Number(params.item_id),
      };
    },

    async getClubPage(params = {}) {
      const appServices = requireWebAppServices();
      if (typeof globalThis.UTBucketedItemSearchViewModel !== 'function') {
        throw Object.assign(new Error('FC27 club search model is unavailable.'), { code: 'EA_CLUB_MODEL_UNAVAILABLE' });
      }
      const model = new globalThis.UTBucketedItemSearchViewModel();
      const criteria = model.searchCriteria;
      criteria.offset = Math.max(0, Number(params.start ?? 0));
      criteria.count = Math.max(1, Math.min(Number(params.count ?? 100), 100));
      const response = await observeOnce(appServices.Club.search(criteria));
      const payload = response.data ?? response.response ?? {};
      const items = resultItems(response).map(serializeItem);
      return {
        itemData: items,
        retrievedAll: Boolean(payload.retrievedAll ?? payload.endOfList ?? items.length < criteria.count),
        status: response.status ?? null,
      };
    },

    async getUnassigned() {
      const appServices = requireWebAppServices();
      const response = await observeOnce(appServices.Item.requestUnassignedItems());
      return { itemData: resultItems(response).map(serializeItem), status: response.status ?? null };
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
      const loaded = await loadItemById(params.item_id);
      const response = await observeOnce(
        loaded.appServices.Item.move([loaded.item], globalThis.ItemPile.TRANSFER, false)
      );
      return { status: response.status ?? null, item_id: Number(params.item_id) };
    },

    async sendToClub(params) {
      const loaded = await loadItemById(params.item_id);
      const response = await observeOnce(
        loaded.appServices.Item.move([loaded.item], globalThis.ItemPile.CLUB, false)
      );
      return { status: response.status ?? null, item_id: Number(params.item_id) };
    },

    async getTradepile() {
      const appServices = requireWebAppServices();
      const response = await observeOnce(appServices.Item.requestTransferItems());
      return { auctionInfo: resultItems(response).map(serializeAuction), status: response.status ?? null };
    },

    async getWatchlist() {
      const appServices = requireWebAppServices();
      const response = await observeOnce(appServices.Item.requestWatchedItems());
      return { auctionInfo: resultItems(response).map(serializeAuction), status: response.status ?? null };
    },

    async relistAll() {
      const appServices = requireWebAppServices();
      const response = await observeOnce(appServices.Item.relistExpiredAuctions());
      return { status: response.status ?? null };
    },

    async clearSold() {
      const appServices = requireWebAppServices();
      const response = await observeOnce(appServices.Item.clearSoldItems(false));
      return { status: response.status ?? null };
    },

    async getSbcSets() {
      const loaded = await loadSbcSets();
      return { sets: loaded.sets.map(plainSbcSet), status: loaded.status };
    },

    async getSbcChallenges(params) {
      const loaded = await loadSbcChallenges(params.set_id);
      return {
        set: plainSbcSet(loaded.set),
        challenges: loaded.challenges.map(plainSbcChallenge),
        status: loaded.status,
      };
    },

    async getSbcChallenge(params) {
      if (params.set_id != null) {
        const loaded = await loadSbcChallenges(params.set_id);
        const challenge = loaded.challenges.find((entry) => String(readValue(entry, ['id', 'challengeId'])) === String(params.challenge_id));
        if (!challenge) throw Object.assign(new Error(`SBC challenge ${params.challenge_id} was not found.`), { code: 'SBC_CHALLENGE_NOT_FOUND' });
        return { set: plainSbcSet(loaded.set), challenge: plainSbcChallenge(challenge), status: loaded.status };
      }
      const loaded = await loadSbcSets();
      for (const set of loaded.sets) {
        const result = await requestSbcChallenges(loaded.appServices, set);
        const challenge = result.challenges.find((entry) => String(readValue(entry, ['id', 'challengeId'])) === String(params.challenge_id));
        if (challenge) return { set: plainSbcSet(set), challenge: plainSbcChallenge(challenge), status: result.status };
      }
      throw Object.assign(new Error(`SBC challenge ${params.challenge_id} was not found.`), { code: 'SBC_CHALLENGE_NOT_FOUND' });
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
