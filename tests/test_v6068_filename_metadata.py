import ast
import os
from pathlib import Path
import re


def _load_guess_metadata_from_filename():
    root = Path(__file__).resolve().parents[1]
    tree = ast.parse((root / "app.py").read_text(encoding="utf-8"))
    function = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "guess_metadata_from_filename"
    )
    module = ast.Module(body=[function], type_ignores=[])
    ast.fix_missing_locations(module)
    namespace = {"os": os, "re": re}
    exec(compile(module, str(root / "app.py"), "exec"), namespace)
    return namespace["guess_metadata_from_filename"]


def test_filename_metadata_is_conservative_and_hyphen_safe() -> None:
    guess = _load_guess_metadata_from_filename()

    assert guess("90-es_intro.mp3") == {"artist": "", "title": "90-es intro"}
    assert guess("Artist - Title.mp3") == {"artist": "Artist", "title": "Title"}
    assert guess("01 - Artist - Title.mp3") == {"artist": "Artist", "title": "Title"}
    assert guess("01. Artist - Title.mp3") == {"artist": "Artist", "title": "Title"}
    assert guess("01_Artist - Title.mp3") == {"artist": "Artist", "title": "Title"}
    assert guess("Blink-182 - All The Small Things.mp3") == {
        "artist": "Blink-182",
        "title": "All The Small Things",
    }
    assert guess("AC-DC.mp3") == {"artist": "", "title": "AC-DC"}
    assert guess("Artist-Title.mp3") == {"artist": "", "title": "Artist-Title"}


def test_number_hyphen_word_is_not_treated_as_track_number() -> None:
    guess = _load_guess_metadata_from_filename()

    assert guess("90-es.mp3") == {"artist": "", "title": "90-es"}
    assert guess("80-as_evek.mp3") == {"artist": "", "title": "80-as evek"}
    assert guess("100-percent.mp3") == {"artist": "", "title": "100-percent"}
