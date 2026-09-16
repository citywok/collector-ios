"""Boundary guard test: years outside the analysis window must be skipped,
not KeyError-crash (stream v6 crashed on a 1901 Extensions row)."""
import importlib.util
import pathlib
import subprocess
import sys


def test_window_guard_present():
    src = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "hf_stream_lexicon.py"
    text = src.read_text()
    assert "y > END_YEAR" in text and "y < START_YEAR" in text, \
        "year-window guard missing from main() row loop"


def test_script_compiles():
    src = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "hf_stream_lexicon.py"
    compile(src.read_text(), "hf_stream_lexicon.py", "exec")
