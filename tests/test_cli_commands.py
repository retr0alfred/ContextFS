"""Phase 24: behavioural coverage of the CLI command surface.

The CLI is the primary deliverable, and before this module it sat at 19% line
coverage - every command had been exercised by hand against the live index, but
nothing pinned that behaviour against regression.

These tests deliberately avoid the ML stack. A fixture writes a small index
*directly* into SQLite plus a graph JSON file, which is enough for every command
that reads the index rather than building it (`digest`, `duplicates`,
`projects`, `tags`, `feedback`, `stats`, `explain`, `reset`, `config`). The
commands that must embed - `scan`, `query`, `timeline` - are covered by their
own integration tests elsewhere; what is checked here is that they fail
*cleanly* when there is no index, which is the path a real user hits first.

Keeping these fast matters: on the target hardware, a test module that loaded
torch would add minutes to every run and would stop being run.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import networkx as nx
import pytest
from typer.testing import CliRunner

from contextfs.cli.main import EXIT_CONFIG_ERROR, app
from contextfs.graph import save_graph
from contextfs.store import Store

runner = CliRunner()


def unwrap(output: str) -> str:
    """Strip Rich's borders and wrapping so long values can be matched."""
    for char in "│─┌┐└┘┬┴┼├┤|+-":
        output = output.replace(char, "")
    return "".join(output.split())


@pytest.fixture
def indexed(tmp_path):
    """Build a small, model-free index and return the flags that select it."""
    data_dir = tmp_path / "derived"
    corpus = tmp_path / "corpus"
    corpus.mkdir()

    # Relative to the real clock, not a fixed date: the CLI calls the insight
    # functions without a `now` override, so a hardcoded date would silently
    # drift from "active" to "dormant" as the calendar moved and the test would
    # start failing for a reason that has nothing to do with the code.
    now = datetime.now()
    files = [
        ("Work/Report/draft.docx", 4000, now - timedelta(days=2), "dup-hash"),
        ("Work/Report/final.docx", 4000, now - timedelta(days=1), "dup-hash"),
        ("Work/Report/notes.md", 900, now - timedelta(days=3), "n1"),
        ("Archive/2019/old.txt", 100, now - timedelta(days=900), "o1"),
        ("Archive/2019/older.txt", 100, now - timedelta(days=950), "o2"),
    ]
    with Store(data_dir / "contextfs.db") as store:
        records = []
        for path, size, mtime, digest_hash in files:
            stamp = mtime.isoformat(timespec="seconds")
            folder, _, name = path.rpartition("/")
            records.append(
                {
                    "path": path,
                    "abs_path": str(corpus / path),
                    "name": name,
                    "stem": name.rsplit(".", 1)[0],
                    "ext": "." + name.rsplit(".", 1)[-1],
                    "folder": folder,
                    "depth": path.count("/"),
                    "size": size,
                    "mtime_ns": 0,
                    "mtime": stamp,
                    "content_hash": digest_hash,
                    "seen_at": stamp,
                    "content_changed_at": stamp,
                }
            )
        store.upsert_files(records)
        ids = {path: store.get_file(path)["id"] for path, *_ in files}

    graph = nx.MultiDiGraph()
    graph.add_edge(
        f"file:{ids['Work/Report/draft.docx']}",
        f"file:{ids['Work/Report/final.docx']}",
        type="duplicate",
        weight=0.62,
    )
    save_graph(graph, data_dir / "graph.json")

    return ["--config", "contextfs.toml", "--root", str(corpus), "--data-dir", str(data_dir)]


def invoke(flags, *args):
    """Run the CLI with the fixture's flags plus command arguments."""
    return runner.invoke(app, [*flags, *args])


# ---------------------------------------------------------------------------
# Commands that read the index
# ---------------------------------------------------------------------------


def test_digest_reports_the_indexed_tree(indexed):
    result = invoke(indexed, "digest")
    assert result.exit_code == 0, result.output
    assert "5files" in unwrap(result.output)
    assert ".docx" in result.output
    assert ".txt" in result.output


def test_digest_json_is_machine_readable(indexed):
    result = invoke(indexed, "digest", "--json")
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["files"] == 5
    assert payload["bytes"] == 9100
    assert payload["duplicate_groups"] == 1


def test_duplicates_finds_the_planted_pair(indexed):
    result = invoke(indexed, "duplicates")
    assert result.exit_code == 0, result.output
    flat = unwrap(result.output)
    assert "Work/Report/draft.docx" in flat
    assert "Work/Report/final.docx" in flat


def test_duplicates_never_offers_to_delete(indexed):
    """The read-only guarantee has to be visible at the surface, not just held."""
    result = invoke(indexed, "duplicates")
    assert "never deletes" in result.output.lower().replace("\n", " ")


def test_duplicates_keeps_the_newer_file(indexed):
    result = invoke(indexed, "duplicates", "--json")
    assert json.loads(result.output)[0]["keeper"] == "Work/Report/final.docx"


def test_projects_classifies_by_recency(indexed):
    result = invoke(indexed, "projects", "--json")
    assert result.exit_code == 0, result.output
    stages = {p["folder"]: p["stage"] for p in json.loads(result.output)}
    assert stages["Work/Report"] == "active"
    assert stages["Archive/2019"] == "finished"


