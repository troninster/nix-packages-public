#!/usr/bin/env python3
"""Small CLI runner for the local FedotFox/Camoufox browser backend.

The first implementation intentionally uses the existing REST service on
127.0.0.1:9377. It keeps large browser snapshots in artifact files and exposes
only compact, redacted summaries to callers such as Hermes/Telegram.
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import pathlib
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

DEFAULT_BASE_URL = os.environ.get("CAMOFOX_URL") or os.environ.get("FEDOTFOX_URL") or "http://127.0.0.1:9377"
DEFAULT_PROFILE = "gmail-login"
DEFAULT_SESSION = "camoufox-agent"
DEFAULT_BUDGET = 12000
DEFAULT_MAX_STEPS = 30
DEFAULT_MAX_MINUTES = 25

SENSITIVE_QUERY_KEYS = {
    "code",
    "state",
    "tl",
    "ifkv",
    "dsh",
    "authuser",
    "login_hint",
    "continue",
    "redirect_uri",
    "oauth_token",
    "access_token",
    "refresh_token",
    "id_token",
    "token",
    "key",
}

DROP_LINE_PATTERNS = [
    re.compile(r"\b(cookie|set-cookie)\s*:", re.I),
    re.compile(r"\b(localStorage|sessionStorage)\b", re.I),
    re.compile(r"\b(document\.cookie)\b", re.I),
]

KEEP_LINE_PATTERNS = [
    re.compile(p, re.I)
    for p in [
        r"^-?\s*(?:\|\s*)?-?\s*(?:heading|button|link|textbox|searchbox|menuitem|tab|checkbox|radio|combobox|dialog|alert|navigation|listitem|option)\b",
        r"\[(?:e\d+)\]",
        r"\b(log in|sign in|continue|download|export|share|copy|open|next|previous|submit|send|search)\b",
        r"\b(password|one-time|mfa|2fa|authenticator|verify|verification|passkey|captcha|security|blocked|required)\b",
    ]
]

BLOCKER_PATTERNS = [
    re.compile(p, re.I)
    for p in [
        r"password",
        r"one-time code",
        r"verification code",
        r"authenticator",
        r"\bmfa\b",
        r"\b2fa\b",
        r"passkey",
        r"captcha",
        r"verify your identity",
        r"security confirmation",
    ]
]

ALLOWED_ACTIONS = {"click", "type", "navigate", "wait", "snapshot", "extract", "finish", "ask_user"}


class AgentError(RuntimeError):
    pass


class ActionError(ValueError):
    pass


def redact(text: Any) -> str:
    """Redact secrets/PII from human-visible text."""
    value = str(text)
    kept: List[str] = []
    for line in value.splitlines():
        if any(p.search(line) for p in DROP_LINE_PATTERNS):
            kept.append("[REDACTED sensitive browser storage line]")
            continue
        kept.append(line)
    value = "\n".join(kept)

    value = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[REDACTED]", value)
    value = re.sub(r"Bearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [REDACTED]", value, flags=re.I)
    value = re.sub(r"\b(?:sk|ghp|gho|github_pat)_[A-Za-z0-9_\-]{8,}\b", "[REDACTED]", value)
    value = re.sub(
        r"(?i)\b(api[_-]?key|token|secret|password|passwd)\s*[:=]\s*[^\s\"']+",
        lambda m: f"{m.group(1)}=[REDACTED]",
        value,
    )

    def redact_url(match: re.Match[str]) -> str:
        raw = match.group(0)
        try:
            parsed = urllib.parse.urlsplit(raw)
            query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
            safe_query = []
            for k, v in query:
                if k.lower() in SENSITIVE_QUERY_KEYS:
                    safe_query.append((k, "[REDACTED]"))
                else:
                    safe_query.append((k, v))
            return urllib.parse.urlunsplit(
                (parsed.scheme, parsed.netloc, parsed.path, urllib.parse.urlencode(safe_query), parsed.fragment)
            )
        except Exception:
            return "[REDACTED_URL]"

    value = re.sub(r"https?://[^\s\"'<>]+", redact_url, value)
    # Also redact bare query fragments that often appear in auth logs.
    value = re.sub(
        r"(?i)([?&](?:code|state|TL|ifkv|dsh|authuser|login_hint|continue|redirect_uri|access_token|refresh_token|id_token)=)[^\s\"'&]+",
        r"\1[REDACTED]",
        value,
    )
    return value


def compact_snapshot(snapshot: str, char_budget: int = DEFAULT_BUDGET) -> str:
    """Return a model-visible compact ARIA/accessibility snapshot."""
    snapshot = redact(snapshot)
    selected: List[str] = []
    for raw_line in snapshot.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            continue
        if any(p.search(line) for p in KEEP_LINE_PATTERNS):
            selected.append(line[:500])

    if not selected:
        selected = [line[:500] for line in snapshot.splitlines()[:80] if line.strip()]

    out: List[str] = []
    used = 0
    truncated = False
    for line in selected:
        add = len(line) + 1
        if out and used + add > char_budget:
            truncated = True
            break
        out.append(line)
        used += add
    if truncated:
        marker = f"[truncated compact snapshot: kept {len(out)} of {len(selected)} relevant lines]"
        # Ensure marker fits reasonably even for small budgets.
        if used + len(marker) + 1 <= char_budget + 80:
            out.append(marker)
    return "\n".join(out)


def detect_blocker(snapshot: str) -> Optional[str]:
    redacted = redact(snapshot)
    for pattern in BLOCKER_PATTERNS:
        if pattern.search(redacted):
            return f"Sensitive or manual verification screen detected: {pattern.pattern}"
    return None


def parse_action(raw: str) -> Dict[str, Any]:
    """Parse and validate one LLM action."""
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ActionError(f"Action is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ActionError("Action must be a JSON object")
    action = data.get("action")
    if action not in ALLOWED_ACTIONS:
        raise ActionError(f"Unknown action: {action!r}")
    if action == "click" and not data.get("ref"):
        raise ActionError("click action requires ref")
    if action == "type" and (not data.get("ref") or "text" not in data):
        raise ActionError("type action requires ref and text")
    if action == "navigate" and not data.get("url"):
        raise ActionError("navigate action requires url")
    if action == "wait":
        data["seconds"] = max(1, min(int(data.get("seconds", 3)), 30))
    if action == "finish":
        data.setdefault("summary", "Finished")
        data.setdefault("files", [])
    if action == "ask_user":
        data.setdefault("reason", "Manual user action is required in the browser")
    return data


def slugify(value: str, max_len: int = 48) -> str:
    value = redact(value).lower()
    value = re.sub(r"[^a-z0-9а-яё]+", "-", value, flags=re.I).strip("-")
    return (value or "task")[:max_len].strip("-") or "task"


@dataclass
class TaskArtifacts:
    root: pathlib.Path

    def __post_init__(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "snapshots").mkdir(exist_ok=True)
        (self.root / "outputs").mkdir(exist_ok=True)

    def write_jsonl(self, name: str, row: Dict[str, Any]) -> None:
        path = self.root / name
        safe = json.loads(json.dumps(row, ensure_ascii=False, default=str))
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(safe, ensure_ascii=False) + "\n")

    def write_json(self, name: str, obj: Dict[str, Any]) -> pathlib.Path:
        path = self.root / name
        path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def save_snapshot(self, step: int, url: str, snapshot: str, char_budget: int = DEFAULT_BUDGET) -> Tuple[pathlib.Path, pathlib.Path]:
        redacted_url = redact(url)
        raw_path = self.root / "snapshots" / f"{step:04d}.raw.txt.gz"
        compact_path = self.root / "snapshots" / f"{step:04d}.compact.txt"
        raw_body = f"url: {redacted_url}\n\n{redact(snapshot)}"
        with gzip.open(raw_path, "wt", encoding="utf-8", errors="replace") as fh:
            fh.write(raw_body)
        compact = f"url: {redacted_url}\n\n{compact_snapshot(snapshot, char_budget)}"
        compact_path.write_text(compact, encoding="utf-8", errors="replace")
        return raw_path, compact_path


class FedotFoxClient:
    def __init__(self, base_url: str = DEFAULT_BASE_URL, timeout: int = 90):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _open(self, method: str, path: str, payload: Optional[Dict[str, Any]] = None, timeout: Optional[int] = None, accept: str = "application/json") -> bytes:
        data = None
        headers = {"Accept": accept}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(self.base_url + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            err = exc.read().decode("utf-8", errors="replace")
            raise AgentError(f"HTTP {exc.code} {method} {path}: {redact(err)}") from exc
        except urllib.error.URLError as exc:
            raise AgentError(f"Cannot reach FedotFox at {self.base_url}: {exc}") from exc

    def _request(self, method: str, path: str, payload: Optional[Dict[str, Any]] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        body = self._open(method, path, payload, timeout, accept="application/json").decode("utf-8", errors="replace")
        if not body.strip():
            return {}
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return {"raw": body}

    def health(self) -> Dict[str, Any]:
        return self._request("GET", "/health", timeout=15)

    def open_tab(self, user_id: str, session_key: str, url: str) -> Dict[str, Any]:
        return self._request("POST", "/tabs", {"userId": user_id, "sessionKey": session_key, "url": url}, timeout=120)

    def snapshot(self, user_id: str, tab_id: str, offset: Optional[int] = None) -> Dict[str, Any]:
        query = urllib.parse.urlencode({k: v for k, v in {"userId": user_id, "offset": offset}.items() if v is not None})
        return self._request("GET", f"/tabs/{urllib.parse.quote(tab_id)}/snapshot?{query}", timeout=120)

    def click(self, user_id: str, tab_id: str, ref: str) -> Dict[str, Any]:
        return self._request("POST", f"/tabs/{urllib.parse.quote(tab_id)}/click", {"userId": user_id, "ref": ref}, timeout=90)

    def type_text(self, user_id: str, tab_id: str, ref: str, text: str, press_enter: bool = False) -> Dict[str, Any]:
        return self._request(
            "POST",
            f"/tabs/{urllib.parse.quote(tab_id)}/type",
            {"userId": user_id, "ref": ref, "text": text, "pressEnter": press_enter},
            timeout=90,
        )

    def navigate(self, user_id: str, tab_id: str, url: str) -> Dict[str, Any]:
        return self._request("POST", f"/tabs/{urllib.parse.quote(tab_id)}/navigate", {"userId": user_id, "url": url}, timeout=120)

    def stats(self, user_id: str, tab_id: str) -> Dict[str, Any]:
        query = urllib.parse.urlencode({"userId": user_id})
        return self._request("GET", f"/tabs/{urllib.parse.quote(tab_id)}/stats?{query}", timeout=30)

    def screenshot(self, user_id: str, tab_id: str, full_page: bool = False) -> bytes:
        query = urllib.parse.urlencode({"userId": user_id, "fullPage": str(full_page).lower()})
        return self._open("GET", f"/tabs/{urllib.parse.quote(tab_id)}/screenshot?{query}", timeout=120, accept="image/png")

    def close_tab(self, user_id: str, tab_id: str) -> Dict[str, Any]:
        query = urllib.parse.urlencode({"userId": user_id})
        return self._request("DELETE", f"/tabs/{urllib.parse.quote(tab_id)}?{query}", timeout=60)


def choose_initial_url(task: str) -> str:
    lower = task.lower()
    if "chatgpt" in lower or "чатgpt" in lower or "чатджип" in lower:
        return "https://chatgpt.com/"
    if "gmail" in lower or "почт" in lower:
        return "https://mail.google.com/"
    match = re.search(r"https?://[^\s\"'<>]+", task)
    if match:
        return match.group(0)
    if "example" in lower:
        return "https://example.com/"
    return "https://www.google.com/"


def heuristic_action(task: str, snapshot: str, url: str, step: int) -> Dict[str, Any]:
    """Safe fallback when no LLM credentials are configured."""
    lower_task = task.lower()
    lower_snapshot = snapshot.lower()
    blocker = detect_blocker(snapshot)
    if blocker:
        return {"action": "ask_user", "reason": blocker}
    if "example" in lower_task:
        title_match = re.search(r'heading\s+"([^"]+)"', snapshot, re.I)
        summary = f"Opened example page; heading={title_match.group(1)!r}" if title_match else "Opened example page."
        return {"action": "finish", "summary": summary, "files": []}
    if "chatgpt" in lower_task or "чатgpt" in lower_task:
        logged_in = any(s in lower_snapshot for s in ["message chatgpt", "prompt", "new chat", "chatgpt", "прикрепить"])
        if logged_in:
            return {"action": "finish", "summary": "ChatGPT page opened; logged-in UI appears visible. No content was exported.", "files": []}
        login_ref = find_ref(snapshot, r"log in|sign in|войти")
        if login_ref and step <= 2:
            return {"action": "click", "ref": login_ref, "reason": "Open login flow"}
        return {"action": "finish", "summary": "ChatGPT page opened, but logged-in state was not confidently detected.", "files": []}
    return {"action": "finish", "summary": "Page opened and compact snapshot captured. No LLM credentials configured for autonomous actions.", "files": []}


def find_ref(snapshot: str, label_regex: str) -> Optional[str]:
    pattern = re.compile(rf'(?:button|link|textbox|menuitem|tab)\s+"[^"]*(?:{label_regex})[^"]*"\s+\[(e\d+)\]', re.I)
    match = pattern.search(snapshot)
    return match.group(1) if match else None


def call_llm(task: str, url: str, compact: str, history: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    api_key = os.environ.get("CAMOUFOX_AGENT_API_KEY") or os.environ.get("OPENAI_API_KEY")
    base_url = os.environ.get("CAMOUFOX_AGENT_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    model = os.environ.get("CAMOUFOX_AGENT_MODEL", "gpt-4.1-mini")
    if not api_key:
        return None
    system = (
        "You are a browser automation controller. Return exactly one JSON object. "
        "Allowed actions: click(ref), type(ref,text,press_enter), navigate(url), wait(seconds), "
        "snapshot(), extract(), finish(summary,files), ask_user(reason). "
        "Stop with ask_user for passwords, MFA, passkeys, captchas, security confirmations. "
        "Never request or expose cookies, localStorage, sessionStorage, tokens, passwords, or MFA codes."
    )
    user = {
        "task": task,
        "url": redact(url),
        "snapshot": compact,
        "recent_actions": history[-8:],
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
        ],
        "temperature": 0,
    }
    req = urllib.request.Request(
        base_url + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception as exc:
        raise AgentError(f"LLM call failed: {redact(exc)}") from exc
    content = data["choices"][0]["message"]["content"]
    return parse_action(content)


def snapshot_needs_retry(data: Dict[str, Any]) -> bool:
    snapshot = str(data.get("snapshot") or "")
    stripped = snapshot.strip()
    if not stripped:
        return True
    if any(p.search(stripped) for p in KEEP_LINE_PATTERNS):
        return False
    if len(stripped) < 20 and str(data.get("url") or "").startswith("http"):
        return True
    return False


def resolve_job_dir(job: str, base_dir: str = "/tmp/camoufox-agent") -> pathlib.Path:
    path = pathlib.Path(job).expanduser()
    if path.exists():
        return path
    candidate = pathlib.Path(base_dir) / job
    if candidate.exists():
        return candidate
    matches = sorted(pathlib.Path(base_dir).glob(f"*{job}*")) if pathlib.Path(base_dir).exists() else []
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise AgentError(f"Job not found: {job}")
    raise AgentError(f"Job id is ambiguous: {job}; matches: {', '.join(p.name for p in matches[:10])}")


def load_job_metadata(job_dir: pathlib.Path) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    task_path = job_dir / "task.json"
    final_path = job_dir / "final_summary.json"
    task = json.loads(task_path.read_text(encoding="utf-8")) if task_path.exists() else {}
    final = json.loads(final_path.read_text(encoding="utf-8")) if final_path.exists() else {}
    return task, final


def run_task(args: argparse.Namespace) -> int:
    started = time.time()
    task = args.task
    slug = slugify(task)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    root = pathlib.Path(args.artifact_dir) if args.artifact_dir else pathlib.Path("/tmp/camoufox-agent") / f"{ts}-{slug}"
    artifacts = TaskArtifacts(root)
    client = FedotFoxClient(args.base_url, timeout=args.timeout)
    health = client.health()
    if args.verbose:
        print("health", redact(json.dumps(health, ensure_ascii=False))[:1000])

    url = args.url or choose_initial_url(task)
    tab = client.open_tab(args.profile, args.session, url)
    tab_id = tab.get("tabId") or tab.get("id")
    if not tab_id:
        raise AgentError(f"FedotFox did not return tabId: {redact(tab)}")

    history: List[Dict[str, Any]] = []
    artifacts.write_json("task.json", {"task": redact(task), "profile": args.profile, "session": args.session, "url": redact(url), "tabId": tab_id})

    final: Optional[Dict[str, Any]] = None
    for step in range(1, args.max_steps + 1):
        if time.time() - started > args.max_minutes * 60:
            final = {"status": "timeout", "summary": f"Stopped after {args.max_minutes} minutes", "files": []}
            break
        snap_data = client.snapshot(args.profile, tab_id)
        for _ in range(max(0, args.snapshot_retries)):
            if not snapshot_needs_retry(snap_data):
                break
            time.sleep(args.settle_seconds)
            snap_data = client.snapshot(args.profile, tab_id)
        current_url = snap_data.get("url", "")
        snapshot = snap_data.get("snapshot", "") or ""
        raw_path, compact_path = artifacts.save_snapshot(step, current_url, snapshot, args.snapshot_char_budget)
        compact = compact_path.read_text(encoding="utf-8", errors="replace")
        blocker = detect_blocker(snapshot)
        if blocker:
            action = {"action": "ask_user", "reason": blocker}
        else:
            action = None if args.no_llm else call_llm(task, current_url, compact, history)
            if action is None:
                action = heuristic_action(task, snapshot, current_url, step)
        action = parse_action(json.dumps(action, ensure_ascii=False))
        safe_action = json.loads(json.dumps(action, ensure_ascii=False))
        if "text" in safe_action:
            safe_action["text"] = redact(safe_action["text"])
        artifacts.write_jsonl(
            "steps.jsonl",
            {"step": step, "url": redact(current_url), "snapshot_raw": str(raw_path), "snapshot_compact": str(compact_path), "action": safe_action},
        )
        history.append({"step": step, "url": redact(current_url), "action": safe_action})

        kind = action["action"]
        if kind == "finish":
            final = {"status": "finished", "summary": redact(action.get("summary", "Finished")), "files": action.get("files", [])}
            break
        if kind == "ask_user":
            final = {"status": "blocked", "summary": redact(action.get("reason", "Manual user action required")), "files": []}
            break
        if kind == "click":
            client.click(args.profile, tab_id, action["ref"])
            time.sleep(args.settle_seconds)
        elif kind == "type":
            client.type_text(args.profile, tab_id, action["ref"], action.get("text", ""), bool(action.get("press_enter")))
            time.sleep(args.settle_seconds)
        elif kind == "navigate":
            client.navigate(args.profile, tab_id, action["url"])
            time.sleep(max(args.settle_seconds, 3))
        elif kind == "wait":
            time.sleep(action.get("seconds", 3))
        elif kind in {"snapshot", "extract"}:
            time.sleep(args.settle_seconds)
        else:
            raise ActionError(f"Unhandled action: {kind}")
    else:
        final = {"status": "max_steps", "summary": f"Stopped after {args.max_steps} steps", "files": []}

    final = final or {"status": "unknown", "summary": "Stopped without final result", "files": []}
    final.update({"artifact_dir": str(root), "job_id": root.name, "steps": len(history), "tabId": tab_id, "profile": args.profile, "session": args.session})
    final_path = artifacts.write_json("final_summary.json", final)
    print(json.dumps({"ok": final["status"] == "finished", "job_id": root.name, "final": final, "final_summary": str(final_path)}, ensure_ascii=False, indent=2))
    return 0 if final["status"] in {"finished", "blocked"} else 2


def health_cmd(args: argparse.Namespace) -> int:
    client = FedotFoxClient(args.base_url, timeout=args.timeout)
    print(json.dumps(client.health(), ensure_ascii=False, indent=2))
    return 0


def status_cmd(args: argparse.Namespace) -> int:
    job_dir = resolve_job_dir(args.job)
    task, final = load_job_metadata(job_dir)
    out: Dict[str, Any] = {"ok": True, "job_id": job_dir.name, "artifact_dir": str(job_dir), "task": task, "final": final}
    profile = final.get("profile") or task.get("profile") or args.profile
    tab_id = final.get("tabId") or task.get("tabId")
    if tab_id and profile:
        try:
            out["tab_stats"] = FedotFoxClient(args.base_url, timeout=args.timeout).stats(profile, tab_id)
        except AgentError as exc:
            out["tab_stats_error"] = redact(str(exc))
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


def logs_cmd(args: argparse.Namespace) -> int:
    job_dir = resolve_job_dir(args.job)
    path = job_dir / "steps.jsonl"
    rows: List[Dict[str, Any]] = []
    if path.exists():
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        for line in lines[-args.limit:]:
            if line.strip():
                rows.append(json.loads(line))
    print(json.dumps({"ok": True, "job_id": job_dir.name, "steps": rows}, ensure_ascii=False, indent=2))
    return 0


def screenshot_cmd(args: argparse.Namespace) -> int:
    job_dir = resolve_job_dir(args.job)
    task, final = load_job_metadata(job_dir)
    profile = final.get("profile") or task.get("profile") or args.profile
    tab_id = args.tab_id or final.get("tabId") or task.get("tabId")
    if not tab_id:
        raise AgentError("No tabId found; pass --tab-id")
    out_dir = job_dir / "outputs"
    out_dir.mkdir(parents=True, exist_ok=True)
    png = FedotFoxClient(args.base_url, timeout=args.timeout).screenshot(profile, tab_id, full_page=args.full_page)
    out_path = out_dir / f"screenshot_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.png"
    out_path.write_bytes(png)
    print(json.dumps({"ok": True, "job_id": job_dir.name, "screenshot": str(out_path)}, ensure_ascii=False, indent=2))
    return 0


def stop_cmd(args: argparse.Namespace) -> int:
    job_dir = resolve_job_dir(args.job)
    task, final = load_job_metadata(job_dir)
    profile = final.get("profile") or task.get("profile") or args.profile
    tab_id = args.tab_id or final.get("tabId") or task.get("tabId")
    if not tab_id:
        raise AgentError("No tabId found; pass --tab-id")
    result = FedotFoxClient(args.base_url, timeout=args.timeout).close_tab(profile, tab_id)
    marker = job_dir / "stopped.json"
    marker.write_text(json.dumps({"stopped_at": datetime.now(timezone.utc).isoformat(), "tabId": tab_id, "profile": profile, "result": result}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "job_id": job_dir.name, "closed": result, "stopped": str(marker)}, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="camoufox-agent", description="Compact CLI runner for the local FedotFox/Camoufox backend")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help=f"FedotFox base URL (default: {DEFAULT_BASE_URL})")
    parser.add_argument("--timeout", type=int, default=90, help="HTTP timeout seconds")
    sub = parser.add_subparsers(dest="command", required=True)

    health = sub.add_parser("health", help="Check FedotFox health")
    health.set_defaults(func=health_cmd)

    status = sub.add_parser("status", help="Show a job summary and live tab stats when available")
    status.add_argument("job", help="Job id or artifact directory")
    status.add_argument("--profile", default=DEFAULT_PROFILE, help="Fallback FedotFox userId/profile")
    status.set_defaults(func=status_cmd)

    logs = sub.add_parser("logs", help="Show compact step log for a job")
    logs.add_argument("job", help="Job id or artifact directory")
    logs.add_argument("--limit", type=int, default=20)
    logs.set_defaults(func=logs_cmd)

    screenshot = sub.add_parser("screenshot", help="Save a PNG screenshot for a job's tab")
    screenshot.add_argument("job", help="Job id or artifact directory")
    screenshot.add_argument("--profile", default=DEFAULT_PROFILE, help="Fallback FedotFox userId/profile")
    screenshot.add_argument("--tab-id", help="Override tab id")
    screenshot.add_argument("--full-page", action="store_true")
    screenshot.set_defaults(func=screenshot_cmd)

    stop = sub.add_parser("stop", help="Close a job's browser tab")
    stop.add_argument("job", help="Job id or artifact directory")
    stop.add_argument("--profile", default=DEFAULT_PROFILE, help="Fallback FedotFox userId/profile")
    stop.add_argument("--tab-id", help="Override tab id")
    stop.set_defaults(func=stop_cmd)

    run = sub.add_parser("run", help="Run one browser task")
    run.add_argument("task", help="Natural-language browser task")
    run.add_argument("--profile", default=DEFAULT_PROFILE, help="FedotFox userId/profile")
    run.add_argument("--session", default=DEFAULT_SESSION, help="FedotFox sessionKey")
    run.add_argument("--url", help="Initial URL override")
    run.add_argument("--artifact-dir", help="Artifact directory; defaults to /tmp/camoufox-agent/<timestamp>-<slug>")
    run.add_argument("--max-steps", type=int, default=DEFAULT_MAX_STEPS)
    run.add_argument("--max-minutes", type=int, default=DEFAULT_MAX_MINUTES)
    run.add_argument("--snapshot-char-budget", type=int, default=DEFAULT_BUDGET)
    run.add_argument("--settle-seconds", type=float, default=2.0)
    run.add_argument("--snapshot-retries", type=int, default=5, help="Retry empty/too-small snapshots before deciding")
    run.add_argument("--no-llm", action="store_true", help="Use safe built-in heuristics only")
    run.add_argument("--verbose", action="store_true")
    run.set_defaults(func=run_task)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except (AgentError, ActionError) as exc:
        print(json.dumps({"ok": False, "error": redact(str(exc))}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
