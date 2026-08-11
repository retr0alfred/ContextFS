"""Phase 2 tests: the CLI exposes every subcommand and never crashes on a stub."""

import subprocess
import sys
from pathlib import Path

from typer.testing import CliRunner

from contextfs.cli.main import EXIT_NOT_IMPLEMENTED, app

PROJECT_ROOT = Path(__file__).resolve().parents[1]
runner = CliRunner()

EXPECTED_COMMANDS = ["scan", "query", "timeline", "explain", "stats", "reset", "config"]


def unwrap(output: str) -> str:
    """Strip Rich's table borders and wrapping so long paths can be matched.

    Rich folds values that exceed the terminal width across several table
    rows; a naive substring check against a long path therefore fails for
    presentational reasons rather than behavioural ones.
    """
    for char in "│─┌┐└┘┬┴┼├┤":
        output = output.replace(char, "")
    return "".join(output.split())


def test_help_lists_every_subcommand():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0, result.output
    for command in EXPECTED_COMMANDS:
        assert command in result.output, f"{command} missing from --help"


def test_version_flag():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "contextfs" in result.output


def test_no_args_shows_help_not_a_crash():
    result = runner.invoke(app, [])
    # Click/Typer's `no_args_is_help` prints usage and exits 2. The point of
    # this test is that it is usage output, not a traceback.
    assert result.exit_code in (0, 2), result.output
    assert "Usage" in result.output
    assert "Traceback" not in result.output


STUBS = [
    (["scan"], 4),
    (["query", "some text"], 15),
    (["timeline", "March to April"], 11),
    (["explain", "abc123"], 16),
    (["stats"], 17),
    (["reset"], 17),
]


def test_every_stub_reports_not_implemented_cleanly():
    for argv, phase in STUBS:
        result = runner.invoke(app, argv)
        assert result.exit_code == EXIT_NOT_IMPLEMENTED, f"{argv} -> {result.output}"
        assert "not yet implemented" in result.output.lower(), argv
        assert f"Phase {phase}" in result.output, argv
        assert result.exception is None or isinstance(result.exception, SystemExit)


def test_each_subcommand_has_its_own_help():
    for command in EXPECTED_COMMANDS:
        result = runner.invoke(app, [command, "--help"])
        assert result.exit_code == 0, f"{command} --help failed: {result.output}"


def test_config_command_prints_resolved_configuration():
    result = runner.invoke(app, ["--config", str(PROJECT_ROOT / "contextfs.toml"), "config"])
    assert result.exit_code == 0, result.output
    assert "scan_root" in result.output
    assert "all-MiniLM-L6-v2" in result.output


def test_config_command_reports_bad_config_without_traceback(tmp_path):
    bad = tmp_path / "contextfs.toml"
    bad.write_text("[retrieval]\nweight_semantic = 0.9\n", encoding="utf-8")
    result = runner.invoke(app, ["--config", str(bad), "config"])
    assert result.exit_code == 2
    assert "Configuration error" in result.output


def test_root_flag_overrides_config(tmp_path):
    result = runner.invoke(
        app,
        [
            "--config",
            str(PROJECT_ROOT / "contextfs.toml"),
            "--root",
            str(tmp_path),
            "config",
            "--paths",
        ],
    )
    assert result.exit_code == 0, result.output
    assert tmp_path.name in unwrap(result.output)


def test_module_invocation_works():
    """`python -m contextfs` must work without the console script on PATH."""
    proc = subprocess.run(
        [sys.executable, "-m", "contextfs", "--version"],
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
    )
    assert proc.returncode == 0, proc.stderr
    assert "contextfs" in proc.stdout


def test_cli_does_not_import_heavy_ml_stack():
    """Startup must stay light: importing the CLI must not pull in torch/spaCy.

    On the target hardware, importing torch costs seconds. `contextfs --help`
    paying that price would make the whole tool feel broken.
    """
    code = (
        "import sys; import contextfs.cli.main as m; "
        "heavy=[n for n in ('torch','spacy','sentence_transformers','lancedb','transformers') "
        "if n in sys.modules]; print(','.join(heavy))"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, cwd=PROJECT_ROOT
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "", f"CLI import pulled in heavy modules: {proc.stdout}"
