#!/usr/bin/env python3
"""Apply the supported Camofox NixOS compatibility transforms atomically."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import sys
import tempfile


@dataclass(frozen=True)
class Variant:
    name: str
    before: str
    after: str | None


@dataclass(frozen=True)
class Transform:
    label: str
    target: str
    variants: tuple[Variant, ...]


def probe_source(new_context: str) -> str:
    return "\n".join(
        (
            "// Active health probe -- detect hung browser even when isConnected() lies",
            "setInterval(async () => {",
            "  if (!browser || healthState.isRecovering) return;",
            "  const timeSinceSuccess = Date.now() - healthState.lastSuccessfulNav;",
            "  // Skip probe if operations are in flight AND last success was recent.",
            "  // If it's been >120s since any successful operation, probe anyway --",
            "  // active ops are likely stuck on a frozen browser and will time out eventually.",
            "  if (healthState.activeOps > 0 && timeSinceSuccess < 120000) {",
            "    log('info', 'health probe skipped, operations active', { activeOps: healthState.activeOps });",
            "    return;",
            "  }",
            "  if (timeSinceSuccess < 120000) return;",
            "  ",
            "  if (healthState.activeOps > 0) {",
            "    log('warn', 'health probe forced despite active ops', { activeOps: healthState.activeOps, timeSinceSuccessMs: timeSinceSuccess });",
            "  }",
            "  ",
            "  let testContext;",
            "  try {",
            f"    testContext = await browser.{new_context};",
            "    const page = await testContext.newPage();",
            "    await page.goto('about:blank', { timeout: 5000 });",
            "    await page.close();",
            "    await testContext.close();",
            "    healthState.lastSuccessfulNav = Date.now();",
            "  } catch (err) {",
            "    failuresTotal.labels('health_probe', 'internal').inc();",
            "    log('warn', 'health probe failed', { error: err.message, timeSinceSuccessMs: timeSinceSuccess });",
            "    if (testContext) await testContext.close().catch(() => {});",
            "    restartBrowser('health probe failed').catch(() => {});",
            "  }",
            "}, 60_000);",
        )
    )


def patched_probe(new_context: str, reset_nav_failures: bool) -> str:
    lines = [
        "// Active health probe -- detect hung browser even when isConnected() lies",
        "setInterval(async () => {",
        "  if (!browser || healthState.isRecovering || activeHealthProbeInFlight) return;",
        "  if (sessions.size === 0 && getTotalTabCount() === 0) return;",
        "  const timeSinceSuccess = Date.now() - healthState.lastSuccessfulNav;",
        "  // Skip probe if operations are in flight AND last success was recent.",
        "  // If it's been >120s since any successful operation, probe anyway --",
        "  // active ops are likely stuck on a frozen browser and will time out eventually.",
        "  if (healthState.activeOps > 0 && timeSinceSuccess < 120000) {",
        "    log('info', 'health probe skipped, operations active', { activeOps: healthState.activeOps });",
        "    return;",
        "  }",
        "  if (timeSinceSuccess < 120000) return;",
        "",
        "  if (healthState.activeOps > 0) {",
        "    log('warn', 'health probe forced despite active ops', { activeOps: healthState.activeOps, timeSinceSuccessMs: timeSinceSuccess });",
        "  }",
        "",
        "  activeHealthProbeInFlight = true;",
        "  let testContext;",
        "  try {",
        f"    testContext = await browser.{new_context};",
        "    const page = await testContext.newPage();",
        "    await page.goto('about:blank', { timeout: 5000 });",
        "    await page.close().catch(() => {});",
        "    await testContext.close();",
        "    testContext = null;",
    ]
    if reset_nav_failures:
        lines.append("    healthState.consecutiveNavFailures = 0;")
    lines.extend(
        (
            "    healthState.lastSuccessfulNav = Date.now();",
            "  } catch (err) {",
            "    failuresTotal.labels('health_probe', 'internal').inc();",
            "    log('warn', 'health probe failed', { error: err.message, timeSinceSuccessMs: timeSinceSuccess });",
            "    if (sessions.size > 0 || getTotalTabCount() > 0 || healthState.activeOps > 0) {",
            "      restartBrowser('health probe failed').catch(() => {});",
            "    } else {",
            "      scheduleBrowserIdleShutdown();",
            "    }",
            "  } finally {",
            "    if (testContext) await testContext.close().catch(() => {});",
            "    activeHealthProbeInFlight = false;",
            "  }",
            "}, 60_000);",
        )
    )
    return "\n".join(lines)


LEGACY_HEALTH_STATE = """const healthState = {
  consecutiveNavFailures: 0,
  lastSuccessfulNav: Date.now(),
  isRecovering: false,
  activeOps: 0,
};"""

NATIVE_HEALTH_STATE = """const healthState = {
  isRecovering: false,
  activeOps: 0,
  lastSuccessfulNav: Date.now(),
};"""

LEGACY_BROWSER_LAUNCH = """      _lastBrowserPid = candidateBrowser.process?.()?.pid ?? null;
      browser = candidateBrowser; // publish AFTER PID is captured
      _lastBrowserRestartAt = Date.now();
      attachBrowserCleanup(browser, localVirtualDisplay);
      pluginEvents.emit('browser:launched', { browser, display: vdDisplay });"""

NATIVE_BROWSER_LAUNCH = """      _lastBrowserPid = candidateBrowser.process?.()?.pid ?? null;
      browser = candidateBrowser; // publish AFTER PID is captured
      _lastBrowserStopReason = null; // clear — browser is healthy
      _lastBrowserRestartAt = Date.now();
      attachBrowserCleanup(browser, localVirtualDisplay);
      pluginEvents.emit('browser:launched', { browser, display: vdDisplay });"""

NATIVE_USER_NAV_HEALTH = "const userNavHealth = new Map();"

TRANSFORMS = (
    Transform(
        "install-dir",
        "pkgman",
        (
            Variant(
                "legacy",
                'export const INSTALL_DIR = userCacheDir("camoufox");',
                'export const INSTALL_DIR = process.env.CAMOUFOX_INSTALL_DIR || userCacheDir("camoufox");',
            ),
            Variant(
                "native-1.14",
                """export const INSTALL_DIR = process.env.CAMOUFOX_INSTALL_DIR
    ? path.resolve(process.env.CAMOUFOX_INSTALL_DIR)
    : userCacheDir("camoufox");""",
                None,
            ),
        ),
    ),
    Transform(
        "camoufox-path",
        "pkgman",
        (
            Variant(
                "shared",
                """export function camoufoxPath(downloadIfMissing = true) {
    // Ensure the directory exists and is not empty""",
                """export function camoufoxPath(downloadIfMissing = true) {
    if (process.env.CAMOUFOX_INSTALL_DIR && fs.existsSync(INSTALL_DIR) && fs.readdirSync(INSTALL_DIR).length > 0) {
        return INSTALL_DIR;
    }
    // Ensure the directory exists and is not empty""",
            ),
        ),
    ),
    Transform(
        "default-addons",
        "server",
        (
            Variant(
                "legacy",
                """        virtual_display: vdDisplay,
      });""",
                """        virtual_display: vdDisplay,
        exclude_addons: ['UBO'],
      });""",
            ),
            Variant(
                "native-1.14",
                "        exclude_addons: CONFIG.disableDefaultAddons ? ['UBO'] : undefined,",
                None,
            ),
        ),
    ),
    Transform(
        "session-grace",
        "server",
        (
            Variant(
                "legacy",
                "    if (session.tabGroups.size === 0) {",
                "    if (session.tabGroups.size === 0 && now - session.lastAccess > 120000) {",
            ),
            Variant(
                "native-1.14",
                "    if (session.tabGroups.size === 0 && !hasActivePageLeases(session)) {",
                "    if (session.tabGroups.size === 0 && !hasActivePageLeases(session) && now - session.lastAccess > 120000) {",
            ),
        ),
    ),
    Transform(
        "request-timeout",
        "server",
        (
            Variant(
                "shared",
                """function requestTimeoutMs(baseMs = HANDLER_TIMEOUT_MS) {
  return proxyPool?.canRotateSessions ? Math.max(baseMs, 180000) : baseMs;
}""",
                """function requestTimeoutMs(baseMs = HANDLER_TIMEOUT_MS) {
  const localFloorMs = 120000;
  const proxyFloorMs = 180000;
  return proxyPool?.canRotateSessions ? Math.max(baseMs, proxyFloorMs) : Math.max(baseMs, localFloorMs);
}""",
            ),
        ),
    ),
    Transform(
        "idle-shutdown",
        "server",
        (
            Variant(
                "legacy",
                """function scheduleBrowserIdleShutdown() {
  clearBrowserIdleTimer();
  if (sessions.size === 0 && browser) {
    browserIdleTimer = setTimeout(async () => {
      if (sessions.size === 0 && browser) {
        log('info', 'browser idle shutdown (no sessions)');
        await closeBrowserFully('idle_shutdown');
      }
    }, BROWSER_IDLE_TIMEOUT_MS);
  }
}""",
                """function scheduleBrowserIdleShutdown() {
  if (browserIdleTimer) return;
  if (sessions.size === 0 && browser) {
    browserIdleTimer = setTimeout(async () => {
      browserIdleTimer = null;
      if (sessions.size === 0 && browser) {
        log('info', 'browser idle shutdown (no sessions)');
        await closeBrowserFully('idle_shutdown');
      }
    }, BROWSER_IDLE_TIMEOUT_MS);
  }
}""",
            ),
            Variant(
                "native",
                "  if (browserIdleTimer || sessions.size > 0 || !browser) return;",
                None,
            ),
        ),
    ),
    Transform(
        "health-state",
        "server",
        (
            Variant(
                "legacy",
                LEGACY_HEALTH_STATE,
                LEGACY_HEALTH_STATE + "\nlet activeHealthProbeInFlight = false;",
            ),
            Variant(
                "native-1.14",
                NATIVE_HEALTH_STATE,
                NATIVE_HEALTH_STATE + "\nlet activeHealthProbeInFlight = false;",
            ),
        ),
    ),
    Transform(
        "browser-launch",
        "server",
        (
            Variant(
                "legacy",
                LEGACY_BROWSER_LAUNCH,
                LEGACY_BROWSER_LAUNCH
                + "\n      healthState.consecutiveNavFailures = 0;"
                + "\n      healthState.lastSuccessfulNav = Date.now();",
            ),
            Variant(
                "native-1.14",
                NATIVE_BROWSER_LAUNCH,
                NATIVE_BROWSER_LAUNCH
                + "\n      userNavHealth.clear();"
                + "\n      healthState.lastSuccessfulNav = Date.now();",
            ),
        ),
    ),
    Transform(
        "active-health-probe",
        "server",
        (
            Variant(
                "legacy",
                probe_source("newContext()"),
                patched_probe("newContext()", reset_nav_failures=True),
            ),
            Variant(
                "native-1.14",
                probe_source("newContext({ viewport: null })"),
                patched_probe(
                    "newContext({ viewport: null })", reset_nav_failures=False
                ),
            ),
        ),
    ),
)


