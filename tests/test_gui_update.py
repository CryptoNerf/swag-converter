"""Updating an unsigned app.

Without a code signature the only things standing between a user and someone
else's code are TLS to a host we named and the checksum published beside the
build.  These make sure neither is optional.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from swag_converter.gui import updater as up


def test_versions_compare_as_numbers() -> None:
    assert up.version_tuple("0.2.10") > up.version_tuple("0.2.9")
    assert up.version_tuple("v0.3.0") > up.version_tuple("0.2.1")
    assert up.version_tuple("1.0.0") > up.version_tuple("0.99.99")
    assert up.version_tuple("0.2.1") == up.version_tuple("v0.2.1")
    assert up.version_tuple("") == (0,)


@pytest.mark.parametrize("url", [
    "http://github.com/CryptoNerf/swag-converter",      # not TLS
    "https://evil.example/releases/latest",             # not a host we named
    "https://api.github.com.evil.example/x",            # nor one that looks like it
    "ftp://github.com/x",
])
def test_only_github_over_tls_is_fetched(url: str) -> None:
    with pytest.raises(RuntimeError):
        up._check_host(url)


def test_the_hosts_we_do_use_are_accepted() -> None:
    for url in ("https://api.github.com/repos/x/y/releases/latest",
                "https://objects.githubusercontent.com/thing.dmg"):
        up._check_host(url)


def test_a_checksum_line_is_matched_by_name() -> None:
    sums = "aaa  other.dmg\nbbb  swag-converter-1.0.0-macos-arm64.dmg\n"
    assert up._digest_for(sums, "swag-converter-1.0.0-macos-arm64.dmg") == "bbb"
    assert up._digest_for(sums, "not-listed.dmg") is None


def test_a_release_without_a_checksum_is_refused(tmp_path: Path, monkeypatch) -> None:
    """An unsigned bundle is not something to install on trust alone."""
    updater = up.Updater("0.1.0")
    release = up.Available(version="9.9.9", url="https://github.com/x.dmg",
                           name="x.dmg", digest=None, notes="")
    with pytest.raises(RuntimeError, match="checksum"):
        updater._download(release)
    assert updater.status()["ready"] is False


def test_a_download_that_does_not_match_is_discarded(tmp_path: Path, monkeypatch) -> None:
    """The one check that matters: swap the bytes, the install must not happen."""
    served = b"definitely not the build you published"

    def fake_fetch_to(url, destination, limit=0):
        destination.write_bytes(served)

    monkeypatch.setattr(up, "_fetch_to", fake_fetch_to)
    honest = hashlib.sha256(b"the build you published").hexdigest()

    updater = up.Updater("0.1.0")
    release = up.Available(version="9.9.9", url="https://github.com/x.dmg",
                           name="x.dmg", digest=honest, notes="")
    with pytest.raises(RuntimeError, match="checksum"):
        updater._download(release)
    assert updater.status()["ready"] is False
    assert updater.status()["phase"] != "ready"


def test_a_release_no_newer_than_ours_is_left_alone(monkeypatch) -> None:
    payload = {
        "tag_name": "v0.2.1",
        "assets": [{"name": "swag.dmg", "browser_download_url": "https://github.com/swag.dmg"}],
        "body": "notes",
    }
    monkeypatch.setattr(up, "_fetch", lambda url, limit=0: json.dumps(payload).encode())
    monkeypatch.setattr(up, "running_bundle", lambda: Path("/Applications/x.app"))
    downloaded = []
    updater = up.Updater("0.2.1")
    monkeypatch.setattr(updater, "_download", lambda release: downloaded.append(release))
    assert updater.check()["phase"] == "current"
    assert downloaded == []


def test_running_from_source_does_not_offer_updates() -> None:
    """There is no bundle to replace, and saying so beats failing later."""
    assert up.running_bundle() is None
    updater = up.Updater("0.2.1")
    assert updater.check()["phase"] == "unsupported"
    assert updater.status()["supported"] is False
    assert updater.apply_and_restart() == "unsupported"


def test_nothing_staged_means_nothing_to_apply(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(up, "running_bundle", lambda: tmp_path / "x.app")
    assert up.Updater("0.2.1").apply_and_restart() == "nothing staged"


def test_only_our_own_bundle_is_accepted(tmp_path: Path) -> None:
    """Whatever the disk image holds has to be this application."""
    import plistlib

    bundle = tmp_path / "Other.app"
    (bundle / "Contents" / "MacOS").mkdir(parents=True)
    executable = bundle / "Contents" / "MacOS" / "other"
    executable.write_text("#!/bin/sh\n")
    executable.chmod(0o755)
    (bundle / "Contents" / "Info.plist").write_bytes(
        plistlib.dumps({"CFBundleIdentifier": "com.example.other", "CFBundleExecutable": "other"})
    )
    assert up._is_application(bundle) is False

    (bundle / "Contents" / "Info.plist").write_bytes(
        plistlib.dumps({"CFBundleIdentifier": "com.cryptonerf.swagconverter",
                        "CFBundleExecutable": "other"})
    )
    assert up._is_application(bundle) is True


def test_paths_with_spaces_and_quotes_survive_the_swap_script() -> None:
    assert up._quote(Path("/Applications/sw(a)g.converter.app")) == "'/Applications/sw(a)g.converter.app'"
    assert up._quote(Path("/a/it's here.app")) == "'/a/it'\\''s here.app'"
