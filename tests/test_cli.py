"""Command line surface."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from swag_converter.cli import _collect, _destination, _parser, main


def _image(tmp_path: Path, name: str, size: int = 48) -> Path:
    array = np.zeros((size, size, 4), dtype=np.uint8)
    array[6:-6, 6:-6, 3] = 255
    array[6:-6, 6:-6, :3] = (40, 120, 200)
    path = tmp_path / name
    Image.fromarray(array, "RGBA").save(path)
    return path


def test_directories_expand_to_supported_images(tmp_path: Path) -> None:
    _image(tmp_path, "a.png")
    _image(tmp_path, "b.png")
    (tmp_path / "notes.txt").write_text("ignore me")
    found = _collect([tmp_path])
    assert {path.name for path in found} == {"a.png", "b.png"}


def test_duplicate_inputs_are_converted_once(tmp_path: Path) -> None:
    first = _image(tmp_path, "a.png")
    assert len(_collect([first, first, tmp_path])) == 1


def test_output_path_defaults_next_to_the_input(tmp_path: Path) -> None:
    source = tmp_path / "logo.png"
    assert _destination(source, None) == tmp_path / "logo.svg"
    assert _destination(source, tmp_path / "out") == tmp_path / "out" / "logo.svg"


def test_missing_input_exits_with_an_error(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    assert main([str(tmp_path / "nothing.png")]) == 2


def test_json_mode_emits_machine_readable_output(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    source = _image(tmp_path, "logo.png")
    assert main([str(source), "--out", str(tmp_path / "out"), "--json", "--no-measure"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert len(payload["converted"]) == 1
    entry = payload["converted"][0]
    assert entry["output"].endswith("logo.svg")
    assert entry["nodes"] > 0 and entry["regions"] >= 1
    assert Path(entry["output"]).exists()


def test_a_broken_file_does_not_stop_the_batch(tmp_path: Path) -> None:
    _image(tmp_path, "good.png")
    (tmp_path / "bad.png").write_bytes(b"not an image at all")
    out = tmp_path / "out"
    # The batch carries on past the bad file...
    status = main([str(tmp_path), "--out", str(out), "--json", "--no-measure"])
    assert (out / "good.svg").exists()
    # ...but still reports failure, so `swag icons/ && deploy` stops.
    assert status == 1


def test_a_clean_batch_exits_zero(tmp_path: Path) -> None:
    _image(tmp_path, "good.png")
    out = tmp_path / "out"
    assert main([str(tmp_path), "--out", str(out), "--json", "--no-measure"]) == 0


def test_bare_invocation_shows_help(capsys: pytest.CaptureFixture) -> None:
    """Typing just `swag` should teach the command, not reject it."""
    assert main([]) == 2
    assert "usage: swag" in capsys.readouterr().out


def test_preset_and_quality_choices_are_validated() -> None:
    with pytest.raises(SystemExit):
        _parser().parse_args(["x.png", "--preset", "nonsense"])
    parsed = _parser().parse_args(["x.png", "-p", "photo", "-q", "max"])
    assert parsed.preset == "photo" and parsed.quality == "max"
