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

  function announceAccountChange(rawUrl, method, status) {
    const normalizedMethod = String(method || 'GET').toUpperCase();
    if (normalizedMethod === 'GET' || normalizedMethod === 'HEAD') return;
    if (Number(status) < 200 || Number(status) >= 300) return;
    if (!detectApiBase(rawUrl)) return;
    let url;
    let path;
    try {
      url = new URL(rawUrl, window.location.href);
      path = url.pathname;
    }
    catch { return; }
    if (
      normalizedMethod === 'POST'
      && /\/sbs\/challenge\/\d+\/?$/.test(path)
      && !url.searchParams.has('skipUserSquadValidation')
    ) return;
    const mutationPaths = ['/auctionhouse', '/item', '/tradepile', '/sbs/', '/packs/', '/scmp/', '/academy/'];
    if (!mutationPaths.some((value) => path.includes(value))) return;
    window.postMessage({
      source: SOURCE_PAGE,
      type: 'FC27_ACCOUNT_CHANGED',
      change: {
        method: normalizedMethod,
        path,
        status: Number(status),
        observed_at: new Date().toISOString(),
      },
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
      const url = typeof input === 'string' || input instanceof URL ? String(input) : input?.url;
      const method = String(init.method || (input instanceof Request ? input.method : 'GET')).toUpperCase();
      try {
        const baseHeaders = input instanceof Request ? input.headers : undefined;
        const merged = new Headers(baseHeaders || {});
        new Headers(init.headers || {}).forEach((value, key) => merged.set(key, value));
        capture(url, merged);
      } catch {
        // Observation failures must never break the EA web app request.
      }
      return original(input, init).then((response) => {
        try { announceAccountChange(url, method, response.status); }
        catch { /* Observation failures must never break the EA web app response. */ }
        return response;
      });
    };
  }

  function observeXhr() {
    const originalOpen = XMLHttpRequest.prototype.open;
    const originalSetHeader = XMLHttpRequest.prototype.setRequestHeader;
    const originalSend = XMLHttpRequest.prototype.send;

    XMLHttpRequest.prototype.open = function fc27Open(method, url, ...rest) {
      this.__fc27Url = url;
      this.__fc27Method = String(method || 'GET').toUpperCase();
      this.__fc27Headers = {};
      this.__fc27ChangeObserved = false;
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
        if (!this.__fc27ChangeObserved) {
          this.__fc27ChangeObserved = true;
          this.addEventListener('loadend', () => {
            try { announceAccountChange(this.__fc27Url, this.__fc27Method, this.status); }
            catch { /* No-op. */ }
          }, { once: true });
        }
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

  function collectionValues(value) {
    if (Array.isArray(value)) return value;
    if (typeof value?.values === 'function') {
      const values = value.values();
      return Array.isArray(values) ? values : Array.from(values || []);
    }
    return [];
  }

  function plainObjectiveRewards(value) {
    const rewardSets = collectionValues(value);
    const sets = rewardSets.length ? rewardSets : value && typeof value === 'object' ? [value] : [];
    const rewards = [];
    for (const set of sets) {
      for (const reward of collectionValues(readValue(set, ['rewards']))) {
        rewards.push({
          type: readValue(reward, ['type']),
          value: readValue(reward, ['value']),
          count: readValue(reward, ['count']),
          asset_id: readValue(reward, ['assetId']),
          game_mode: readValue(reward, ['gameMode']),
          tradable: readValue(reward, ['tradable']),
          premium: readValue(reward, ['isPremium']),
          is_xp: readValue(reward, ['isXP']) === true
            || String(readValue(reward, ['type']) || '').toLowerCase() === 'season_xp',
        });
      }
    }
    return rewards;
  }

  function plainSeasonTrack(level, currentXp) {
    if (!level) return null;
    const requiredXp = Number(readValue(level, ['xp', 'completeXP']) ?? 0);
    const claimed = typeof level?.isClaimed === 'function' ? Boolean(level.isClaimed()) : null;
    const claimable = typeof level?.isClaimable === 'function'
      ? Boolean(level.isClaimable(currentXp))
      : currentXp >= requiredXp && claimed !== true;
    return {
      state: readValue(level, ['state', '_state', 'status']),
      unlocked: currentXp >= requiredXp,
      claimable,
      claimed,
      selected_reward_id: readValue(level, ['userSelectedRewardId', 'selectedRewardId']),
      reward_flow: readValue(level, ['rewardSetFlow', 'rewardFlow']),
      rewards: plainObjectiveRewards(readValue(level, ['rewardSets', 'rewards'])),
    };
  }

  function plainSeasonLevels(campaign) {
    if (!campaign) return [];
    const currentXp = Number(readValue(campaign, ['xp', '_xp', 'totalUserXP']) ?? 0);
    const standardByLevel = new Map(
      collectionValues(readValue(campaign, ['levels']))
        .map((level) => [Number(readValue(level, ['id', 'level'])), level])
    );
    const premiumByLevel = new Map(
      collectionValues(readValue(campaign, ['premiumLevels']))
        .map((level) => [Number(readValue(level, ['id', 'level'])), level])
    );
    const levelIds = [...new Set([...standardByLevel.keys(), ...premiumByLevel.keys()])]
      .filter(Number.isFinite)
      .sort((left, right) => left - right);
    return levelIds.map((levelId) => {
      const standard = standardByLevel.get(levelId) ?? null;
      const premium = premiumByLevel.get(levelId) ?? null;
      const requiredXp = Number(readValue(standard ?? premium, ['xp', 'completeXP']) ?? 0);
      return {
        level: levelId,
        required_xp: requiredXp,
        remaining_xp: Math.max(0, requiredXp - currentXp),
        standard: plainSeasonTrack(standard, currentXp),
        premium: plainSeasonTrack(premium, currentXp),
      };
    });
  }

  function plainObjectiveCampaign(campaign) {
    if (!campaign) return null;
    const levelCount = new Set([
      ...collectionValues(readValue(campaign, ['levels'])).map((level) => readValue(level, ['id', 'level'])),
      ...collectionValues(readValue(campaign, ['premiumLevels'])).map((level) => readValue(level, ['id', 'level'])),
    ]).size;
    return {
      id: readValue(campaign, ['id', 'seasonId']),
      title: readValue(campaign, ['title', 'name']),
      subtitle: readValue(campaign, ['subtitle', 'description']),
      start_time: readValue(campaign, ['startTime']),
      end_time: readValue(campaign, ['endTime']),
      current_xp: readValue(campaign, ['xp', '_xp', 'totalUserXP']),
      current_level: readValue(campaign, ['currentLevel', 'currentUserLevel']),
      current_level_threshold: readValue(campaign, ['currentLevelThreshold']),
      has_premium: Boolean(readValue(campaign, ['hasPremium'])),
      user_token_count: readValue(campaign, ['userTokenCount']),
      level_count: levelCount,
    };
  }

  function plainObjectiveTask(task) {
    const target = Number(readValue(task, ['multiplier', 'target', 'requiredProgress']) ?? 0);
    const progress = Number(readValue(task, ['progress', '_progress', 'currentProgress']) ?? 0);
    const redeemed = typeof task?.isRedeemed === 'function' ? Boolean(task.isRedeemed()) : null;
    const completed = redeemed === true
      || (typeof task?.isCompleted === 'function' && Boolean(task.isCompleted()))
      || (target > 0 && progress >= target);
    return {
      id: readValue(task, ['id', 'objectiveId']),
      title: readValue(task, ['title', 'name']),
      description: readValue(task, ['description', 'desc']),
      state: readValue(task, ['state', '_state']),
      progress,
      target,
      completed,
      redeemed,
      claimable: typeof task?.isClaimable === 'function' ? Boolean(task.isClaimable()) : null,
      rewards: plainObjectiveRewards(readValue(task, ['rewards', 'rewardSet'])),
    };
  }

  function plainObjectiveGroup(group, objectiveScope = 'ut') {
    const tasks = collectionValues(
      typeof group?.getObjectives === 'function' ? group.getObjectives() : group?.objectives
    ).map(plainObjectiveTask);
    const rawRequiredTasks = Number(readValue(group, ['requiredObjectivesCount', 'objectivesCompletionCount']) ?? 0);
    const requiredTasks = rawRequiredTasks > 0 ? rawRequiredTasks : tasks.length;
    const completedTasks = tasks.filter((task) => task.completed).length;
    const redeemed = typeof group?.isRedeemed === 'function' ? Boolean(group.isRedeemed()) : null;
    const completed = redeemed === true
      || (typeof group?.isCompleted === 'function' && Boolean(group.isCompleted()))
      || (requiredTasks > 0 && completedTasks >= requiredTasks);
    const compositeId = readValue(group, ['compositeId']);
    return {
      id: readValue(group, ['id', 'groupId']),
      composite_id: compositeId,
      content_type: String(compositeId || '').split('-', 1)[0] || null,
      title: readValue(group, ['title', 'name']),
      subtitle: readValue(group, ['subtitle', 'subTitle']),
      objective_scope: objectiveScope,
      game_mode: readValue(group, ['gameMode']),
      type: readValue(group, ['type', 'groupType']),
      state: readValue(group, ['state', '_state', 'groupState']),
      start_time: readValue(group, ['startTime']),
      end_time: readValue(group, ['endTime']),
      repeatability_mode: readValue(group, ['repeatabilityMode']),
      times_completed: readValue(group, ['timesCompleted']),
      required_tasks: requiredTasks,
      completed_tasks: completedTasks,
      completed,
      redeemed,
      claimable: typeof group?.isClaimable === 'function' ? Boolean(group.isClaimable()) : null,
      locked_by: collectionValues(readValue(group, ['lockedBy', 'lockedByGroupIds'])),
      tasks,
      rewards: plainObjectiveRewards(readValue(group, ['rewards', 'rewardSet'])),
    };
  }

  function plainObjectiveCategory(category, objectiveScope = 'ut') {
    return {
      id: readValue(category, ['id', 'categoryId']),
      name: readValue(category, ['name', 'title']),
      priority: readValue(category, ['priority']),
      groups: collectionValues(
        typeof category?.getGroups === 'function' ? category.getGroups() : category?.groups
      ).map((group) => plainObjectiveGroup(group, objectiveScope)),
    };
  }

  function plainEvolutionAwards(value) {
    return collectionValues(value).map((award) => ({
      type: readValue(award, ['type', 'id']),
      value: readValue(award, ['value', 'delta']),
      count: readValue(award, ['count']),
      max_value: readValue(award, ['maxValue']),
      priority: readValue(award, ['priority']),
    }));
  }

  function plainEvolutionRequirements(value) {
    return collectionValues(value).map((requirement) => ({
      attribute: readValue(requirement, ['attribute']),
      scope: readValue(requirement, ['scope']),
      targets: collectionValues(readValue(requirement, ['targets'])),
    }));
  }

  function plainEvolutionCosts(value) {
    return collectionValues(value).map((cost) => ({
      type: readValue(cost, ['type', 'name']),
      amount: readValue(cost, ['amount', 'funds']),
    }));
  }

  function evolutionSectionName(categoryName) {
    return String(categoryName || '')
      .trim()
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '_')
      .replace(/^_+|_+$/g, '') || 'evolutions';
  }

  function plainEvolutionSlot(slot, category = null, lifecycle = {}) {
    const levels = collectionValues(slot?.levels).map((level) => {
      const choices = readValue(level, ['userSelectableAwards']);
      return {
        index: readValue(level, ['id', 'levelId', 'level']),
        name: readValue(level, ['name', 'title']),
        state: readValue(level, ['state', '_state', 'status']),
        completed: typeof level?.isComplete === 'function' ? Boolean(level.isComplete()) : null,
        claimable: typeof level?.isClaimable === 'function' ? Boolean(level.isClaimable()) : null,
        selected_award_index: readValue(level, ['selectedAwardIndex']),
        has_upgrade_choices: Boolean(choices && Object.keys(choices).length),
        objectives: collectionValues(level?.objectives).map(plainObjectiveTask),
        ea_rewards: plainEvolutionAwards(readValue(level, ['awards', 'rewards'])),
      };
    });
    const itemId = Number(readValue(slot, ['id', 'slotId']));
    const player = readValue(slot, ['player']);
    const categoryName = readValue(category, ['description', 'name']);
    const status = readValue(slot, ['status', '_status']);
    const active = typeof slot?.isActive === 'function' ? Boolean(slot.isActive()) : lifecycle.active === true;
    const started = typeof slot?.isStarted === 'function'
      ? Boolean(slot.isStarted())
      : active || lifecycle.inactive === true;
    const completed = typeof slot?.isSlotComplete === 'function'
      ? Boolean(slot.isSlotComplete())
      : levels.length > 0 && levels.every((level) => level.completed === true);
    const expired = lifecycle.expired === true
      || (typeof slot?.hasEndTimeExpired === 'function' && Boolean(slot.hasEndTimeExpired()));
    return {
      id: itemId,
      ea_id: itemId,
      name: readValue(slot, ['slotName', 'name', 'title']),
      description: readValue(slot, ['slotDescription', 'description']),
      category_id: readValue(slot, ['categoryId']),
      category_name: categoryName,
      display_group: started ? 'my_evolutions' : evolutionSectionName(categoryName),
      status,
      timed: Boolean(readValue(slot, ['timed'])),
      enrollment_end_time: readValue(slot, ['endTimePurchaseVisibility']),
      end_time: readValue(slot, ['endTime']),
      repeatability_count: readValue(slot, ['numberOfRepetitions']),
      remaining_repetitions: typeof slot?.getRemainingRepetitions === 'function'
        ? slot.getRemainingRepetitions()
        : null,
      refresh_period: readValue(slot, ['refreshPeriod']),
      repetition_index: readValue(slot, ['repetitionIndex']),
      real_player_id: readValue(slot, ['realPlayerId']),
      training_time: readValue(slot, ['trainingTime', 'readableTrainingTime']),
      active,
      started,
      paused: lifecycle.inactive === true || String(status || '').toUpperCase() === 'INACTIVE',
      claimable: lifecycle.reward_ready === true
        || (typeof slot?.isClaimable === 'function' ? Boolean(slot.isClaimable()) : false),
      completed,
      expired,
      availability: started ? 'account_started' : 'account_available',
      player: typeof player?.isValid === 'function' && player.isValid() ? serializeItem(player) : null,
      ea_requirements: plainEvolutionRequirements(readValue(slot, ['eligibilityRequirements'])),
      costs: plainEvolutionCosts(readValue(slot, ['prices'])),
      lifecycle_evidence: lifecycle,
      levels,
    };
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

  function isSbcSlotRequirements(value) {
    return Array.isArray(value)
      && value.length > 0
      && value.every((entry) => (
        entry
        && typeof entry === 'object'
        && Number.isInteger(Number(entry.index))
        && Object.prototype.hasOwnProperty.call(entry, 'playerType')
      ));
  }

  function directArray(value, keys) {
    if (!value || typeof value !== 'object') return null;
    for (const key of keys) {
      if (Array.isArray(value[key])) return value[key];
    }
    return null;
  }

  function sbcSlotMetadata(challenge, detailPayload = null, squad = null) {
    const slotRequirements = [
      detailPayload?.playerRequirements,
      detailPayload?.playerrequirements,
      detailPayload?.slotRequirements,
      detailPayload?.squad?.playerRequirements,
      detailPayload?.squad?.playerrequirements,
      detailPayload?.squad?.slotRequirements,
      squad?.playerRequirements,
      squad?.playerrequirements,
      squad?.slotRequirements,
      challenge?.playerRequirements,
      challenge?.playerrequirements,
      challenge?.slotRequirements,
    ].find(isSbcSlotRequirements) || null;
    if (slotRequirements) {
      const slotIndices = [...new Set(
        slotRequirements
          .filter((entry) => (
            Number(entry.index) >= 0
            && Number(entry.index) < 11
            && String(entry.playerType || 'DEFAULT').toUpperCase() !== 'BRICK'
          ))
          .map((entry) => Number(entry.index))
      )].sort((left, right) => left - right);
      if (slotIndices.length) {
        return {
          slot_indices: slotIndices,
          slot_indices_source: 'ea_player_requirements',
          slot_requirements: plainValue(slotRequirements, 0, new WeakSet(), 8),
        };
      }
    }
    const brickIndices = directArray(squad, ['simpleBrickIndices', 'brickIndices'])
      || directArray(detailPayload?.squad, ['simpleBrickIndices', 'brickIndices'])
      || directArray(detailPayload, ['simpleBrickIndices', 'brickIndices']);
    if (brickIndices) {
      const bricks = new Set(
        brickIndices
          .map(Number)
          .filter((index) => Number.isInteger(index) && index >= 0 && index < 11)
      );
      return {
        slot_indices: Array.from({ length: 11 }, (_, index) => index)
          .filter((index) => !bricks.has(index)),
        slot_indices_source: 'ea_simple_brick_indices',
        slot_requirements: null,
      };
    }
    return {
      slot_indices: null,
      slot_indices_source: null,
      slot_requirements: null,
    };
  }

  function plainSbcChallenge(challenge, detailPayload = null, squad = null) {
    const requirements = challenge?.eligibilityRequirements || challenge?.requirements || [];
    const slotMetadata = sbcSlotMetadata(challenge, detailPayload, squad);
    const declaredPlayerCount = readValue(
      challenge,
      ['playerCount', 'requiredPlayerCount', 'maxPlayers', 'squadSize', 'numberOfPlayers']
    );
    const raw = plainValue(challenge, 0, new WeakSet(), 8);
    if (raw && typeof raw === 'object') delete raw.squad;
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
      challenge_type: readValue(challenge, ['type', 'challengeType']),
      player_count: declaredPlayerCount ?? slotMetadata.slot_indices?.length ?? null,
      slot_indices: slotMetadata.slot_indices,
      slot_indices_source: slotMetadata.slot_indices_source,
      slot_requirements: slotMetadata.slot_requirements,
      formation: plainValue(readValue(challenge, ['formation', 'formationData']), 0, new WeakSet(), 8),
      slots: plainValue(readValue(challenge, ['slots', 'squadSlots', 'positions']), 0, new WeakSet(), 8),
      rewards: plainValue(readValue(challenge, ['rewards', 'awards']), 0, new WeakSet(), 8),
      requirements: plainValue(requirements, 0, new WeakSet(), 8),
      raw,
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
            slot_index: Number(readValue(entry, ['index', 'slotIndex']) ?? slotIndex),
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

  function savedSbcSlotIndices(squad) {
    const normalized = sbcSquadReadback(squad);
    return (normalized?.players || []).map((entry) => Number(entry.slot_index));
  }

  function sbcSquadReadback(squad, eligibility = null) {
    if (!squad) return null;
    const players = typeof squad.getPlayers === 'function' ? squad.getPlayers() : squad.players;
    const normalizedPlayers = Array.isArray(players)
      ? players.map((entry, slotIndex) => {
        const item = typeof entry?.getItem === 'function' ? entry.getItem() : entry?.item;
        return {
          slot_index: Number(readValue(entry, ['index', 'slotIndex']) ?? slotIndex),
          position: readValue(entry, ['position', 'positionId'], ['getPosition']),
          item: item ? serializeItem(item) : null,
        };
      })
        .filter((entry) => Number(entry.item?.item_id) > 0)
        .sort((left, right) => left.slot_index - right.slot_index)
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

  function sbcActionReadback(set, challenge, squad, eligibility, status = null, detailPayload = null) {
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
      challenge: plainSbcChallenge(challenge, detailPayload, squad),
      squad: sbcSquadReadback(squad, eligibility),
      saved_item_ids: savedSbcItemIds(squad),
      saved_slot_indices: savedSbcSlotIndices(squad),
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

  async function loadObjectives() {
    const appServices = requireWebAppServices();
    const objectiveService = appServices.Objectives;
    objectiveService.flushCategoryCache?.();
    const utResponse = await observeOnce(objectiveService.requestCategories());
    const utPayload = utResponse.data ?? utResponse.response ?? {};
    const categories = collectionValues(utPayload.categories ?? utPayload)
      .map((category) => plainObjectiveCategory(category, 'ut'));
    const objectiveSources = [{
      id: 'ut',
      name: 'Ultimate Team Objectives',
      status: utResponse.status ?? null,
    }];
    let campaign = null;

    if (objectiveService.isMetaFeatureEnabled?.()) {
      objectiveService.flushCampaignCache?.();
      const campaignResponse = await observeOnce(objectiveService.requestCampaignDetails());
      const campaignPayload = campaignResponse.data ?? campaignResponse.response ?? {};
      campaign = campaignPayload.campaign ?? null;

      const groupsResponse = await observeOnce(objectiveService.requestMetaObjectiveGroups());
      const groupsPayload = groupsResponse.data ?? groupsResponse.response ?? {};
      let metaGroups = collectionValues(groupsPayload.metaGroups ?? groupsPayload.groups ?? groupsPayload);
      let progressStatus = null;
      const campaignId = readValue(campaign, ['id', 'seasonId']);
      if (campaignId !== null) {
        const progressResponse = await observeOnce(objectiveService.requestCampaignProgress(campaignId));
        const progressPayload = progressResponse.data ?? progressResponse.response ?? {};
        const progressGroups = collectionValues(progressPayload.groups);
        if (progressGroups.length) metaGroups = progressGroups;
        campaign = progressPayload.campaign ?? campaign;
        progressStatus = progressResponse.status ?? null;
      }

      categories.push({
        id: 'fc-meta',
        name: 'FC Objectives',
        priority: -1,
        groups: metaGroups.map((group) => plainObjectiveGroup(group, 'fc')),
      });
      objectiveSources.push({
        id: 'fc',
        name: 'FC Objectives',
        status: groupsResponse.status ?? null,
        progress_status: progressStatus,
      });
    }

    const sections = categories.map((category) => ({
      id: category.id,
      name: category.name,
      priority: category.priority,
      group_count: category.groups.length,
      completed_count: category.groups.filter((group) => group.completed).length,
      claimable_count: category.groups.filter((group) => group.claimable === true).length,
    }));

    return {
      categories,
      campaign: plainObjectiveCampaign(campaign),
      season_levels: plainSeasonLevels(campaign),
      sections,
      objective_sources: objectiveSources,
      status: utResponse.status ?? null,
    };
  }

  async function loadEvolutionSlots() {
    const appServices = requireWebAppServices();
    if (!appServices.Academy?.isFeatureEnabled?.()) {
      throw Object.assign(new Error('FC27 Evolutions are not enabled for this account.'), { code: 'EA_EVOLUTIONS_DISABLED' });
    }
    appServices.Academy.reset?.();
    const states = globalThis.AcademySlotState || {};
    const requestedStates = [states.NOT_STARTED, states.STARTED].filter((value, index, values) => (
      value !== undefined && value !== null && values.indexOf(value) === index
    ));
    if (!requestedStates.length) requestedStates.push(null);
    const slotsById = new Map();
    const categoriesById = new Map();
    const lifecycleSets = {
      active: new Set(),
      active_timed: new Set(),
      inactive: new Set(),
      reward_ready: new Set(),
      expired: new Set(),
      disabled: new Set(),
      disabled_with_progress: new Set(),
      stripped: new Set(),
    };
    const lifecycleFields = {
      activeSlotIds: 'active',
      activeTimedSlotIds: 'active_timed',
      inactiveSlotIds: 'inactive',
      rewardReadySlotIds: 'reward_ready',
      expiredSlotIds: 'expired',
      disabledSlotIds: 'disabled',
      disabledWithProgressSlotIds: 'disabled_with_progress',
      strippedSlotIds: 'stripped',
    };
    let status = null;

    function recordLifecycle(value) {
      if (!value || typeof value !== 'object') return;
      for (const [field, lifecycleKey] of Object.entries(lifecycleFields)) {
        for (const itemId of value[field] || []) {
          const normalized = Number(itemId);
          if (Number.isFinite(normalized)) lifecycleSets[lifecycleKey].add(normalized);
        }
      }
    }

    function recordPayload(payload) {
      recordLifecycle(payload);
      for (const category of collectionValues(payload.categories)) {
        categoriesById.set(String(readValue(category, ['id', 'categoryId'])), category);
      }
      for (const slot of [...collectionValues(payload.slots), ...collectionValues(payload.temporarySlots)]) {
        const itemId = Number(readValue(slot, ['id', 'slotId']));
        if (Number.isFinite(itemId)) slotsById.set(String(itemId), slot);
      }
    }

    async function requestPages(requestPage, params) {
      const count = 100;
      for (let page = 0; page < 20; page += 1) {
        const response = await observeOnce(requestPage({
          ...params,
          count,
          offset: page * count,
          sort: 0,
          forceFetch: true,
        }));
        status = response.status ?? status;
        const payload = response.data ?? response.response ?? {};
        recordPayload(payload);
        const pageSlots = collectionValues(payload.slots);
        if (payload.isFull === true || pageSlots.length < count) break;
      }
    }

    for (const slotStatus of requestedStates) {
      await requestPages(
        (params) => appServices.Academy.requestAcademyHub(params),
        { slotStatus }
      );
    }
    for (const category of categoriesById.values()) {
      const categoryId = readValue(category, ['id', 'categoryId']);
      await requestPages(
        (params) => appServices.Academy.requestSlotsByCategory(params),
        { categoryId }
      );
    }

    recordLifecycle(globalThis.repositories?.Academy);
    const lifecycle = Object.fromEntries(
      Object.entries(lifecycleSets).map(([key, ids]) => [
        key,
        [...ids].sort((left, right) => left - right),
      ])
    );
    const evolutions = Array.from(slotsById.values()).map((slot) => {
      const itemId = Number(readValue(slot, ['id', 'slotId']));
      const category = categoriesById.get(String(readValue(slot, ['categoryId'])));
      const evidence = Object.fromEntries(
        Object.entries(lifecycleSets).map(([key, ids]) => [key, ids.has(itemId)])
      );
      return plainEvolutionSlot(slot, category, evidence);
    });
    return {
      evolutions,
      categories: Array.from(categoriesById.values()).map((category) => ({
        id: readValue(category, ['id', 'categoryId']),
        name: readValue(category, ['description', 'name']),
        count: readValue(category, ['count']),
      })),
      lifecycle,
      status,
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

  async function loadChallengeState(appServices, challenge, timeoutMs = 30000) {
    const response = await observeOnce(appServices.SBC.loadChallenge(challenge), timeoutMs);
    const payload = response.data ?? response.response ?? {};
    const squad = payload.squad ?? challenge.squad ?? null;
    if (!squad) {
      throw Object.assign(new Error(`SBC challenge ${challenge.id} did not return a squad.`), { code: 'SBC_SQUAD_UNAVAILABLE' });
    }
    challenge.squad = squad;
    return { payload, squad, status: response.status ?? null };
  }

  async function loadPlainSbcChallenge(appServices, challenge) {
    try {
      const state = await loadChallengeState(appServices, challenge, 15000);
      return plainSbcChallenge(challenge, state.payload, state.squad);
    } catch (error) {
      const completed = typeof challenge?.isCompleted === 'function'
        ? Boolean(challenge.isCompleted())
        : String(readValue(challenge, ['status'])).toUpperCase() === 'COMPLETED';
      if (!completed) throw error;
      return {
        ...plainSbcChallenge(challenge),
        slot_layout_error: {
          code: error?.code || 'SBC_SLOT_LAYOUT_UNAVAILABLE',
          status: error?.status ?? null,
          message: error?.message || String(error),
        },
      };
    }
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

    async getObjectives() {
      return loadObjectives();
    },

    async getEvolutions() {
      return loadEvolutionSlots();
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
      const challenges = [];
      for (const challenge of loaded.challenges) {
        challenges.push(await loadPlainSbcChallenge(loaded.appServices, challenge));
      }
      return {
        set: plainSbcSet(loaded.set),
        challenges,
        status: loaded.status,
      };
    },

    async getSbcChallenge(params) {
      if (params.set_id != null) {
        const loaded = await loadSbcChallenges(params.set_id);
        const challenge = loaded.challenges.find((entry) => String(readValue(entry, ['id', 'challengeId'])) === String(params.challenge_id));
        if (!challenge) throw Object.assign(new Error(`SBC challenge ${params.challenge_id} was not found.`), { code: 'SBC_CHALLENGE_NOT_FOUND' });
        return {
          set: plainSbcSet(loaded.set),
          challenge: await loadPlainSbcChallenge(loaded.appServices, challenge),
          status: loaded.status,
        };
      }
      const loaded = await loadSbcSets();
      for (const set of loaded.sets) {
        const result = await requestSbcChallenges(loaded.appServices, set);
        const challenge = result.challenges.find((entry) => String(readValue(entry, ['id', 'challengeId'])) === String(params.challenge_id));
        if (challenge) {
          return {
            set: plainSbcSet(set),
            challenge: await loadPlainSbcChallenge(result.appServices, challenge),
            status: result.status,
          };
        }
      }
      throw Object.assign(new Error(`SBC challenge ${params.challenge_id} was not found.`), { code: 'SBC_CHALLENGE_NOT_FOUND' });
    },

    async saveSbcSquad(params) {
      const loaded = await findSbcChallenge(params.set_id, params.challenge_id);
      const items = await exactOwnedItems(loaded.appServices, params.item_ids || []);
      const state = await loadChallengeState(loaded.appServices, loaded.challenge);
      const squad = state.squad;
      const liveSlotIndices = sbcSlotMetadata(
        loaded.challenge,
        state.payload,
        squad
      ).slot_indices;
      const expectedSlotIndices = (params.slot_indices || []).map(Number);
      if (
        !liveSlotIndices
        || liveSlotIndices.length !== expectedSlotIndices.length
        || liveSlotIndices.some((slotIndex, index) => slotIndex !== expectedSlotIndices[index])
      ) {
        throw Object.assign(new Error('The live SBC fillable slots do not match the persisted solution.'), {
          code: 'SBC_SLOT_LAYOUT_MISMATCH',
          payload: { live_slot_indices: liveSlotIndices, expected_slot_indices: expectedSlotIndices },
        });
      }
      squad.removeAllItems();
      squad.setPlayers(items, true);
      const placedSlotIndices = savedSbcSlotIndices(squad);
      const placedItemIds = savedSbcItemIds(squad);
      if (
        placedSlotIndices.length !== expectedSlotIndices.length
        || placedSlotIndices.some((slotIndex, index) => slotIndex !== expectedSlotIndices[index])
        || placedItemIds.length !== items.length
        || placedItemIds.some((itemId, index) => itemId !== Number(params.item_ids[index]))
      ) {
        throw Object.assign(new Error('EA did not place the SBC items into the expected fillable slots.'), {
          code: 'SBC_SLOT_PLACEMENT_MISMATCH',
          payload: {
            placed_slot_indices: placedSlotIndices,
            expected_slot_indices: expectedSlotIndices,
            placed_item_ids: placedItemIds,
          },
        });
      }
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
          response.status ?? null,
          response.data ?? response.response ?? null
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
        challenge: await loadPlainSbcChallenge(loaded.appServices, loaded.challenge),
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
      const savedIds = savedSbcItemIds(squad);
      const savedSlotIndices = savedSbcSlotIndices(squad);
      const expectedIds = (params.item_ids || []).map(Number);
      const expectedSlotIndices = (params.slot_indices || []).map(Number);
      if (
        savedIds.length !== expectedIds.length
        || savedIds.some((itemId, index) => itemId !== expectedIds[index])
        || savedSlotIndices.length !== expectedSlotIndices.length
        || savedSlotIndices.some((slotIndex, index) => slotIndex !== expectedSlotIndices[index])
      ) {
        throw Object.assign(new Error('The saved SBC squad does not match the confirmed item order.'), {
          code: 'SBC_SAVED_SQUAD_MISMATCH',
          payload: {
            saved_item_ids: savedIds,
            expected_item_ids: expectedIds,
            saved_slot_indices: savedSlotIndices,
            expected_slot_indices: expectedSlotIndices,
          },
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
        submitted_slot_indices: savedSlotIndices,
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
