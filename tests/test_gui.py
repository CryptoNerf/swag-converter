"""The desktop layer, without opening a window.

The window itself is the platform's; what is ours is the bridge between the
page and the tracer.  Several of these exist because the thing they cover
was broken and nothing noticed — a file dialog that raised before it opened,
a duplicate that raced itself for one output path.
"""

from __future__ import annotations

import base64
from pathlib import Path
import time

import numpy as np
import pytest
from PIL import Image

from swag_converter.gui.bridge import DEFAULTS, Bridge, image_filter
from swag_converter.image import SUPPORTED_SUFFIXES


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


# -- the dialogs, which is where "Add images" silently died -----------------

def test_the_open_filter_is_one_pywebview_accepts() -> None:
    """It validates the filter before opening anything.

    Space-separated patterns raise ValueError, the bridge call rejects, and
    the button does nothing whatsoever — no dialog, no error, no clue.
    """
    from webview.util import parse_file_type

    parse_file_type(image_filter())  # raises if malformed
    assert ";" in image_filter()
    assert " " not in image_filter().split("(", 1)[1]


def test_the_save_filter_is_one_pywebview_accepts() -> None:
    from webview.util import parse_file_type

    parse_file_type("SVG (*.svg)")


def test_the_dialogs_are_asked_for_by_their_current_names() -> None:
    """OPEN_DIALOG and friends are deprecated and will be removed."""
    import webview

    source = Path(Bridge.__module__.replace(".", "/"))
    text = (Path(__file__).parent.parent / "src" / source.with_suffix(".py")).read_text()
    assert "webview.OPEN_DIALOG" not in text
    assert "webview.FOLDER_DIALOG" not in text
    assert "webview.SAVE_DIALOG" not in text
    assert webview.FileDialog.OPEN is not None


# -- what the page is told --------------------------------------------------

def test_describe_tells_the_page_what_it_may_offer(bridge: Bridge) -> None:
    described = bridge.describe()
    assert described["presets"][0] == "auto"
    assert "balanced" in described["qualities"]
    assert ".png" in described["suffixes"]
    assert described["settings"] == DEFAULTS


def test_every_offered_format_can_actually_be_opened(tmp_path: Path) -> None:
    """Offering a format Pillow cannot read wastes the user's time.

    The dialog accepts the file, the thumbnail comes back blank and the
    conversion fails on something the tool said it supported.
    """
    Image.init()
    readable = {extension.lower() for extension in Image.EXTENSION}
    assert SUPPORTED_SUFFIXES <= readable


def test_phone_photographs_are_supported() -> None:
    """HEIC is what phones produce, and this is a tool for photographs."""
    pytest.importorskip("pillow_heif")
    assert ".heic" in SUPPORTED_SUFFIXES


# -- settings ---------------------------------------------------------------

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


def test_every_preset_and_quality_on_offer_is_accepted(bridge: Bridge) -> None:
    """The page builds its menus from describe(); both ends must agree."""
    described = bridge.describe()
    for preset in described["presets"]:
        bridge.update_settings({"preset": preset})
        assert bridge.settings["preset"] == preset
    for quality in described["qualities"]:
        bridge.update_settings({"quality": quality})
        assert bridge.settings["quality"] == quality


# -- getting images in ------------------------------------------------------

def test_a_drop_has_to_be_an_image(bridge: Bridge) -> None:
    """The page hands over bytes and a name; neither can be taken on trust."""
    payload = base64.b64encode(b"not an image").decode()
    assert bridge.accept_drop("notes.txt", payload)["added"] == []
    assert bridge.accept_drop("broken.png", "!!!not base64!!!")["added"] == []
    assert bridge.accept_drop("empty.png", "")["added"] == []
    assert bridge.poll()["jobs"] == []


def test_a_refused_drop_says_why(bridge: Bridge) -> None:
    """Silence is indistinguishable from a broken button."""
    outcome = bridge.accept_drop("notes.txt", base64.b64encode(b"x").decode())
    assert outcome["skipped"] and outcome["skipped"][0]["reason"]
    assert outcome["skipped"][0]["name"] == "notes.txt"


