"""Unit tests for speech_fetch.py pure logic (no network)."""
import importlib.util
import datetime
import pathlib

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "speech_fetch",
    pathlib.Path(__file__).resolve().parent.parent / "scripts" / "speech_fetch.py")
mod = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(mod)

_SPEC2 = importlib.util.spec_from_file_location(
    "crec_test_harness",
    pathlib.Path(__file__).resolve().parent.parent / "scripts" / "crec_stageB.py")
crec = importlib.util.module_from_spec(_SPEC2)
_SPEC2.loader.exec_module(crec)


def test_lexicons_match_the_crec_core_pattern_for_pattern():
    # Contract: cross-layer gradient uses IDENTICAL patterns to the floor run.
    # (Same strings, not same module instance — module identity is per-process.)
    assert list(mod.LEX) == list(crec.LEX)
    for k in crec.LEX:
        assert mod.LEX[k].pattern == crec.LEX[k].pattern, f"pattern drift: {k}"


def test_srt_to_text_strips_indices_and_timestamps():
    srt = "WEBVTT\n\n1\n00:00:01,000 --> 00:00:03,000\nHELLO THERE EVERYONE\n\n2\n00:00:03,500 --> 00:00:05,000\nWELCOME BACK\n"
    out = mod.srt_to_text(srt)
    assert "HELLO THERE EVERYONE WELCOME BACK" == out.replace("  ", " ")
    assert "-->" not in out and "WEBVTT" not in out


def test_dense_windows_are_valid_ordered_dates():
    assert len(mod.DENSE_WINDOWS) == 6
    for a, b in mod.DENSE_WINDOWS:
        d0, d1 = datetime.date.fromisoformat(a), datetime.date.fromisoformat(b)
        assert d0 <= d1


def test_host_table_covers_every_planned_show():
    spec3 = importlib.util.spec_from_file_location(
        "sample_frames",
        pathlib.Path(__file__).resolve().parent.parent / "scripts" / "sample_frames.py")
    sf = importlib.util.module_from_spec(spec3)
    spec3.loader.exec_module(sf)
    for show in sf.SHOWS:
        assert show in mod.SHOW_HOSTS, f"SHOW_HOSTS missing planned show: {show}"


def test_stats_counts_words_segments_and_thresholds_quotes():
    s = mod.Stats()
    s.add("Fox shows", "Sean Hannity", "we must come together in unity", date="2025-03-04")
    s.add("Fox shows", "Sean Hannity", "short line", date="2025-03-05")
    v = s.by_layer["Fox shows"]["Sean Hannity"]
    assert v["words"] == 8  # 6 + 2
    assert v["segs"] == 2
    assert v["lex"]["civil"] == 1  # one search() per segment, not per match
    # quote preserved only for the >=12-word segment: 7 < 12 wait nuance
    # 7-word line → no quote; adjust: add a long civil line
    s.add("Fox shows", "Sean Hannity",
          "we must come together in unity as one nation and heal our divisions today",
          date="2025-03-06")
    assert any(t == "civil" for _, t, _ in v["q"])


def test_save_caption_cache_first_no_network_for_valid_file(tmp_path, monkeypatch):
    calls = []
    def fake_run(cmd, **kw):
        calls.append(cmd)
        class R: stdout = "200"
        return R()
    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    FR = tmp_path
    ident = "FOXNEWSW_20260301_020000_TestShow"
    good = FR / (ident + ".cc5.txt")
    good.write_text("X" * 6000, encoding="utf-8")
    monkeypatch.setattr(mod, "FRAMES", str(FR))
    out = mod.save_caption(ident, ".cc5.txt")
    assert out == "X" * 6000
    assert calls == []  # cache hit: ZERO network calls issued


def test_save_caption_error_body_never_clobbers_valid_cache(tmp_path, monkeypatch):
    counter = {"n": 0}
    def fake_run(cmd, **kw):
        counter["n"] += 1
        class R: stdout = "500"
        return R()
    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    FR = tmp_path
    ident = "FOXNEWSW_20260302_020000_TestShow2"
    good = FR / (ident + ".cc5.txt")
    good.write_text("Y" * 7000, encoding="utf-8")
    monkeypatch.setattr(mod, "FRAMES", str(FR))
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)
    out = mod.save_caption(ident, ".cc5.txt")
    assert out == "Y" * 7000  # cache survived a failing server
    assert good.read_text(encoding="utf-8") == "Y" * 7000  # unharmed on disk


def test_stats_quote_excludes_short_segments():
    s = mod.Stats()
    s.add("Fox shows", "Host", "evil enemy traitors", )  # 3 words, matches enemy
    v = s.by_layer["Fox shows"]["Host"]
    assert v["lex"]["enemy"] == 1
    assert v["q"] == []  # <12 words → no verbatim quote stored


def test_dump_creates_layer_json(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "OUT", str(tmp_path))
    s = mod.Stats()
    s.add("YouTube layer", "Ted Cruz", "one two three four five six seven eight nine ten eleven twelve thirteen", )
    s.dump()
    data = __import__("json").load(open(tmp_path / "layers_stats.json"))
    assert "YouTube layer" in data and data["YouTube layer"]["Ted Cruz"]["segs"] == 1