def preflight(sources: dict[str, str]) -> tuple[list[tuple[Transform, Variant]], list[str]]:
    plan: list[tuple[Transform, Variant]] = []
    errors: list[str] = []
    for transform in TRANSFORMS:
        text = sources[transform.target]
        counts = [(variant, text.count(variant.before)) for variant in transform.variants]
        matches = [variant for variant, count in counts if count == 1]
        if len(matches) != 1 or any(count not in (0, 1) for _, count in counts):
            summary = ", ".join(
                f"{variant.name}={count}" for variant, count in counts
            )
            errors.append(f"{transform.label} ({summary})")
            continue
        plan.append((transform, matches[0]))

    selected = {transform.label: variant.name for transform, variant in plan}
    health_labels = ("health-state", "browser-launch", "active-health-probe")
    if all(label in selected for label in health_labels):
        health_profile = tuple(selected[label] for label in health_labels)
        coherent_profiles = {
            ("legacy", "legacy", "legacy"),
            ("native-1.14", "native-1.14", "native-1.14"),
        }
        if health_profile not in coherent_profiles:
            summary = ", ".join(
                f"{label}={selected[label]}" for label in health_labels
            )
            errors.append(f"health-profile coherence ({summary})")

    if selected.get("browser-launch") == "native-1.14":
        count = sources["server"].count(NATIVE_USER_NAV_HEALTH)
        if count != 1:
            errors.append(f"user-nav-health declaration (native-1.14={count})")
    return plan, errors


