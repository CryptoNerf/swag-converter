"""The desktop layer, without opening a window.

The window itself is the platform's; what is ours is the bridge between the
page and the tracer, and that is what these cover.
"""

from __future__ import annotations

import base64
from pathlib import Path
import time

import numpy as np
import pytest
from PIL import Image

from swag_converter.gui.bridge import DEFAULTS, Bridge


def _image(tmp_path: Path, name: str = "in.png", size: int = 64) -> Path:
    array = np.zeros((size, size, 4), dtype=np.uint8)
    array[:, :, 3] = 255
    array[:, :, 0] = 220
    array[size // 4 : size // 2, size // 4 : size // 2] = (20, 40, 200, 255)
    path = tmp_path / name
    Image.fromarray(array, "RGBA").save(path)
    return path


@pytest.fixture
def bridge():
    made = Bridge()
    yield made
    made.shutdown()


def test_describe_tells_the_page_what_it_may_offer(bridge: Bridge) -> None:
    described = bridge.describe()
    assert described["presets"][0] == "auto"
    assert "balanced" in described["qualities"]
    assert ".png" in described["suffixes"]
    assert described["settings"] == DEFAULTS


def test_settings_reject_what_the_tracer_would_refuse(bridge: Bridge) -> None:
    """The page is the only caller, but it is still the outside."""
    bridge.update_settings({"preset": "nonsense", "quality": "nonsense",
                            "background": "nonsense", "max_edge": "banana"})
    assert bridge.settings["preset"] == "auto"
    assert bridge.settings["quality"] == "balanced"
    assert bridge.settings["background"] == "auto"
    assert bridge.settings["max_edge"] == 1024

    bridge.update_settings({"max_edge": 99999})
    assert bridge.settings["max_edge"] == 8192
    bridge.update_settings({"max_edge": -5})
    assert bridge.settings["max_edge"] == 0
    bridge.update_settings({"launch": "/bin/sh"})
    assert "launch" not in bridge.settings


def test_a_drop_has_to_be_an_image(bridge: Bridge) -> None:
    """The page hands over bytes and a name; neither can be taken on trust."""
    payload = base64.b64encode(b"not an image").decode()
    assert bridge.accept_drop("notes.txt", payload) == []        # wrong suffix
    assert bridge.accept_drop("broken.png", "!!!not base64!!!") == []
    assert bridge.accept_drop("empty.png", "") == []
    assert bridge.poll()["jobs"] == []


def test_a_drop_larger_than_the_cap_is_refused(bridge: Bridge, monkeypatch) -> None:
    """Dropped bytes travel through the page, so the size has a ceiling."""
    monkeypatch.setattr("swag_converter.gui.bridge.MAX_DROP_BYTES", 64)
    payload = base64.b64encode(b"x" * 256).decode()
    assert bridge.accept_drop("big.png", payload) == []
    assert bridge.poll()["jobs"] == []


def test_a_dropped_image_is_kept_somewhere_we_own(tmp_path: Path, bridge: Bridge) -> None:
    source = _image(tmp_path)
    payload = base64.b64encode(source.read_bytes()).decode()
    added = bridge.accept_drop("dropped.png", payload)
    assert len(added) == 1
    kept = Path(added[0]["source"])
    assert kept.exists() and kept.parent != tmp_path
    assert added[0]["thumbnail"].startswith("data:image/png;base64,")


def test_output_goes_beside_the_original_by_default(tmp_path: Path, bridge: Bridge) -> None:
    source = _image(tmp_path)
    added = bridge._accept([source])
    assert Path(added[0]["destination"]) == source.with_suffix(".svg")

    elsewhere = tmp_path / "svg"
    elsewhere.mkdir()
    bridge.update_settings({"output_dir": str(elsewhere)})
    again = bridge._accept([source])
    assert Path(again[0]["destination"]).parent == elsewhere


def test_unsupported_files_are_not_queued(tmp_path: Path, bridge: Bridge) -> None:
    text = tmp_path / "notes.txt"
    text.write_text("hello")
    assert bridge._accept([text, tmp_path / "missing.png"]) == []
    assert bridge.poll()["jobs"] == []


@pytest.mark.slow
def test_a_conversion_runs_through_the_bridge(tmp_path: Path, bridge: Bridge) -> None:
    """The whole path the window uses: queue, convert, report, preview."""
    out = tmp_path / "out"
    out.mkdir()
    bridge.update_settings({"quality": "fast", "measure": False, "output_dir": str(out)})
    bridge._accept([_image(tmp_path, "one.png"), _image(tmp_path, "two.png")])

    snapshot = bridge.start()
    assert snapshot["busy"]

    deadline = time.monotonic() + 180
    while bridge.poll()["busy"] and time.monotonic() < deadline:
        time.sleep(0.2)

    jobs = bridge.poll()["jobs"]
    assert [job["status"] for job in jobs] == ["done", "done"], jobs
    for job in jobs:
        assert job["result"]["regions"] >= 1
        assert Path(job["destination"]).exists()

    preview = bridge.preview(jobs[0]["id"])
    assert preview["svg"].startswith("<svg")
    assert preview["source"].startswith("data:image/png;base64,")

    assert bridge.clear_finished()["jobs"] == []


def test_preview_of_something_unconverted_is_empty(tmp_path: Path, bridge: Bridge) -> None:
    added = bridge._accept([_image(tmp_path)])
    assert bridge.preview(added[0]["id"]) == {}
    assert bridge.preview("nosuchjob") == {}
