import { STORAGE_KEYS } from './constants.js';

const MAX_ENTRIES = 500;

class Logger {
  async _write(level, message, data = {}) {
    const entry = {
      ts: new Date().toISOString(),
      level,
      message,
      data,
    };
    try {
      const current = await chrome.storage.local.get(STORAGE_KEYS.logs);
      const rows = Array.isArray(current[STORAGE_KEYS.logs]) ? current[STORAGE_KEYS.logs] : [];
      rows.push(entry);
      if (rows.length > MAX_ENTRIES) rows.splice(0, rows.length - MAX_ENTRIES);
      await chrome.storage.local.set({ [STORAGE_KEYS.logs]: rows });
    } catch (error) {
      console.warn('[FC27 Copilot] log persistence failed', error);
    }
    const fn = level === 'error' ? console.error : level === 'warn' ? console.warn : console.log;
    fn(`[FC27 Copilot] ${message}`, data);
  }

  debug(message, data) { return this._write('debug', message, data); }
  info(message, data) { return this._write('info', message, data); }
  trade(message, data) { return this._write('trade', message, data); }
  warn(message, data) { return this._write('warn', message, data); }
  error(message, data) { return this._write('error', message, data); }

  async list(limit = 100) {
    const current = await chrome.storage.local.get(STORAGE_KEYS.logs);
    const rows = Array.isArray(current[STORAGE_KEYS.logs]) ? current[STORAGE_KEYS.logs] : [];
    return rows.slice(-Math.max(1, Math.min(Number(limit) || 100, 500))).reverse();
  }
}

export const logger = new Logger();