def test_every_project_row_explains_itself(indexed):
    for project in json.loads(invoke(indexed, "projects", "--json").output):
        assert project["reason"], project


def test_tags_resolves_a_unique_substring(indexed):
    result = invoke(indexed, "tags", "notes.md")
    assert result.exit_code == 0, result.output
    assert "Work/Report/notes.md" in unwrap(result.output)


def test_tags_refuses_an_ambiguous_substring(indexed):
    result = invoke(indexed, "tags", ".docx")
    assert result.exit_code == EXIT_CONFIG_ERROR
    assert "ambiguous" in result.output.lower()


def test_tags_reports_an_unknown_file_without_a_traceback(indexed):
    result = invoke(indexed, "tags", "nothing-like-this")
    assert result.exit_code == EXIT_CONFIG_ERROR
    assert "Traceback" not in result.output


def test_stats_runs_against_a_partial_index(indexed):
    """`stats` must survive an index whose later layers were never built."""
    result = invoke(indexed, "stats")
    assert result.exit_code == 0, result.output
    assert "Traceback" not in result.output


# ---------------------------------------------------------------------------
# Feedback
# ---------------------------------------------------------------------------


def test_feedback_with_nothing_recorded_is_not_an_error(indexed):
    result = invoke(indexed, "feedback")
    assert result.exit_code == 0, result.output
    assert "no feedback recorded" in result.output.lower()


def test_feedback_without_a_previous_query_fails_cleanly(indexed):
    result = invoke(indexed, "feedback", "--pick", "1")
    assert result.exit_code == EXIT_CONFIG_ERROR
    assert "Traceback" not in result.output


def _seed_last_query(flags, file_id, path):
    """Write a last_query.json as `query` would have."""
    data_dir = flags[flags.index("--data-dir") + 1]
    from pathlib import Path

    payload = {
        "query": "quarterly report",
        "results": [{"rank": 1, "file_id": file_id, "path": path, "score": 0.5}],
    }
    Path(data_dir, "last_query.json").write_text(json.dumps(payload), encoding="utf-8")


def test_feedback_records_a_pick_from_the_last_query(indexed):
    with Store(indexed[indexed.index("--data-dir") + 1] + "/contextfs.db") as store:
        file_id = store.get_file("Work/Report/final.docx")["id"]
    _seed_last_query(indexed, file_id, "Work/Report/final.docx")

    result = invoke(indexed, "feedback", "--pick", "1")
    assert result.exit_code == 0, result.output

    listed = invoke(indexed, "feedback", "--show")
    assert "quarterly report" in unwrap(listed.output).replace(
        "quarterlyreport", "quarterly report"
    )


def test_feedback_rejects_an_out_of_range_rank(indexed):
    with Store(indexed[indexed.index("--data-dir") + 1] + "/contextfs.db") as store:
        file_id = store.get_file("Work/Report/final.docx")["id"]
    _seed_last_query(indexed, file_id, "Work/Report/final.docx")

    result = invoke(indexed, "feedback", "--pick", "9")
    assert result.exit_code == EXIT_CONFIG_ERROR
    assert "rank 9" in result.output


def test_feedback_clear_empties_the_table(indexed):
    with Store(indexed[indexed.index("--data-dir") + 1] + "/contextfs.db") as store:
        file_id = store.get_file("Work/Report/final.docx")["id"]
    _seed_last_query(indexed, file_id, "Work/Report/final.docx")
    invoke(indexed, "feedback", "--pick", "1")

    result = invoke(indexed, "feedback", "--clear")
    assert result.exit_code == 0
    with Store(indexed[indexed.index("--data-dir") + 1] + "/contextfs.db") as store:
        assert store.feedback_count() == 0


# ---------------------------------------------------------------------------
# Failure paths a first-time user actually hits
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "command",
    ["query", "timeline", "digest", "duplicates", "projects", "tags", "feedback"],
)
def test_every_index_reading_command_fails_cleanly_with_no_index(tmp_path, command):
    """The most common first-run mistake must never produce a traceback."""
    args = ["--config", "contextfs.toml", "--data-dir", str(tmp_path / "empty"), command]
    if command in {"query", "timeline", "tags"}:
        args.append("anything")
    result = runner.invoke(app, args)
    assert result.exit_code == EXIT_CONFIG_ERROR, result.output
    assert "Traceback" not in result.output
    assert "scan" in result.output.lower()


def test_explain_without_a_previous_query_fails_cleanly(tmp_path):
    result = runner.invoke(
        app, ["--config", "contextfs.toml", "--data-dir", str(tmp_path / "d"), "explain", "1"]
    )
    assert result.exit_code != 0
    assert "Traceback" not in result.output


def test_reset_removes_only_the_derived_directory(indexed, tmp_path):
    corpus = tmp_path / "corpus"
    (corpus / "keepme.txt").write_text("untouched", encoding="utf-8")
    data_dir = tmp_path / "derived"
    assert data_dir.exists()

    result = invoke(indexed, "reset", "--yes")
    assert result.exit_code == 0, result.output
    assert not data_dir.exists() or not any(data_dir.iterdir())
    assert (corpus / "keepme.txt").read_text(encoding="utf-8") == "untouched"
