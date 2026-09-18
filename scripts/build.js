import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const source = path.join(root, 'extension');
const target = path.join(root, 'dist');

await fs.rm(target, { recursive: true, force: true });
await fs.cp(source, target, { recursive: true });
console.log(`FC27 extension copied to ${target}`);
