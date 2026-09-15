"""Unit tests for the CREC attribution core (scripts/crec_stageB.py).

Covers the three bugs fixed on 2026-09-14: 'United States' false match in
the civility lexicon, ambiguous-surname resolution safety, and presiding-
officer guard regex. Loads the script by path (scripts/ has no package).
"""
import importlib.util
import pathlib
import re

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "crec_stageB",
    pathlib.Path(__file__).resolve().parent.parent / "scripts" / "crec_stageB.py")
mod = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(mod)


def test_civil_lexicon_does_not_match_united_states():
    rx = mod.LEX["civil"]
    assert not rx.search("the United States of America has long stood")
    assert rx.search("a bipartisan compromise came together")


def test_empathy_and_faith_lexicons_match_expected_terms():
    assert mod.LEX["empathy"].search("compassion for the families")
    assert not mod.LEX["empathy"].search("the weather today is pleasant for October")
    assert mod.LEX["faith"].search("to God we commend")
    assert mod.LEX["deserv"].search("able-bodied adults deserve work")


class _Member(dict):
    def __init__(self, name, state, chamber):
        super().__init__(name=name, state=state, chamber=chamber)


def _idx():
    idx = {"ROGERS": [
        _Member("Rogers, Mike D.", "Alabama", "Senate"),
        _Member("Rogers, Mike J.", "Michigan", "House"),
        _Member("Rogers, Harold", "Kentucky", "House"),
    ], "SMITH": [_Member("Smith, Jason", "Texas", "House")]}
    return idx


def test_resolve_state_disambiguates_same_surname():
    idx = _idx()
    assert mod.resolve(idx, "ROGERS", "Alabama", "House")["name"] == "Rogers, Mike D."
    assert mod.resolve(idx, "ROGERS", "Michigan", "House")["name"] == "Rogers, Mike J."


def test_resolve_uses_chamber_when_it_is_unique_and_skips_true_ambiguity():
    idx = _idx()
    assert mod.resolve(idx, "ROGERS", None, "Senate")["name"] == "Rogers, Mike D."
    # two House Rogers (MI + KY) with no state: unresolvable -> None, no guessing
    assert mod.resolve(idx, "ROGERS", None, "House") is None
    assert mod.resolve(idx, "ROGERS", "Vermont", "House") is None
    assert mod.resolve(idx, "SMITH", None, "Senate")["name"] == "Smith, Jason"


def test_speaker_and_hon_regexes_capture_record_conventions():
    sm = mod.SPK.match("Mr. SMITH of Texas:")
    assert sm and sm.group(1) == "SMITH" and sm.group(2) == "Texas"
    hon = mod.HON.match("HON. JASON SMITH, A REPRESENTATIVE IN THE CONGRESS OF THE UNITED STATES FROM TEXAS:")
    assert hon and hon.group(1).split()[-1] == "SMITH" and hon.group(2) == "TEXAS"
    assert mod.GUARD.match("Mr. Speaker:") or mod.GUARD.match("Ms. Chairman:")


def test_guard_blocks_presiding_officer_attribution():
    line = "Mr. Speaker, the gentleman yields back the balance of his time."
    assert mod.GUARD.match(line.strip()) and not mod.SPK.match(line.strip())


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
