import { callEA } from './bridge.js';
import { markSessionError } from './session-monitor.js';
import { logger } from '../shared/logger.js';

export async function safeEACall(method, params = {}) {
  try {
    const data = await callEA(method, params);
    return { ok: true, data };
  } catch (error) {
    const status = Number(error?.status || 0);
    await markSessionError(error);

    if (status === 401) {
      await logger.warn('EA session expired; re-login is required.', { method, status });
    } else if (status === 429) {
      await logger.warn('EA rate limit response; local backoff activated.', { method, status });
    } else if (status === 461) {
      await logger.error('EA transfer restriction response; write/read requests are locally locked.', { method, status });
    } else if (status === 403) {
      await logger.warn('EA returned 403. Resolve any verification/captcha manually in the Web App.', { method, status });
    } else {
      await logger.warn('EA call failed', { method, status: status || null, code: error?.code || null });
    }

    return {
      ok: false,
      error: {
        message: String(error?.message || error),
        status: error?.status ?? null,
        code: error?.code ?? null,
        payload: error?.payload ?? null,
      },
    };
  }
}
