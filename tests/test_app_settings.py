"""Settings: CPU indexing by default, the GPU as an explicit choice (log.md Decision 110)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from contextfs.app.settings import SETTINGS_VERSION, Settings


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    monkeypatch.setenv("CONTEXTFS_HOME", str(tmp_path))
    return tmp_path


def _write(home: Path, data: dict) -> None:
    (home / "settings.json").write_text(json.dumps(data), encoding="utf-8")


def test_new_settings_index_on_the_cpu(home):
    s = Settings.load()
    assert s.gpu == "off" and s.version == SETTINGS_VERSION


def test_a_version_1_file_with_the_old_default_is_moved_to_the_cpu_once(home):
    _write(home, {"roots": ["C:\\x"], "gpu": "auto", "first_run_done": True})  # no version: version 1
    s = Settings.load()
    assert s.gpu == "off" and s.version == 2 and s.roots == ["C:\\x"]
    s.gpu = "auto"  # the user turns the GPU back on
    s.save()
    assert Settings.load().gpu == "auto"  # and it stays on


def test_a_current_file_keeps_the_users_gpu_choice(home):
    _write(home, {"gpu": "auto", "version": 2})
    assert Settings.load().gpu == "auto"


def test_the_indexer_child_gets_the_cpu_unless_the_gpu_is_chosen(home, tmp_path):
    from contextfs.app.gui import child_args

    s = Settings.load()
    s.roots = ["C:\\docs"]
    assert child_args(s, tmp_path / "index")[:3] == ["--indexer", "--gpu", "off"]
    s.gpu = "auto"
    assert child_args(s, tmp_path / "index")[:3] == ["--indexer", "--gpu", "auto"]


def test_the_child_maps_gpu_off_to_the_cpu_device(monkeypatch, tmp_path):
    from contextfs.app import indexer_child
    from contextfs.engine import Engine

    seen = {}

    class _Stop(Exception):
        pass

    def fake_open(index_dir, mode="read", device="cpu", **kw):
        seen["device"] = device
        raise _Stop

    monkeypatch.setattr(Engine, "open", staticmethod(fake_open))
    assert indexer_child.main(["--gpu", "off", str(tmp_path / "i"), str(tmp_path)]) == 1
    assert seen["device"] == "cpu"
