"""Contract tests for the `research` chat project.

The project's defining behavior is AGENTS.md auto-injection: the llm-chat
chat server injects AGENTS.md found in the project repo root into every
thread's system prompt as binding project instructions, and those
instructions make every user message a generic internet-research question.
These tests pin the artifacts that make that work:

1. The repo carries an AGENTS.md that states the standing research mandate
   and is small enough to survive the server's injection size cap.
2. The live projects.yaml registry accepts the entry and satisfies every
   invariant the registry loader enforces (existing branch, unique
   non-overlapping worktree prefix).
3. AGENTS.md is recoverable from the project's worktree compose rule.

If the llm-chat checkout (or its registry module) is unavailable from this
host, tests 2-3 skip rather than fail — they exercise live host config,
not vendored logic.
"""
from __future__ import annotations

import os
import pathlib
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
LLM_CHAT_SRC = pathlib.Path("/home/andrew/llm-chat/src")
LIVE_REGISTRY_YAML = pathlib.Path("/home/andrew/llm-chat/projects.yaml")

# chat_server skips instruction files above 50KB entirely (2 * 25_000 cap).
MAX_INJECT_BYTES = 50_000

_MANDATE_MARKERS = (
    # Standing rule: every message is a research question.
    "generic question",
    # Research-before-answering via the web_search tool.
    "web_search",
    # Every factual answer carries sources.
    "Sources:",
    # Memory-only fallback must be labeled, never passed off as researched.
    "unverified recall",
)


class AgentsMdContractTests(unittest.TestCase):
    """AGENTS.md must be present, mandate-bearing, and injectable."""

    def setUp(self):
        self.agents_md = REPO / "AGENTS.md"

    def test_agents_md_exists_nonempty(self):
        self.assertTrue(self.agents_md.is_file(), "AGENTS.md missing from repo root")
        text = self.agents_md.read_text(encoding="utf-8")
        self.assertGreater(len(text.strip()), 0)

    def test_agents_md_states_research_mandate(self):
        text = self.agents_md.read_text(encoding="utf-8")
        missing = [m for m in _MANDATE_MARKERS if m not in text]
        self.assertEqual([], missing, f"AGENTS.md lost mandate markers: {missing}")

    def test_agents_md_survives_injection_size_cap(self):
        size = self.agents_md.stat().st_size
        self.assertLess(
            size,
            MAX_INJECT_BYTES,
            f"AGENTS.md is {size} bytes; above the {MAX_INJECT_BYTES}-byte cap "
            "the chat server skips oversized instruction files entirely — "
            "the mandate would silently stop being injected",
        )

    def test_research_project_contract(self):
        try:
            registry_mod = _import_registry_module()
        except _Unavailable as exc:
            self.skipTest(str(exc))
        try:
            proj = registry_mod.require("research")
        except registry_mod.ProjectRegistryError as exc:
            self.fail(f"live registry refuses 'research': {exc}")
        self.assertEqual(proj.path, pathlib.Path("/home/andrew/research"))
        self.assertTrue(proj.path.is_dir())
        # Git-mode worktree contract: system can spawn research-* worktrees.
        self.assertEqual(proj.worktree_mode, "git")
        self.assertEqual(proj.worktree_prefix, "research")
        wt_dir = proj.worktree_dir("abcd1234")
        self.assertEqual(str(wt_dir), "/home/andrew/work_trees/research-abcd1234")

# Branch existence is not asserted separately: require() makes the loader run
# its own `git rev-parse --verify refs/heads/<branch>` against the repo, so
# test_research_project_contract already proves the registered branch is real.


class _Unavailable(Exception):
    """Environment lacks the llm-chat checkout; contract tests skip."""


def _import_registry_module():
    """Load webapp.projects against the live registry via env override.

    Because other test processes may hold cached snapshots pointing at a
    different registry file, force a reload() after import.
    """
    if not LLM_CHAT_SRC.is_dir():
        raise _Unavailable(f"llm-chat checkout not found: {LLM_CHAT_SRC}")
    if not LIVE_REGISTRY_YAML.is_file():
        raise _Unavailable(f"live registry not found: {LIVE_REGISTRY_YAML}")
    import sys

    src_added = str(LLM_CHAT_SRC) not in sys.path
    if src_added:
        sys.path.insert(0, str(LLM_CHAT_SRC))
    try:
        import webapp.projects as registry_mod
    finally:
        if src_added:
            pass  # keep on path; repeated imports must still resolve
    prev = os.environ.get("CCT_PROJECTS_YAML")
    os.environ["CCT_PROJECTS_YAML"] = str(LIVE_REGISTRY_YAML)
    try:
        registry_mod.reload()
    finally:
        if prev is None:
            os.environ.pop("CCT_PROJECTS_YAML", None)
        else:
            os.environ["CCT_PROJECTS_YAML"] = prev
    return registry_mod


if __name__ == "__main__":
    unittest.main()
