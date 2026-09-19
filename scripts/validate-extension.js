import fs from 'node:fs/promises';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const extension = path.join(root, 'extension');
const errors = [];

async function walk(dir) {
  const out = [];
  for (const entry of await fs.readdir(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) out.push(...await walk(full));
    else out.push(full);
  }
  return out;
}

const manifestPath = path.join(extension, 'manifest.json');
const manifest = JSON.parse(await fs.readFile(manifestPath, 'utf8'));
if (!manifest.name?.includes('FC27')) errors.push('manifest name is not FC27');
if (!manifest.host_permissions?.includes('https://*.ea.com/*')) errors.push('dynamic EA subdomain host permission missing');
if (!manifest.host_permissions?.includes('http://127.0.0.1/*')) errors.push('loopback fc27d host permission missing');
if (!manifest.host_permissions?.includes('http://localhost/*')) errors.push('localhost fc27d host permission missing');
if (manifest.externally_connectable) errors.push('manual externally_connectable bridge must be removed');
if (manifest.permissions?.includes('alarms')) errors.push('thin bridge must not own keepalive scheduling');

const files = await walk(extension);
for (const file of files.filter((f) => f.endsWith('.js'))) {
  const checked = spawnSync(process.execPath, ['--check', file], { encoding: 'utf8' });
  if (checked.status !== 0) errors.push(`${path.relative(root, file)}: ${checked.stderr.trim()}`);
  const text = await fs.readFile(file, 'utf8');
  if (text.includes('/ut/game/fc26')) errors.push(`${path.relative(root, file)} contains a hard-coded FC26 game path`);
}

const obsolete = [
  'background/mcp-server.js',
  'background/rate-limiter.js',
  'background/tools/market-tools.js',
];
for (const relative of obsolete) {
  try {
    await fs.access(path.join(extension, relative));
    errors.push(`obsolete extension module still exists: ${relative}`);
  } catch {
    // Expected.
  }
}

const worker = await fs.readFile(path.join(extension, 'background', 'service-worker.js'), 'utf8');
if (!worker.includes("FC27_BRIDGE_POLL")) errors.push('content-driven daemon poll handler missing');
if (worker.includes('onMessageExternal')) errors.push('legacy external bridge handler still exists');
if (worker.includes('writeToolsEnabled')) errors.push('extension still contains account policy state');

const daemonBridge = await fs.readFile(path.join(extension, 'background', 'daemon.js'), 'utf8');
if (!daemonBridge.includes('/browser/poll')) errors.push('direct daemon poll missing');
if (!daemonBridge.includes('/browser/respond')) errors.push('direct daemon response forwarding missing');
if (!daemonBridge.includes('/browser/event')) errors.push('browser event delivery missing');

const pageBridge = await fs.readFile(path.join(extension, 'background', 'bridge.js'), 'utf8');
if (!pageBridge.includes('for (const tab of tabs)')) errors.push('multi-tab Web App bridge selection missing');

const contentScript = await fs.readFile(path.join(extension, 'content', 'content-script.js'), 'utf8');
if (!contentScript.includes("type: 'FC27_BRIDGE_POLL'")) errors.push('Web App-driven daemon polling missing');

const pageInject = await fs.readFile(path.join(extension, 'content', 'page-inject.js'), 'utf8');
if (!pageInject.includes('const savedIds = savedSbcItemIds(squad);')) {
  errors.push('SBC submit must reuse the positive-item saved squad filter');
}
if (!pageInject.includes("type: 'FC27_ACCOUNT_CHANGED'")) errors.push('account-change observation missing');
if (!pageInject.includes('async getObjectives()')) errors.push('objective reader missing');
if (!pageInject.includes('requestMetaObjectiveGroups')) errors.push('FC Objectives group reader missing');
if (!pageInject.includes('requestCampaignProgress')) errors.push('FC Objectives progress reader missing');
if (!pageInject.includes("typeof group?.isRedeemed === 'function'")) errors.push('redeemed objective-group completion handling missing');
if (!pageInject.includes('plainObjectiveCampaign')) errors.push('compact objective campaign serialization missing');
if (!pageInject.includes('plainObjectiveRewards')) errors.push('compact objective reward serialization missing');
if (!pageInject.includes('plainSeasonLevels')) errors.push('compact FC Season level serialization missing');
if (!pageInject.includes('lifecycleSets')) errors.push('Evolution lifecycle evidence missing');
if (!pageInject.includes('requestPages')) errors.push('Evolution pagination missing');
if (!pageInject.includes("display_group: started ? 'my_evolutions'")) errors.push('My Evolutions display group missing');
if (!pageInject.includes('async getEvolutions()')) errors.push('evolution reader missing');
if (!pageInject.includes('async getSquads(params = {})')) errors.push('squad reader missing');
if (!pageInject.includes('requestSquadList')) errors.push('squad-list request missing');
if (!pageInject.includes('requestSquadById')) errors.push('exact squad request missing');
if (!pageInject.includes('squadTacticsCatalog')) errors.push('squad tactics catalog missing');
if (!pageInject.includes('async setActiveSquad(params)')) errors.push('active-squad writer missing');
if (!pageInject.includes('async saveSquad(params)')) errors.push('squad writer missing');
if (!pageInject.includes('async saveSquadTactics(params)')) errors.push('squad tactics writer missing');
if (!pageInject.includes("const mutationPaths = ['/auctionhouse', '/item', '/tradepile', '/squad'")) errors.push('squad mutation observation missing');
if (!pageInject.includes('requestSlotsByCategory')) errors.push('available evolution category reader missing');

for (const file of files.filter((f) => f.endsWith('.js') && !f.endsWith('content/page-inject.js'))) {
  const text = await fs.readFile(file, 'utf8');
  if (text.includes('X-UT-SID') || text.includes('X-UT-PHISHING-TOKEN')) {
    errors.push(`${path.relative(root, file)} handles raw EA session headers outside page context`);
  }
}

// Check static relative imports exist.
for (const file of files.filter((f) => f.endsWith('.js'))) {
  const text = await fs.readFile(file, 'utf8');
  const re = /from\s+['"](\.[^'"]+)['"]/g;
  let match;
  while ((match = re.exec(text))) {
    const target = path.resolve(path.dirname(file), match[1]);
    try { await fs.access(target); }
    catch { errors.push(`${path.relative(root, file)} imports missing ${match[1]}`); }
  }
}

if (errors.length) {
  console.error(errors.join('\n'));
  process.exit(1);
}
console.log(`Validated manifest + ${files.filter((f) => f.endsWith('.js')).length} JavaScript files.`);
