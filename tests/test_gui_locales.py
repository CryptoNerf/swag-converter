"""Interface text, in both languages.

The value of these is not that English reads well — it is that a key used by
the page exists in every dictionary, because a missing one shows the key
itself to whoever is reading in that language.
"""

from __future__ import annotations

import json
from pathlib import Path
import re

import pytest

from swag_converter.gui import locales
from swag_converter.gui.bridge import Bridge
from swag_converter.gui.store import Store

WEB = Path(locales.HERE).parent / "web"


def _dictionaries() -> dict[str, dict[str, str]]:
    return {code: locales._read(code) for code in locales.codes()}


def test_both_languages_are_offered() -> None:
    codes = locales.codes()
    assert "en" in codes and "ru" in codes
    assert all(entry["name"] for entry in locales.available())


def test_every_language_covers_every_key() -> None:
    dictionaries = _dictionaries()
    english = dictionaries[locales.FALLBACK]
    for code, strings in dictionaries.items():
        missing = sorted(set(english) - set(strings))
        assert not missing, f"{code} is missing {missing}"
        extra = sorted(set(strings) - set(english))
        assert not extra, f"{code} has keys English does not: {extra}"


def test_placeholders_match_across_languages() -> None:
    """A translation that drops {n} silently loses the number."""
    dictionaries = _dictionaries()
    english = dictionaries[locales.FALLBACK]
    for code, strings in dictionaries.items():
        for key, text in english.items():
            assert set(re.findall(r"{(\w+)}", text)) == set(re.findall(r"{(\w+)}", strings[key])), \
                f"{code}:{key} does not use the same placeholders"


def test_every_key_the_page_asks_for_exists() -> None:
    """data-t in the markup and t('...') in the script, against the dictionary."""
    english = locales._read(locales.FALLBACK)
    markup = (WEB / "index.html").read_text(encoding="utf-8")
    script = (WEB / "app.js").read_text(encoding="utf-8")

    wanted = set(re.findall(r'data-t(?:-title)?="([^"]+)"', markup))
    # A trailing dot is the start of a key the script builds, like
    # t('preset.' + value); those are checked exhaustively just below.
    wanted |= {key for key in re.findall(r"\bt\('([a-z][\w.]*)'", script)
               if not key.endswith(".")}
    # Keys the script builds from a value, checked against what the bridge offers.
    for prefix, values in (("preset", ["auto", "icon", "illustration", "photo", "poster"]),
                           ("quality", ["fast", "balanced", "max"]),
                           ("bg", ["auto", "always", "keep"]),
                           ("edge", ["640", "1024", "1600", "2400", "0"])):
        for value in values:
            wanted.add(f"{prefix}.{value}")
            if prefix in ("preset", "quality", "bg"):
                wanted.add(f"{prefix}.{value}.note")

    missing = sorted(key for key in wanted if key not in english)
    assert not missing, f"the page asks for keys no dictionary has: {missing}"


def test_nothing_in_the_dictionary_goes_unused() -> None:
    """A key nobody asks for is a sentence somebody translated for nothing."""
    english = locales._read(locales.FALLBACK)
    markup = (WEB / "index.html").read_text(encoding="utf-8")
    script = (WEB / "app.js").read_text(encoding="utf-8")
    blob = markup + script
    unused = []
    for key in english:
        if key == "language.name":
            continue
        stem = key.rsplit(".note", 1)[0]
        prefix = key.split(".", 1)[0]
        if key in blob or stem in blob or f"'{prefix}." in blob or f'"{prefix}.' in blob:
            continue
        unused.append(key)
    assert not unused, f"nothing uses {unused}"


def test_an_unfinished_translation_falls_back_to_english(monkeypatch) -> None:
    monkeypatch.setattr(locales, "_read", lambda code: {"bar.convert": "X"} if code == "zz"
                        else json.loads((locales.HERE / "en.json").read_text(encoding="utf-8")))
    strings = locales.strings("zz")
    assert strings["bar.convert"] == "X"
    assert strings["bar.stop"] == "Stop"          # not a bare key, not empty


def test_the_window_remembers_the_language(tmp_path: Path) -> None:
    store = Store(tmp_path / "settings.json")
    bridge = Bridge(store=store)
    try:
        assert bridge.describe()["strings"]["bar.convert"] == "Convert"
        bridge.update_settings({"language": "ru"})
        assert bridge.describe()["strings"]["bar.convert"] == "Конвертировать"
    finally:
        bridge.shutdown()

    again = Bridge(store=Store(tmp_path / "settings.json"))
    try:
        assert again.settings["language"] == "ru"
        assert again.describe()["strings"]["bar.convert"] == "Конвертировать"
    finally:
        again.shutdown()


def test_an_unknown_language_is_refused(tmp_path: Path) -> None:
    bridge = Bridge(store=Store(tmp_path / "settings.json"))
    try:
        bridge.update_settings({"language": "../../etc/passwd"})
        assert bridge.settings["language"] == "en"
    finally:
        bridge.shutdown()


def test_changing_the_language_does_not_age_a_result(tmp_path: Path) -> None:
    """Translation is not a tracing setting; results must stay current."""
    bridge = Bridge(store=Store(tmp_path / "settings.json"))
    try:
        before = bridge._tracing_options()
        bridge.update_settings({"language": "ru", "auto_update": False})
        assert bridge._tracing_options() == before
    finally:
        bridge.shutdown()


def test_the_real_settings_file_is_never_touched_by_a_test_run() -> None:
    """The guard that makes the rest of these safe to run on a real machine.

    A Bridge writes preferences as it is used. Without the redirection in
    conftest, running the suite rewrote the preferences of whoever ran it —
    which it did, once, before this existed.
    """
    import os

    from swag_converter.gui.store import HOME_VARIABLE, config_directory

    assert os.environ.get(HOME_VARIABLE), "the settings sandbox is not set up"
    resolved = str(config_directory())
    assert "Application Support/swag-converter" not in resolved
    assert resolved.startswith(os.environ[HOME_VARIABLE])
