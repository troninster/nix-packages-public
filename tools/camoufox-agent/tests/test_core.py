import json
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import camoufox_agent as agent


class RedactionTests(unittest.TestCase):
    def test_redacts_emails_and_sensitive_query_params(self):
        text = (
            "Account user@example.com opened "
            "https://accounts.google.com/signin?continue=https://chatgpt.com&code=abc&state=xyz&safe=ok "
            "Authorization: Bearer sk-secret"
        )
        redacted = agent.redact(text)
        self.assertNotIn("user@example.com", redacted)
        self.assertNotIn("abc", redacted)
        self.assertNotIn("xyz", redacted)
        self.assertNotIn("sk-secret", redacted)
        self.assertIn("[REDACTED]", redacted)
        self.assertIn("safe=ok", redacted)

    def test_blocks_storage_and_cookie_dump_lines(self):
        text = "safe line\nCookie: session=abc\nlocalStorage token=def\nvisible button"
        redacted = agent.redact(text)
        self.assertIn("safe line", redacted)
        self.assertIn("visible button", redacted)
        self.assertNotIn("session=abc", redacted)
        self.assertNotIn("localStorage", redacted)


class SnapshotCompactionTests(unittest.TestCase):
    def test_compact_snapshot_keeps_interactive_refs_and_blockers(self):
        snapshot = "\n".join([
            "- text \"noise\"",
            "- heading \"Welcome\"",
            "- button \"Log in\" [e1]",
            "- textbox \"One-time code\" [e2]",
            "- link \"Download report\" [e3]",
            "- text \"Captcha required\"",
        ])
        compact = agent.compact_snapshot(snapshot, char_budget=500)
        self.assertIn('heading "Welcome"', compact)
        self.assertIn('button "Log in" [e1]', compact)
        self.assertIn('textbox "One-time code" [e2]', compact)
        self.assertIn('link "Download report" [e3]', compact)
        self.assertIn('Captcha required', compact)

    def test_compact_snapshot_respects_budget_and_preserves_refs(self):
        snapshot = "\n".join(f'- button "Item {i}" [e{i}]' for i in range(100))
        compact = agent.compact_snapshot(snapshot, char_budget=300)
        self.assertLessEqual(len(compact), 360)
        self.assertIn('[e0]', compact)
        self.assertIn('[truncated', compact)


class SnapshotRetryTests(unittest.TestCase):
    def test_snapshot_needs_retry_for_empty_snapshot(self):
        self.assertTrue(agent.snapshot_needs_retry({"snapshot": "", "url": "https://chatgpt.com/"}))
        self.assertTrue(agent.snapshot_needs_retry({"snapshot": "   ", "url": "https://chatgpt.com/"}))
        self.assertFalse(agent.snapshot_needs_retry({"snapshot": '- heading "Ready"', "url": "https://example.com/"}))


class ActionParsingTests(unittest.TestCase):
    def test_parse_action_rejects_unknown_action(self):
        with self.assertRaises(agent.ActionError):
            agent.parse_action('{"action":"steal_cookies"}')

    def test_parse_action_requires_ref_for_click(self):
        with self.assertRaises(agent.ActionError):
            agent.parse_action('{"action":"click"}')

    def test_parse_action_accepts_finish(self):
        action = agent.parse_action('{"action":"finish","summary":"done","files":["/tmp/a.md"]}')
        self.assertEqual(action["action"], "finish")
        self.assertEqual(action["files"], ["/tmp/a.md"])


class ArtifactTests(unittest.TestCase):
    def test_save_snapshot_writes_raw_and_compact_without_printing_raw(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifacts = agent.TaskArtifacts(pathlib.Path(tmp))
            raw, compact = artifacts.save_snapshot(1, "https://example.com/?code=abc", 'button "Open" [e1]\n' * 100, 120)
            self.assertTrue(raw.exists())
            self.assertTrue(compact.exists())
            compact_text = compact.read_text()
            self.assertIn("[REDACTED]", compact_text)
            self.assertLess(len(compact_text), 400)

    def test_resolve_job_dir_accepts_unique_partial_job_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            job = pathlib.Path(tmp) / "20260505_010203-open-example"
            job.mkdir()
            self.assertEqual(agent.resolve_job_dir("010203", tmp), job)


if __name__ == "__main__":
    unittest.main()
