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
      description: readValue(set, ['description', 'desc']),
      status: readValue(set, ['status']),
      expires: readValue(set, ['expires', 'endTime']),
      repeatable: Boolean(readValue(set, ['repeatable', 'isRepeatable'])),
      completed: Boolean(readValue(set, ['completed'], ['isComplete'])),
      completed_count: readValue(set, ['challengesCompletedCount', 'completedCount']),
      times_completed: readValue(set, ['timesCompleted']),
      rewards: plainValue(readValue(set, ['rewards', 'awards']), 0, new WeakSet(), 8),
      challenge_count: readValue(set, ['challengesCount']) ?? (Array.isArray(set?.challenges) ? set.challenges.length : null),
      raw: plainValue(set, 0, new WeakSet(), 8),
    };
  }

  function plainValue(value, depth = 0, seen = new WeakSet(), maxDepth = 3) {
    if (value === null || value === undefined || ['string', 'number', 'boolean'].includes(typeof value)) return value ?? null;
    if (typeof value === 'function' || depth > maxDepth) return undefined;
    if (Array.isArray(value)) return value.map((entry) => plainValue(entry, depth + 1, seen, maxDepth)).filter((entry) => entry !== undefined);
    if (typeof value !== 'object' || seen.has(value)) return undefined;
    seen.add(value);
    const output = {};
    for (const key of Object.keys(value)) {
      const normalized = plainValue(value[key], depth + 1, seen, maxDepth);
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
      description: readValue(challenge, ['description', 'desc']),
      status: readValue(challenge, ['status']),
      expires: readValue(challenge, ['expires', 'endTime']),
      repeatable: Boolean(readValue(challenge, ['repeatable', 'isRepeatable'])),
      completed: typeof challenge?.isCompleted === 'function' ? Boolean(challenge.isCompleted()) : Boolean(challenge?.completed),
      times_completed: readValue(challenge, ['timesCompleted']),
      formation: plainValue(readValue(challenge, ['formation', 'formationData']), 0, new WeakSet(), 8),
      slots: plainValue(readValue(challenge, ['slots', 'squadSlots', 'positions']), 0, new WeakSet(), 8),
      rewards: plainValue(readValue(challenge, ['rewards', 'awards']), 0, new WeakSet(), 8),
      requirements: plainValue(requirements, 0, new WeakSet(), 8),
      raw: plainValue(challenge, 0, new WeakSet(), 8),
    };
  }

  function plainSbcSquad(squad) {
    if (!squad) return null;
    const players = typeof squad.getPlayers === 'function' ? squad.getPlayers() : squad.players;
    return {
      formation: readValue(squad, ['formation', 'formationId']),
      rating: readValue(squad, ['rating', 'squadRating'], ['getRating']),
      chemistry: readValue(squad, ['chemistry'], ['getChemistry']),
      eligible: readValue(squad, ['eligible', 'isEligible'], ['isEligible']),
      players: Array.isArray(players)
        ? players.map((entry, slotIndex) => {
          const item = typeof entry?.getItem === 'function' ? entry.getItem() : entry?.item;
          return {
            slot_index: slotIndex,
            position: readValue(entry, ['position', 'positionId'], ['getPosition']),
            item: item ? serializeItem(item) : null,
            raw: plainValue(entry, 0, new WeakSet(), 5),
          };
        })
        : [],
      raw: plainValue(squad, 0, new WeakSet(), 8),
    };
  }

  function currentSbcContext(setId, challengeId) {
    const currentViewController = globalThis.getAppMain?.()
      ?.getRootViewController?.()
      ?.getPresentedViewController?.()
      ?.getCurrentViewController?.();
    const currentController = currentViewController?.getCurrentController?.();
    const overviewController = currentController?._overviewController
      ?? currentController?.leftController;
    const currentSet = overviewController?._set ?? currentController?._set;
    const currentChallenge = overviewController?._challenge;
    const currentSquad = overviewController?._squad ?? currentController?._squad;
    const currentSetId = readValue(currentSet, ['id', 'setId']);
    const currentChallengeId = readValue(currentChallenge, ['id', 'challengeId']);
    if (
      currentSet
      && currentChallenge
      && String(currentSetId) === String(setId)
      && String(currentChallengeId) === String(challengeId)
    ) {
      return {
        controller: currentController,
        set: currentSet,
        challenge: currentChallenge,
        squad: currentSquad ?? currentChallenge.squad,
      };
    }
    return null;
  }

  function savedSbcItemIds(squad) {
    const normalized = sbcSquadReadback(squad);
    return (normalized?.players || [])
      .map((entry) => entry.item?.item_id)
      .filter((itemId) => Number.isFinite(itemId) && itemId > 0);
  }

  function sbcSquadReadback(squad, eligibility = null) {
    if (!squad) return null;
    const players = typeof squad.getPlayers === 'function' ? squad.getPlayers() : squad.players;
    const normalizedPlayers = Array.isArray(players)
      ? players.map((entry, slotIndex) => {
        const item = typeof entry?.getItem === 'function' ? entry.getItem() : entry?.item;
        return {
          slot_index: slotIndex,
          position: readValue(entry, ['position', 'positionId'], ['getPosition']),
          item: item ? serializeItem(item) : null,
        };
      }).filter((entry) => Number(entry.item?.item_id) > 0)
      : [];
    return {
      formation: readValue(squad, ['formation', 'formationId', '_formation']),
      rating: readValue(squad, ['rating', 'squadRating'], ['getRating']),
      chemistry: readValue(squad, ['chemistry'], ['getChemistry']),
      eligible: eligibility?.eligible === true,
      eligibility_evidence: eligibility,
      players: normalizedPlayers,
    };
  }

  function currentSbcSubmissionEvidence(setId, challengeId, challenge) {
    const context = currentSbcContext(setId, challengeId);
    const requirements = challenge?.eligibilityRequirements || challenge?.requirements || [];
    const requirementResults = requirements.map((requirement, index) => ({
      index,
      met: typeof challenge?.isRequirementMet === 'function'
        ? Boolean(challenge.isRequirementMet(requirement))
        : null,
    }));
    const root = context?.controller?.view?.getRootElement?.() || null;
    const submitButton = root
      ? Array.from(root.querySelectorAll('button')).find(
        (button) => String(button.textContent || '').trim().toLowerCase() === 'submit'
      )
      : null;
    const style = submitButton ? globalThis.getComputedStyle?.(submitButton) : null;
    const submitAvailable = Boolean(
      submitButton
      && submitButton.disabled !== true
      && submitButton.getAttribute('aria-disabled') !== 'true'
      && submitButton.hidden !== true
      && style?.display !== 'none'
      && style?.visibility !== 'hidden'
    );
    const allRequirementsMet = requirementResults.length > 0
      && requirementResults.every((value) => value.met === true);
    return {
      eligible: Boolean(context && allRequirementsMet && submitAvailable),
      source: 'ea_challenge_requirements',
      identity_match: Boolean(context),
      requirements: requirementResults,
      all_requirements_met: allRequirementsMet,
      submit_available: submitAvailable,
    };
  }

  function sbcActionReadback(set, challenge, squad, eligibility, status = null) {
    return {
      set: {
        id: readValue(set, ['id', 'setId']),
        name: readValue(set, ['name', 'displayName']),
        repeatable: Boolean(readValue(set, ['repeatable', 'isRepeatable'])),
        completed: Boolean(readValue(set, ['completed'], ['isComplete'])),
        completed_count: readValue(set, ['challengesCompletedCount', 'completedCount']),
        times_completed: readValue(set, ['timesCompleted']),
        rewards: plainValue(readValue(set, ['rewards', 'awards']), 0, new WeakSet(), 8),
      },
      challenge: {
        id: readValue(challenge, ['id', 'challengeId']),
        set_id: readValue(challenge, ['setId']),
        name: readValue(challenge, ['name', 'displayName']),
        status: readValue(challenge, ['status']),
        completed: typeof challenge?.isCompleted === 'function'
          ? Boolean(challenge.isCompleted())
          : Boolean(challenge?.completed),
        repeatable: Boolean(readValue(challenge, ['repeatable', 'isRepeatable'])),
        times_completed: readValue(challenge, ['timesCompleted']),
        rewards: plainValue(readValue(challenge, ['rewards', 'awards']), 0, new WeakSet(), 8),
      },
      squad: sbcSquadReadback(squad, eligibility),
      saved_item_ids: savedSbcItemIds(squad),
      status,
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
    return { appServices, set, challenges, status: response.status ?? null };
  }

  function observeOnce(observable, timeoutMs = 30000) {
    return new Promise((resolve, reject) => {
      if (!observable?.observe) {
        reject(Object.assign(new Error('EA Web App service did not return an observable.'), { code: 'EA_SERVICE_UNAVAILABLE' }));
        return;
      }
      const timeout = setTimeout(() => {
        reject(Object.assign(new Error('EA Web App service did not respond before the local timeout.'), { code: 'EA_SERVICE_TIMEOUT' }));
      }, timeoutMs);
      observable.observe(undefined, (sender, response) => {
        clearTimeout(timeout);
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

  async function findSbcChallenge(setId, challengeId) {
    const loaded = await loadSbcChallenges(setId);
    const challenge = loaded.challenges.find(
      (entry) => String(readValue(entry, ['id', 'challengeId'])) === String(challengeId)
    );
    if (!challenge) {
      throw Object.assign(new Error(`SBC challenge ${challengeId} was not found.`), { code: 'SBC_CHALLENGE_NOT_FOUND' });
    }
    return { ...loaded, challenge };
  }

  async function exactOwnedItems(appServices, itemIds) {
    const ids = itemIds.map(Number);
    const response = await observeOnce(appServices.Item.requestItemsById(ids));
    const byId = new Map(
      resultItems(response).map((item) => [Number(readValue(item, ['id', 'itemId'], ['getId'])), item])
    );
    const missing = ids.filter((itemId) => !byId.has(itemId));
    if (missing.length) {
      throw Object.assign(new Error(`Owned SBC items were not found: ${missing.join(', ')}.`), {
        code: 'ITEM_NOT_FOUND',
        payload: { missing_item_ids: missing },
      });
    }
    const items = ids.map((itemId) => byId.get(itemId));
    const concepts = items
      .filter((item) => Boolean(readValue(item, ['concept', 'isConcept'])))
      .map((item) => Number(readValue(item, ['id', 'itemId'], ['getId'])));
    if (concepts.length) {
      throw Object.assign(new Error(`Concept items cannot be saved to an SBC: ${concepts.join(', ')}.`), {
        code: 'SBC_CONCEPT_ITEM',
        payload: { concept_item_ids: concepts },
      });
    }
    return items;
  }

  async function loadChallengeSquad(appServices, challenge) {
    const response = await observeOnce(appServices.SBC.loadChallenge(challenge));
    const payload = response.data ?? response.response ?? {};
    const squad = payload.squad ?? challenge.squad ?? null;
    if (!squad) {
      throw Object.assign(new Error(`SBC challenge ${challenge.id} did not return a squad.`), { code: 'SBC_SQUAD_UNAVAILABLE' });
    }
    challenge.squad = squad;
    return squad;
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

    async saveSbcSquad(params) {
      const loaded = await findSbcChallenge(params.set_id, params.challenge_id);
      const items = await exactOwnedItems(loaded.appServices, params.item_ids || []);
      const squad = await loadChallengeSquad(loaded.appServices, loaded.challenge);
      squad.removeAllItems();
      squad.setPlayers(items, true);
      const response = await observeOnce(loaded.appServices.SBC.saveChallenge(loaded.challenge));
      return {
        set_id: Number(params.set_id),
        challenge_id: Number(params.challenge_id),
        requested_item_ids: (params.item_ids || []).map(Number),
        status: response.status ?? null,
      };
    },

    async readSavedSbcSquad(params) {
      const loaded = await findSbcChallenge(params.set_id, params.challenge_id);
      const response = await observeOnce(
        loaded.appServices.SBC.sbcDAO.loadChallenge(
          loaded.challenge.id,
          typeof loaded.challenge.isInProgress === 'function'
            ? loaded.challenge.isInProgress()
            : true
        ),
        15000
      );
      const squad = response.data?.squad ?? response.response?.squad ?? null;
      if (!squad) {
        throw Object.assign(
          new Error(`SBC challenge ${params.challenge_id} did not return a saved squad.`),
          { code: 'SBC_SQUAD_UNAVAILABLE' }
        );
      }
      loaded.challenge.squad = squad;
      const eligibility = currentSbcSubmissionEvidence(
        params.set_id,
        params.challenge_id,
        loaded.challenge
      );
      return {
        ...sbcActionReadback(
          loaded.set,
          loaded.challenge,
          squad,
          eligibility,
          response.status ?? null
        ),
        source: 'ea_webapp_fresh',
        freshness: {
          sets_requested: true,
          challenges_requested: true,
          challenge_loaded: true,
        },
      };
    },

    async readSbcSubmissionState(params) {
      const loaded = await findSbcChallenge(params.set_id, params.challenge_id);
      return {
        set: plainSbcSet(loaded.set),
        challenge: plainSbcChallenge(loaded.challenge),
        source: 'ea_webapp_fresh',
        freshness: {
          sets_requested: true,
          challenges_requested: true,
        },
        status: loaded.status,
      };
    },

    async submitSbc(params) {
      const loaded = await findSbcChallenge(params.set_id, params.challenge_id);
      const refreshed = await observeOnce(
        loaded.appServices.SBC.sbcDAO.loadChallenge(
          loaded.challenge.id,
          typeof loaded.challenge.isInProgress === 'function' ? loaded.challenge.isInProgress() : true
        )
      );
      const squad = refreshed.data?.squad ?? refreshed.response?.squad ?? null;
      if (!squad) {
        throw Object.assign(new Error(`SBC challenge ${params.challenge_id} has no saved squad.`), { code: 'SBC_SQUAD_UNAVAILABLE' });
      }
      loaded.challenge.squad = squad;
      const savedIds = (typeof squad.getPlayers === 'function' ? squad.getPlayers() : [])
        .map((entry) => typeof entry?.getItem === 'function' ? entry.getItem() : entry?.item)
        .filter(Boolean)
        .map((item) => Number(readValue(item, ['id', 'itemId'], ['getId'])));
      const expectedIds = (params.item_ids || []).map(Number);
      if (savedIds.length !== expectedIds.length || savedIds.some((itemId, index) => itemId !== expectedIds[index])) {
        throw Object.assign(new Error('The saved SBC squad does not match the confirmed item order.'), {
          code: 'SBC_SAVED_SQUAD_MISMATCH',
          payload: { saved_item_ids: savedIds, expected_item_ids: expectedIds },
        });
      }
      const eligibility = currentSbcSubmissionEvidence(
        params.set_id,
        params.challenge_id,
        loaded.challenge
      );
      if (eligibility.eligible !== true) {
        throw Object.assign(new Error('The saved SBC squad is no longer eligible for submission.'), {
          code: 'SBC_NOT_ELIGIBLE',
          payload: { eligibility_evidence: eligibility },
        });
      }
      const currentCounters = {
        challenge_times_completed: readValue(loaded.challenge, ['timesCompleted']),
        set_times_completed: readValue(loaded.set, ['timesCompleted']),
        set_completed_count: readValue(loaded.set, ['challengesCompletedCount', 'completedCount']),
      };
      const expectedCounters = params.expected_counters || {};
      const changedCounter = Object.entries(expectedCounters).find(
        ([key, expected]) => expected != null
          && (currentCounters[key] == null || Number(currentCounters[key]) !== Number(expected))
      );
      if (changedCounter) {
        throw Object.assign(new Error('The SBC completion counter changed after pre-submit validation.'), {
          code: 'SBC_SUBMIT_BASELINE_MISMATCH',
          payload: {
            counter: changedCounter[0],
            expected: changedCounter[1],
            actual: currentCounters[changedCounter[0]],
          },
        });
      }
      const chemistryEnabled = Boolean(loaded.appServices.Chemistry?.isFeatureEnabled?.());
      const response = await observeOnce(
        loaded.appServices.SBC.submitChallenge(
          loaded.challenge,
          loaded.set,
          true,
          chemistryEnabled
        )
      );
      return {
        submitted_item_ids: savedIds,
        status: response.status ?? null,
        success: response.success !== false,
        data: plainValue(response.data ?? response.response ?? null, 0, new WeakSet(), 8),
      };
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
