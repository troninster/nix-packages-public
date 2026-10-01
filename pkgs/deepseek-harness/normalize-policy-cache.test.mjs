import assert from 'node:assert/strict';
import test from 'node:test';
import { normalizePolicyCache } from './normalize-policy-cache.mjs';

const policy = {
  minimumReleaseAge: 1440,
  minimumReleaseAgeExclude: ['reviewed@1.2.3'],
  tarballUrlBinding: true,
  resolutionShapeCheck: true,
  dependencyAliasCheck: true,
  trustPolicy: null,
  trustPolicyExclude: [],
  trustPolicyIgnoreAfter: null,
};
const hash = 'unchanged-native-parsed-lockfile-hash';
const record = (path, timestamp, inode) => ({
  lockfile: { hash, path, size: 123, mtimeNs: timestamp, inode },
  verifiedAt: timestamp,
  policy,
});

test('normalizes only volatile fields and retains native lockfile hash and policy', () => {
  const first = normalizePolicyCache(JSON.stringify(record('/build/source/pnpm-lock.yaml', 'first', '1')), '/build/source/pnpm-lock.yaml');
  const second = normalizePolicyCache(JSON.stringify(record('/other/source/pnpm-lock.yaml', 'second', '2')), '/other/source/pnpm-lock.yaml');
  assert.equal(first, second);
  const normalized = JSON.parse(first);
  assert.equal(normalized.lockfile.hash, hash);
  assert.deepEqual(normalized.policy, policy);
  assert.deepEqual(normalized.lockfile, { hash, path: '/verified/pnpm-lock.yaml', size: -1, mtimeNs: '', inode: '' });
  assert.equal(normalized.verifiedAt, '');
});

test('fails closed without genuine verification for this lockfile and active policy', () => {
  const path = '/build/source/pnpm-lock.yaml';
  assert.throws(() => normalizePolicyCache('', path));
  assert.throws(() => normalizePolicyCache(JSON.stringify(record('/different/pnpm-lock.yaml', 'first', '1')), path));
  const unchecked = record(path, 'first', '1');
  unchecked.policy = { ...policy, minimumReleaseAge: 0 };
  assert.throws(() => normalizePolicyCache(JSON.stringify(unchecked), path));
  unchecked.policy = { ...policy, tarballUrlBinding: false };
  assert.throws(() => normalizePolicyCache(JSON.stringify(unchecked), path));
});
