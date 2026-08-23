# camoufox-agent

Small CLI runner for this machine's local FedotFox/Camoufox browser backend.

It is intentionally simpler than a Playwright MCP stack: Hermes/Telegram can run one foreground command, while the runner talks to the existing local REST backend at `http://127.0.0.1:9377`, saves raw snapshots to artifact files, and prints only compact redacted summaries.

## Commands

```bash
camoufox-agent health

camoufox-agent run --profile gmail-login --session chatgpt-login \
  "Open ChatGPT and report whether the logged-in UI is visible"

camoufox-agent status <job-id-or-artifact-dir>
camoufox-agent logs <job-id-or-artifact-dir> --limit 20
camoufox-agent screenshot <job-id-or-artifact-dir>
camoufox-agent stop <job-id-or-artifact-dir>
```

Useful safe smoke test:

```bash
camoufox-agent run --no-llm --profile gmail-login --session smoke-test \
  "Open example.com and report the visible heading"
```

## Artifacts

Each run writes files under:

```text
/tmp/camoufox-agent/<timestamp>-<task-slug>/
```

Important files:

- `task.json` — redacted task metadata
- `steps.jsonl` — redacted step/action log
- `snapshots/*.raw.txt.gz` — redacted raw ARIA/accessibility snapshots
- `snapshots/*.compact.txt` — compact model-visible snapshots
- `final_summary.json` — final result

## Safety

The runner redacts emails, token-like query parameters, bearer/API tokens, and drops cookie/localStorage/sessionStorage lines from human-visible output.

It stops with `blocked` if it detects password, MFA, passkey, captcha, or security confirmation screens. The user must complete those manually in the browser; do not paste passwords or one-time codes into chat.

## LLM mode

By default, if an API key is present, the runner can call an OpenAI-compatible chat completions API for `observe -> decide -> act`.

Environment variables:

```text
CAMOUFOX_AGENT_API_KEY
CAMOUFOX_AGENT_BASE_URL     # default: https://api.openai.com/v1
CAMOUFOX_AGENT_MODEL        # default: gpt-4.1-mini
```

Use `--no-llm` for deterministic smoke tests and safe built-in heuristics.
