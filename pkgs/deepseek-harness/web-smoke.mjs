import { spawn } from 'node:child_process';
import { constants } from 'node:fs';
import { mkdir, mkdtemp, open, readdir } from 'node:fs/promises';
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
let stage = 'isolation';
let failureStage;
let failureDiagnostic;
let harness;
let stderr = '';
let stderrBytes = 0;
let reportStatus = 'absent';
let reportBytes = 0;
let errorClass = 'unclassified';
const stackFrames = new Set();

function observeFailure(text) {
  errorClass = /\b(AggregateError|StartupError|TypeError|SyntaxError|RangeError|ReferenceError|Error)(?=[: \[])/.exec(text)?.[1] ?? errorClass;
  // Only installation-owned, known public runtime files; no store prefix,
  // exception messages, plugin configuration or dynamic filenames are emitted.
  const frames = /\/lib\/deepseek-harness\/(apps\/cli\/lib\/(?:bin|args|profile-boot|startup-diagnostics|process-shutdown)\.js|packages\/boot\/app-boot\/lib\/(?:index|profile|profile-context|compatibility-preflight|profile-resolution\/(?:resolver|service))\.js|vendor\/(?:loader|include|cordis)\/lib\/(?:index|internal|fiber)\.js):(\d{1,7}):(\d{1,5})/g;
  for (const match of text.matchAll(frames)) {
    if (stackFrames.size === 6) break;
    stackFrames.add(`${match[1]}:${match[2]}:${match[3]}`);
  }
}

// Report only constants, never child logs, exception text, paths or launch data.
function startupDiagnostic(text = stderr) {
  const code = /(?:\bcode: ['"]|\[)(ERR_MODULE_NOT_FOUND|MODULE_NOT_FOUND|ERR_DLOPEN_FAILED|ERR_UNKNOWN_FILE_EXTENSION|ERR_UNSUPPORTED_DIR_IMPORT|ERR_PACKAGE_PATH_NOT_EXPORTED|EACCES|EROFS|ENOENT|EADDRINUSE)(?:['"]|\])/.exec(text)?.[1];
  if (code) return code;
  if (text.includes('profile resolution: unsupported Node module loader')) return 'unsupported-node-loader';
  if (text.includes('web-app: @deepseek-ai/dsh-web-frontend is not resolvable')) return 'frontend-package-unresolved';
  if (text.includes('dsh: skipping profile bundle')) return 'profile-bundle-skipped';
  if (text.includes('No usable native binding found for ')) return 'native-binding-unusable';
  if (text.includes('No usable prebuilt binary found in ')) return 'prebuilt-binding-unusable';
  if (text.includes('dsh: host preparation failed:')) return 'host-preparation-failed';
  if (text.includes('dsh: plugin tree failed to load:')) return 'plugin-tree-failed';
  if (text.includes('dsh: startup failed:')) return 'required-plugin-failed';
  if (text.includes('error: --profile <name> is required')) return 'profile-argument-required';
  if (text.includes('error: unknown option')) return 'cli-unknown-option';
  if (text.includes('error: too many arguments') || text.includes('error: unexpected argument')) return 'cli-unexpected-argument';
  return 'unclassified';
}

async function failureDiagnosticFromOwnReport() {
  observeFailure(stderr);
  const diagnostic = startupDiagnostic();
  if (diagnostic !== 'unclassified' || !harness || child?.exitCode == null) return diagnostic;
  let report;
  try {
    const logs = join(harness, 'logs');
    const filename = (await readdir(logs))
      .filter(name => /^startup-[0-9TZ.-]+-[0-9a-f-]+\.log$/.test(name)).sort().at(-1);
    if (!filename) return diagnostic;
    // Only this child's fresh isolation contains these reports. Never emit or
    // copy their raw contents; classify at most 64 KiB in memory.
    report = await open(join(logs, filename), constants.O_RDONLY | constants.O_NOFOLLOW);
    const stat = await report.stat();
    if (!stat.isFile()) return diagnostic;
    reportStatus = 'present';
    reportBytes = stat.size;
    const buffer = Buffer.alloc(65_536);
    const { bytesRead } = await report.read(buffer, 0, buffer.length, 0);
    const text = buffer.toString('utf8', 0, bytesRead);
    observeFailure(text);
    return startupDiagnostic(text);
  } catch {
    return diagnostic;
  } finally {
    await report?.close().catch(() => {});
  }
}

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
  harness = join(world, 'harness');
  await Promise.all([home, workspace, temporary].map(path => mkdir(path)));
  controller.signal.throwIfAborted();

  const ready = Promise.withResolvers();
  controller.signal.addEventListener('abort', () => ready.reject(new Error('timeout')), { once: true });
  stage = 'startup';
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
      DSH_HOME: harness,
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
  child.stderr.setEncoding('utf8');
  child.stderr.on('data', chunk => {
    stderrBytes += Buffer.byteLength(chunk);
    stderr = (stderr + chunk).slice(-65_536);
  });

  // Readiness contains a launch token. Keep it in memory; never print logs.
  let output = '';
  child.stdout.setEncoding('utf8');
  child.stdout.on('data', chunk => {
    output = (output + chunk).slice(-65_536);
    const match = /^dsh web: (http:\/\/[^\s]+)[^\n]*\n/m.exec(output);
    if (match) ready.resolve(match[1]);
  });
  const launchUrl = new URL(await ready.promise);
  stage = 'readiness';
  if (controller.signal.aborted || launchUrl.protocol !== 'http:'
      || launchUrl.hostname !== '127.0.0.1' || !launchUrl.port
      || launchUrl.searchParams.getAll('token').length !== 1
      || !launchUrl.searchParams.get('token')) throw new Error('invalid readiness');

  stage = 'authentication';
  const response = await fetch(launchUrl, { redirect: 'manual', signal: controller.signal });
  const cookies = response.headers.getSetCookie().map(value => value.split(';', 1)[0]).join('; ');
  if (response.status !== 303 || !cookies) throw new Error('authentication exchange failed');
  await response.arrayBuffer();

  stage = 'frontend';
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
  stage = 'module-asset';
  const asset = await fetch(assetUrl, {
    headers: { Cookie: cookies }, redirect: 'error', signal: controller.signal,
  });
  if (asset.status !== 200 || !asset.headers.get('content-type')?.includes('javascript')
      || (await asset.arrayBuffer()).byteLength === 0) throw new Error('module asset unavailable');
  passed = true;
} catch {
  // Errors from fetch and child processes can contain tokens or headers.
  failureStage = stage;
  failureDiagnostic = await failureDiagnosticFromOwnReport();
  passed = false;
} finally {
  clearTimeout(timeout);
  controller.abort();
  if (child) {
    stage = 'shutdown';
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

const diagnostic = passed ? '' : ` Stage: ${failureStage ?? stage}; diagnostic: ${failureDiagnostic ?? startupDiagnostic()}.`;
const exitStatus = !passed && Number.isInteger(child?.exitCode) ? ` Child exit: ${child.exitCode}.` : '';
const evidence = passed ? '' : ` Error class: ${errorClass}; stderr bytes: ${stderrBytes}; own report: ${reportStatus}; report bytes: ${reportBytes}; runtime frames: ${[...stackFrames].join(', ') || 'none'}.`;
console[passed ? 'log' : 'error'](`DeepSeek Harness isolated web smoke ${passed ? 'passed' : 'failed'}.${diagnostic}${exitStatus}${evidence}`);
process.exitCode = passed ? 0 : 1;
