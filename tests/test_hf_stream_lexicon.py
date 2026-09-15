"""Unit tests for hf_stream_lexicon.py (pure logic, no network/stream)."""
import importlib.util
import pathlib
import types

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "hf_stream_lexicon",
    pathlib.Path(__file__).resolve().parent.parent / "scripts" / "hf_stream_lexicon.py")
mod = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(mod)
# import the CREC core for the lexicon-pattern contract
_SPEC2 = importlib.util.spec_from_file_location(
    "crec_stageB_har", pathlib.Path(__file__).resolve().parent.parent / "scripts" / "crec_stageB.py")
crec = importlib.util.module_from_spec(_SPEC2)
_SPEC2.loader.exec_module(crec)


def test_lexicon_patterns_identical_to_crec_core():
    for k in crec.LEX:
        assert mod.LEX[k].pattern == crec.LEX[k].pattern, f"drift {k}"


def test_period_boundaries():
    assert mod.period_of(1995) == "pre-trump-1995-2015"
    assert mod.period_of(2015) == "pre-trump-1995-2015"
    assert mod.period_of(2016) == "trump1-2016-2020"
    assert mod.period_of(2020) == "trump1-2016-2020"
    assert mod.period_of(2021) == "biden-2021-2024"
    assert mod.period_of(2024) == "biden-2021-2024"
    assert mod.period_of(2025) == "trump2-2025-2026"


def test_year_of_parses_datetimes_and_ints():
    assert mod.year_of(2017) == 2017
    assert mod.year_of("2019-04-01T00:00:00Z") == 2019
    assert mod.year_of("speech delivered 2003") == 2003
    assert mod.year_of("104th congress") is None or mod.year_of("104") == 104


def test_aggregate_row_filters_party_and_window():
    import collections
    stats = collections.defaultdict(lambda: {"words": 0, "segs": 0, "lex": collections.Counter()})
    row = types.SimpleNamespace()
    speech = "we come together with compassion for those who suffer"
    mod.aggregate_row({"text": speech}, 2018, "Republican", "House", stats, text=speech)
    assert stats[2018]["segs"] == 1 and stats[2018]["lex"]["civil"] >= 1
    mod.aggregate_row({"text": speech}, 2018, "Democrat", "House", stats, text=speech)
    assert stats[2018]["segs"] == 1  # unchanged
    mod.aggregate_row({"text": speech}, 1994, "Republican", "House", stats, text=speech)
    assert 1994 not in stats  # outside window


def test_detect_columns_fuzzy_match():
    cols = {"speech_date": None, "chamber_name": None, "speech_text": None}
    y, t = mod.detect_columns(cols)
    assert y == "speech_date" and t == "speech_text"
    with pytest.raises(ValueError):
        mod.detect_columns({"foo": None})
