"""Unit tests for sample_frames.py (no network — pure structure checks).

The network phases of this script are verified by the integrated
probe/background runs; these tests pin the module surface that the
Phase 2 fetchers will import against.
"""
import importlib.util
import pathlib

_SPEC = importlib.util.spec_from_file_location(
    "sample_frames",
    pathlib.Path(__file__).resolve().parent.parent / "scripts" / "sample_frames.py")
mod = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(mod)


def test_module_imports_without_network_side_effects():
    assert callable(mod.archive_search)
    assert callable(mod.fox_layer)
    assert callable(mod.youtube_layer)
    assert callable(mod.rally_layer)


def test_show_list_covers_prime_time_plan():
    for show in ("Hannity", "The Five", "Fox and Friends", "The Ingraham Angle",
                 "Gutfeld", "Special Report", "America Reports", "Fox News Night"):
        assert show in mod.SHOWS, f"missing prime-time show: {show}"


def test_frames_output_dir_configured():
    assert mod.OUT == "/tmp/frames"
