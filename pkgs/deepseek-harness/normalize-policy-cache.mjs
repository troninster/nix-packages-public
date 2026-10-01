import { readFileSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

// pnpm 11.7's native byHash lookup rechecks the lockfile and policy. Preserve
// its actual online proof, not registry metadata or a manufactured exemption.
// https://github.com/pnpm/pnpm/blob/v11.7.0/installing/deps-installer/src/install/verifyLockfileResolutionsCache.ts
export function normalizePolicyCache(contents, lockfilePath) {
  const records = contents.split('\n').filter(line => line.trim()).map(line => JSON.parse(line))
    .filter(record => record.lockfile?.path === resolve(lockfilePath));
  if (!records.length) throw new Error('Missing online lockfile verification proof');
  const hashes = new Set();
  const lines = records.map(record => {
    const { hash } = record.lockfile;
    const policy = record.policy;
    if (typeof hash !== 'string' || !hash
        || !policy || typeof policy !== 'object' || Array.isArray(policy)
        || !Number.isFinite(policy.minimumReleaseAge) || policy.minimumReleaseAge <= 0
        || !Array.isArray(policy.minimumReleaseAgeExclude)
        || policy.minimumReleaseAgeExclude.some(value => typeof value !== 'string')
        || policy.tarballUrlBinding !== true || policy.resolutionShapeCheck !== true
        || policy.dependencyAliasCheck !== true) {
      throw new Error('Invalid online lockfile verification proof');
    }
    hashes.add(hash);
    return JSON.stringify({
      lockfile: { hash, path: '/verified/pnpm-lock.yaml', size: -1, mtimeNs: '', inode: '' },
      verifiedAt: '',
      policy: Object.fromEntries(Object.keys(policy).sort().map(key => [key, policy[key]])),
    });
  });
  if (hashes.size !== 1) throw new Error('Conflicting online lockfile verification proofs');
  return [...new Set(lines)].sort().join('\n') + '\n';
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  try {
    const [input, lockfile, output, ...extra] = process.argv.slice(2);
    if (!input || !lockfile || !output || extra.length) throw new Error('Invalid arguments');
    writeFileSync(output, normalizePolicyCache(readFileSync(input, 'utf8'), lockfile));
  } catch {
    console.error('Invalid pnpm online lockfile verification proof.');
    process.exitCode = 1;
  }
}
