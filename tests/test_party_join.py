"""Party-join tests built from REAL Eugleo tokens (2026-09-15/16 probes):
chamber ∈ {H, S, E, None}; first_name may be 'Unknown'; last names may carry
middle initials in first ('GERALD R.'); states: full names / Unknown / OCR garbage.

Fixture discipline: the REALISH index is swapped in per-test and restored
afterward, so the import-time invariant test always sees the PRODUCTION index
(a module-level clobber previously masked that exact defect)."""
import importlib.util
import pathlib

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "hf_stream_lexicon",
    pathlib.Path(__file__).resolve().parent.parent / "scripts" / "hf_stream_lexicon.py")
mod = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(mod)

_PROD_INDEX = mod.PARTY_LAST  # production index, captured pre-any-test

REALISH = {
    "FORD|GERALD": [[1965, 1973, "rep", "Republican", "MI"]],
    "SMITH|JASON": [[2013, 2027, "rep", "Republican", "MO"]],
    "SWITCHER|P": [[1995, 2005, "rep", "Republican", "TX"], [2005, 2015, "rep", "Democrat", "TX"]],
    "ONEPARTY|ST": [[1995, 2025, "rep", "Republican", "IA"]],
    "WARREN|FRANCIS": [[1905, 1915, "sen", "Republican", "WY"]],
}


@pytest.fixture()
def realish():
    mod.PARTY_LAST = mod.build_last_index(REALISH)
    yield
    mod.PARTY_LAST = _PROD_INDEX


def test_party_last_index_built_at_import_time():
    """Import-time invariant, run WITHOUT the realish fixture: the production
    index must be a real, populated dict (v4 crashed because a stray
    None-clobber survived patch ordering)."""
    assert isinstance(_PROD_INDEX, dict) and len(_PROD_INDEX) > 1000


def test_unknown_first_name_resolves_via_last_only_index(realish):
    # real shard-10 token: first_name='Unknown'
    assert mod.party_of("FORD", "Unknown", "H", "Unknown", 1968) == "Republican"


def test_middle_initial_first_names_resolve(realish):
    # real token: 'GERALD R.'
    assert mod.party_of("FORD", "GERALD R.", "H", "Unknown", 1968) == "Republican"


def test_extensions_chamber_e_joins_by_unanimity(realish):
    # real shard-4 token: ch='E' (Extensions)
    assert mod.party_of("SMITH", "JASON", "E", "Missouri", 2019) == "Republican"


def test_contested_party_never_guessed(realish):
    assert mod.party_of("SWITCHER", "P", "E", "Unknown", 2010) is None


def test_senate_requires_type_match_even_unanimous(realish):
    assert mod.party_of("SMITH", "JASON", "S", "Unknown", 2019) is None
    assert mod.party_of("WARREN", "Unknown", "S", "Wyoming", 1908) == "Republican"


def test_garbled_state_and_missing_name(realish):
    assert mod.party_of("ONEPARTY", "ST", "H", "Vorniont", 2019) == "Republican"
    assert mod.party_of("NOBODY", "X", "H", "Ohio", 2015) is None
    assert mod.party_of(None, "X", "H", "Ohio", 2015) is None


def test_fallback_year_blind_but_unanimous_only(realish):
    # upstream date drift: unanimous name resolves outside mapped term window
    assert mod.party_of("SMITH", "JASON", "H", "Missouri", 2009) == "Republican"
