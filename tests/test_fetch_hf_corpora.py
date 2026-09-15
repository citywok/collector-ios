"""Unit tests for fetch_hf_corpora.py (structure only, no network)."""
import importlib.util
import pathlib

_SPEC = importlib.util.spec_from_file_location(
    "fetch_hf_corpora",
    pathlib.Path(__file__).resolve().parent.parent / "scripts" / "fetch_hf_corpora.py")
mod = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(mod)


def test_module_surface():
    assert callable(mod.main)
    assert mod.OUT == "/tmp/hf_corpora"


def test_target_repos_declared():
    import inspect
    src = inspect.getsource(mod)
    assert "Eugleo/us-congressional-speeches" in src
    assert "macleginn/stanford_congress_record_longer_speeches" in src
    assert "yeeder/congressional-record-parquet" in src
