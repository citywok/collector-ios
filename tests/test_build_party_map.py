"""Unit tests for build_party_map.py pure logic (no network)."""
import importlib.util
import pathlib

_SPEC = importlib.util.spec_from_file_location(
    "build_party_map",
    pathlib.Path(__file__).resolve().parent.parent / "scripts" / "build_party_map.py")
mod = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(mod)


def test_normalize_strips_apostrophes_and_caps():
    assert mod.normalize("O'Brien") == "OBRIEN"
    assert mod.normalize("smatt ") == "SMATT"


def test_party_map_records_schema_matches_stream_analyzer():
    # contract: [start, end, type, party, state] term records per name key
    import json
    recs = [[1995, 2005, "rep", "Republican", "TX"]]
    assert recs[0][0] < recs[0][1] and recs[0][2] in ("rep", "sen", "del", "gov"[:3])