def apply_plan(
    sources: dict[str, str], plan: list[tuple[Transform, Variant]]
) -> dict[str, str]:
    patched = dict(sources)
    for transform, variant in plan:
        text = patched[transform.target]
        count = text.count(variant.before)
        if count != 1:
            raise ValueError(
                f"{transform.label}: expected one {variant.name} anchor during apply, got {count}"
            )
        if variant.after is not None:
            patched[transform.target] = text.replace(
                variant.before, variant.after, 1
            )
    return patched


def write_atomically(paths: dict[str, Path], sources: dict[str, str]) -> None:
    staged: list[tuple[Path, Path]] = []
    try:
        for target, path in paths.items():
            fd, temporary_name = tempfile.mkstemp(
                prefix=f".{path.name}.", dir=path.parent
            )
            temporary = Path(temporary_name)
            with os.fdopen(fd, "w") as handle:
                handle.write(sources[target])
            os.chmod(temporary, path.stat().st_mode)
            staged.append((temporary, path))
        for temporary, path in staged:
            os.replace(temporary, path)
    finally:
        for temporary, _ in staged:
            temporary.unlink(missing_ok=True)


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(f"usage: {argv[0]} CAMOUFOX-PKGMAN CAMOFOX-SERVER", file=sys.stderr)
        return 2
    paths = {"pkgman": Path(argv[1]), "server": Path(argv[2])}
    try:
        sources = {target: path.read_text() for target, path in paths.items()}
    except OSError as error:
        print(f"camofox-browser compatibility preflight failed: {error}", file=sys.stderr)
        return 1

    plan, errors = preflight(sources)
    if errors:
        print("camofox-browser compatibility preflight failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1

    try:
        patched = apply_plan(sources, plan)
        write_atomically(paths, patched)
    except (OSError, ValueError) as error:
        print(f"camofox-browser compatibility apply failed: {error}", file=sys.stderr)
        return 1

    variants = ", ".join(
        f"{transform.label}={variant.name}" for transform, variant in plan
    )
    print(f"camofox-browser compatibility applied: {variants}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