def test_a_drop_larger_than_the_cap_is_refused(bridge: Bridge, monkeypatch) -> None:
    """Dropped bytes travel through the page, so the size has a ceiling."""
    monkeypatch.setattr("swag_converter.gui.bridge.MAX_DROP_BYTES", 64)
    payload = base64.b64encode(b"x" * 256).decode()
    assert bridge.accept_drop("big.png", payload)["added"] == []
    assert bridge.poll()["jobs"] == []


def test_a_dropped_image_is_kept_somewhere_we_own(tmp_path: Path, bridge: Bridge) -> None:
    source = _image(tmp_path)
    payload = base64.b64encode(source.read_bytes()).decode()
    added = bridge.accept_drop("dropped.png", payload)["added"]
    assert len(added) == 1
    kept = Path(added[0]["source"])
    assert kept.exists() and kept.parent != tmp_path
    assert added[0]["thumbnail"].startswith("data:image/png;base64,")


def test_a_drop_that_cannot_be_opened_leaves_nothing_behind(bridge: Bridge) -> None:
    """A rejected drop must not litter the scratch directory."""
    payload = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"rubbish" * 40).decode()
    assert bridge.accept_drop("fake.png", payload)["added"] == []
    assert list(bridge._scratch.glob("*")) == []


def test_the_same_file_is_not_queued_twice(tmp_path: Path, bridge: Bridge) -> None:
    """Two jobs for one file race each other for a single output path."""
    source = _image(tmp_path)
    assert len(bridge._accept([source])["added"]) == 1
    again = bridge._accept([source])
    assert again["added"] == []
    assert "already" in again["skipped"][0]["reason"]
    assert len(bridge.poll()["jobs"]) == 1


def test_a_file_that_is_not_really_an_image_is_refused_on_the_spot(
    tmp_path: Path, bridge: Bridge
) -> None:
    """Better to say so while the name is on screen than to fail mid-batch."""
    broken = tmp_path / "corrupt.png"
    broken.write_bytes(b"\x89PNG\r\n\x1a\n" + b"garbage" * 40)
    outcome = bridge._accept([broken])
    assert outcome["added"] == []
    assert "open" in outcome["skipped"][0]["reason"]
    assert bridge.poll()["jobs"] == []


def test_unsupported_and_missing_files_are_not_queued(tmp_path: Path, bridge: Bridge) -> None:
    text = tmp_path / "notes.txt"
    text.write_text("hello")
    outcome = bridge._accept([text, tmp_path / "missing.png"])
    assert outcome["added"] == []
    assert len(outcome["skipped"]) == 2
    assert bridge.poll()["jobs"] == []


# -- where results go -------------------------------------------------------

def test_output_goes_beside_the_original_by_default(tmp_path: Path, bridge: Bridge) -> None:
    source = _image(tmp_path)
    added = bridge._accept([source])["added"]
    assert Path(added[0]["destination"]) == source.with_suffix(".svg")


def test_a_chosen_folder_is_used_instead(tmp_path: Path, bridge: Bridge) -> None:
    source = _image(tmp_path)
    elsewhere = tmp_path / "svg"
    elsewhere.mkdir()
    bridge.update_settings({"output_dir": str(elsewhere)})
    added = bridge._accept([source])["added"]
    assert Path(added[0]["destination"]).parent == elsewhere


def test_choosing_a_folder_then_going_back_to_beside(tmp_path: Path, bridge: Bridge) -> None:
    bridge.update_settings({"output_dir": str(tmp_path)})
    assert bridge.use_source_folder() == ""
    assert bridge.settings["output_dir"] == ""


# -- running ----------------------------------------------------------------

def test_starting_with_nothing_queued_is_harmless(bridge: Bridge) -> None:
    snapshot = bridge.start()
    assert snapshot["busy"] is False and snapshot["jobs"] == []


