"""Phase 1 smoke tests: the package installs, imports, and is version-tagged."""

from pathlib import Path

import contextfs

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_package_imports_and_has_version():
    assert isinstance(contextfs.__version__, str)
    assert contextfs.__version__.count(".") == 2


def test_expected_scaffold_directories_exist():
    for rel in ("src/contextfs", "tests", "data", "docs", "scripts"):
        assert (PROJECT_ROOT / rel).is_dir(), f"missing scaffold directory: {rel}"


def test_default_config_file_present_and_has_no_absolute_scan_path():
    cfg = PROJECT_ROOT / "contextfs.toml"
    assert cfg.is_file(), "contextfs.toml must ship with the repo"

    text = cfg.read_text(encoding="utf-8")
    assert "[paths]" in text

    # Guard the "never hardcode a scan path" constraint: no drive-letter or
    # POSIX-absolute path may appear in an assignment in the shipped config.
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or "=" not in stripped:
            continue
        value = stripped.split("=", 1)[1].strip()
        assert not value.startswith('"/'), f"absolute POSIX path in config: {stripped}"
        assert not (
            len(value) > 3 and value[1].isalpha() and value[2] == ":"
        ), f"absolute Windows path in config: {stripped}"
