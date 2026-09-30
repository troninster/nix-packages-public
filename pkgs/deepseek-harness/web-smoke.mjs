import { spawn } from 'node:child_process';
import { mkdir, mkdtemp } from 'node:fs/promises';
import { join, resolve } from 'node:path';

const [executable, isolationRoot, ...extra] = process.argv.slice(2);
if (!executable || !isolationRoot || extra.length) {
  console.error('Usage: web-smoke.mjs <dsh> <temporary-root>');
  process.exit(1);
}

// Leave five seconds of the 45-second budget for this child's shutdown.
const controller = new AbortController();
const timeout = setTimeout(() => controller.abort(), 40_000);
let child;
let closed;
let passed = false;

async function awaitClose(milliseconds) {
  let timer;
  try {
    return await Promise.race([
      closed.then(() => true),
      new Promise(resolveWait => {
        timer = setTimeout(() => resolveWait(false), milliseconds);
      }),
    ]);
  } finally {
    clearTimeout(timer);
  }
}

try {
  const root = resolve(isolationRoot);
  await mkdir(root, { recursive: true });
  const world = await mkdtemp(join(root, 'web-smoke-'));
  const home = join(world, 'home');
  const workspace = join(world, 'workspace');
  const temporary = join(world, 'tmp');
  await Promise.all([home, workspace, temporary].map(path => mkdir(path)));
  controller.signal.throwIfAborted();

  const ready = Promise.withResolvers();
  controller.signal.addEventListener('abort', () => ready.reject(new Error('timeout')), { once: true });
  child = spawn(executable, ['web', '--no-open', '--host', '127.0.0.1', '--port', '0'], {
    cwd: workspace,
    // Do not inherit provider credentials, user configuration or proxy settings.
    env: {
      PATH: process.env.PATH ?? '',
      HOME: home,
      TMPDIR: temporary,
      XDG_CONFIG_HOME: join(home, 'config'),
      XDG_CACHE_HOME: join(home, 'cache'),
      XDG_DATA_HOME: join(home, 'data'),
      DSH_HOME: join(world, 'harness'),
      DSH_AGENTS_HOME: join(world, 'agents'),
      DSH_TELEMETRY_DISABLED: '1',
      LANG: 'C.UTF-8',
      TZ: 'UTC',
    },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  closed = new Promise(resolveClosed => child.once('close', resolveClosed));
  child.once('error', () => ready.reject(new Error('startup failed')));
  child.once('close', () => ready.reject(new Error('startup stopped')));
  child.stderr.resume();

  // Readiness contains a launch token. Keep it in memory; never print logs.
  let output = '';
  child.stdout.setEncoding('utf8');
  child.stdout.on('data', chunk => {
    output = (output + chunk).slice(-65_536);
    const match = /^dsh web: (http:\/\/[^\s]+)[^\n]*\n/m.exec(output);
    if (match) ready.resolve(match[1]);
  });
  const launchUrl = new URL(await ready.promise);
  if (controller.signal.aborted || launchUrl.protocol !== 'http:'
      || launchUrl.hostname !== '127.0.0.1' || !launchUrl.port
      || launchUrl.searchParams.getAll('token').length !== 1
      || !launchUrl.searchParams.get('token')) throw new Error('invalid readiness');

  const response = await fetch(launchUrl, { redirect: 'manual', signal: controller.signal });
  const cookies = response.headers.getSetCookie().map(value => value.split(';', 1)[0]).join('; ');
  if (response.status !== 303 || !cookies) throw new Error('authentication exchange failed');
  await response.arrayBuffer();

  const baseUrl = new URL('/', launchUrl);
  const page = await fetch(baseUrl, {
    headers: { Cookie: cookies }, redirect: 'error', signal: controller.signal,
  });
  const html = await page.text();
  if (page.status !== 200 || !html.includes('__DSH_BOOT__')) throw new Error('frontend unavailable');
  const moduleTag = [...html.matchAll(/<script\b[^>]*>/gi)]
    .find(([tag]) => /\btype\s*=\s*["']module["']/i.test(tag) && /\bsrc\s*=/i.test(tag));
  const moduleSource = moduleTag?.[0].match(/\bsrc\s*=\s*["']([^"']+)["']/i)?.[1];
  if (!moduleSource) throw new Error('module asset missing');
  const assetUrl = new URL(moduleSource, baseUrl);
  if (assetUrl.origin !== baseUrl.origin || !assetUrl.pathname.endsWith('.js')) {
    throw new Error('module asset is not local');
  }
  const asset = await fetch(assetUrl, {
    headers: { Cookie: cookies }, redirect: 'error', signal: controller.signal,
  });
  if (asset.status !== 200 || !asset.headers.get('content-type')?.includes('javascript')
      || (await asset.arrayBuffer()).byteLength === 0) throw new Error('module asset unavailable');
  passed = true;
} catch {
  // Errors from fetch and child processes can contain tokens or headers.
  passed = false;
} finally {
  clearTimeout(timeout);
  controller.abort();
  if (child) {
    if (child.exitCode === null && child.signalCode === null) {
      child.kill('SIGTERM');
      if (!await awaitClose(3_000)) {
        passed = false;
        child.kill('SIGKILL');
        await awaitClose(2_000);
      }
      if (child.exitCode !== 0 && child.signalCode !== 'SIGTERM') passed = false;
    } else if (child.exitCode !== 0) {
      passed = false;
    }
  }
}

console[passed ? 'log' : 'error'](`DeepSeek Harness isolated web smoke ${passed ? 'passed' : 'failed'}.`);
process.exitCode = passed ? 0 : 1;