def test_cancelling_when_idle_is_harmless(bridge: Bridge) -> None:
    assert bridge.cancel()["busy"] is False


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
        assert job["elapsed"] is not None
        assert Path(job["destination"]).exists()

    preview = bridge.preview(jobs[0]["id"])
    assert preview["svg"].startswith("<svg")
    assert preview["source"].startswith("data:image/png;base64,")
    assert preview["result"]["destination"] == jobs[0]["destination"]

    assert bridge.clear_finished()["jobs"] == []


@pytest.mark.slow
def test_a_run_can_be_stopped_and_started_again(tmp_path: Path, bridge: Bridge) -> None:
    """Cancelling has to actually stop it, and leave the queue usable."""
    out = tmp_path / "out"
    out.mkdir()
    bridge.update_settings({"quality": "max", "measure": False, "output_dir": str(out)})
    bridge._accept([_image(tmp_path, "big.png", size=512)])
    bridge.start()

    deadline = time.monotonic() + 30
    while bridge.poll()["jobs"][0]["status"] != "running" and time.monotonic() < deadline:
        time.sleep(0.1)

    snapshot = bridge.cancel()
    assert snapshot["busy"] is False
    assert snapshot["jobs"][0]["status"] == "cancelled"
    assert list(out.iterdir()) == []

    bridge.update_settings({"quality": "fast"})
    bridge.start()
    deadline = time.monotonic() + 180
    while bridge.poll()["busy"] and time.monotonic() < deadline:
        time.sleep(0.2)
    assert bridge.poll()["jobs"][0]["status"] == "done"


@pytest.mark.slow
def test_a_running_job_cannot_be_removed(tmp_path: Path, bridge: Bridge) -> None:
    """Removing one mid-flight would leave a process writing to a dead job."""
    bridge.update_settings({"quality": "max", "measure": False})
    added = bridge._accept([_image(tmp_path, "busy.png", size=512)])["added"]
    bridge.start()
    deadline = time.monotonic() + 30
    while bridge.poll()["jobs"][0]["status"] != "running" and time.monotonic() < deadline:
        time.sleep(0.1)
    bridge.remove(added[0]["id"])
    assert len(bridge.poll()["jobs"]) == 1
    bridge.cancel()


def test_preview_of_something_unconverted_is_empty(tmp_path: Path, bridge: Bridge) -> None:
    added = bridge._accept([_image(tmp_path)])["added"]
    assert bridge.preview(added[0]["id"]) == {}
    assert bridge.preview("nosuchjob") == {}


def test_reveal_and_open_refuse_paths_that_are_not_there(bridge: Bridge) -> None:
    assert bridge.reveal("/no/such/file.svg") is False
    assert bridge.open_path("/no/such/file.svg") is False


def test_shutdown_takes_the_scratch_directory_with_it(tmp_path: Path) -> None:
    made = Bridge()
    source = _image(tmp_path)
    made.accept_drop("dropped.png", base64.b64encode(source.read_bytes()).decode())
    scratch = made._scratch
    assert scratch.exists()
    made.shutdown()
    assert not scratch.exists()


def test_the_progress_bar_knows_every_stage_the_tracer_reports() -> None:
    """A renamed stage would leave the bar at zero and say nothing.

    The window reads stage labels straight out of the converter's progress
    callback, so the two lists have to agree exactly.
    """
    import re

    from swag_converter.gui.jobs import STAGES

    source = Path(__file__).parent.parent / "src/swag_converter/convert.py"
    emitted = set(re.findall(r'step\("([^"]+)"\)', source.read_text()))
    assert emitted == set(STAGES)


def test_progress_only_moves_forward_through_the_stages() -> None:
    from swag_converter.gui.jobs import STAGES, Job

    seen = [
        Job(identifier="x", source=Path("a.png"), destination=Path("a.svg"), stage=stage).state()["progress"]
        for stage in STAGES
    ]
    assert seen == sorted(seen)
    assert seen[-1] == pytest.approx(1.0)


def test_an_unknown_stage_does_not_break_the_bar() -> None:
    from swag_converter.gui.jobs import Job

    job = Job(identifier="x", source=Path("a.png"), destination=Path("a.svg"), stage="doing something new")
    assert job.state()["progress"] == 0
