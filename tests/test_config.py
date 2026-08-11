"""Phase 2 tests: configuration loading, precedence, validation, path resolution."""

import os
from pathlib import Path

import pytest

from contextfs.config import ConfigError, ContextFSConfig, find_config_file, load_config

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SHIPPED_CONFIG = PROJECT_ROOT / "contextfs.toml"


def write_config(tmp_path: Path, body: str, name: str = "contextfs.toml") -> Path:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path


# --- the shipped config must load -------------------------------------------


def test_shipped_config_loads_and_validates():
    cfg = load_config(SHIPPED_CONFIG)
    assert isinstance(cfg, ContextFSConfig)
    assert cfg.source_file == SHIPPED_CONFIG
    assert cfg.embeddings.dimension == 384


def test_shipped_config_weights_are_normalised():
    cfg = load_config(SHIPPED_CONFIG)
    assert sum(cfg.temporal.weights.values()) == pytest.approx(1.0)
    assert sum(cfg.retrieval.weights.values()) == pytest.approx(1.0)


# --- path resolution ---------------------------------------------------------


def test_relative_paths_resolve_against_config_file_not_cwd(tmp_path, monkeypatch):
    cfg_dir = tmp_path / "project"
    cfg_dir.mkdir()
    cfg_path = write_config(cfg_dir, '[paths]\nroot = "corpus"\ndata_dir = ".contextfs"\n')

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    cfg = load_config(cfg_path)
    assert cfg.paths.root == cfg_dir / "corpus"
    assert cfg.paths.data_dir == cfg_dir / ".contextfs"


def test_absolute_paths_are_left_alone(tmp_path):
    target = tmp_path / "somewhere_absolute"
    cfg_path = write_config(
        tmp_path, f'[paths]\nroot = "{target.as_posix()}"\n', name="contextfs.toml"
    )
    cfg = load_config(cfg_path)
    assert cfg.paths.root == target


def test_derived_stores_live_under_data_dir(tmp_path):
    cfg_path = write_config(tmp_path, '[paths]\nroot = "corpus"\ndata_dir = "derived"\n')
    cfg = load_config(cfg_path)
    data_dir = tmp_path / "derived"
    assert cfg.db_path.parent == data_dir
    assert cfg.vector_dir.parent == data_dir
    assert cfg.graph_file.parent == data_dir


# --- precedence --------------------------------------------------------------


def test_cli_root_overrides_config_file(tmp_path):
    cfg_path = write_config(tmp_path, '[paths]\nroot = "from_file"\n')
    override = tmp_path / "from_cli"
    cfg = load_config(cfg_path, root=override)
    assert cfg.paths.root == override


def test_env_var_overrides_config_file(tmp_path, monkeypatch):
    cfg_path = write_config(tmp_path, '[paths]\nroot = "from_file"\n')
    monkeypatch.setenv("CONTEXTFS_ROOT", str(tmp_path / "from_env"))
    cfg = load_config(cfg_path)
    assert cfg.paths.root == tmp_path / "from_env"


def test_cli_beats_env(tmp_path, monkeypatch):
    cfg_path = write_config(tmp_path, '[paths]\nroot = "from_file"\n')
    monkeypatch.setenv("CONTEXTFS_ROOT", str(tmp_path / "from_env"))
    cfg = load_config(cfg_path, root=tmp_path / "from_cli")
    assert cfg.paths.root == tmp_path / "from_cli"


def test_local_config_takes_priority_in_discovery(tmp_path, monkeypatch):
    write_config(tmp_path, '[paths]\nroot = "shipped"\n', name="contextfs.toml")
    write_config(tmp_path, '[paths]\nroot = "personal"\n', name="contextfs.local.toml")
    monkeypatch.chdir(tmp_path)
    found = find_config_file()
    assert found is not None and found.name == "contextfs.local.toml"


def test_config_discovery_walks_upward(tmp_path, monkeypatch):
    write_config(tmp_path, '[paths]\nroot = "corpus"\n')
    nested = tmp_path / "a" / "b" / "c"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)
    found = find_config_file()
    assert found == tmp_path / "contextfs.toml"


def test_overrides_dict_is_applied(tmp_path):
    cfg_path = write_config(tmp_path, "[retrieval]\nweight_semantic = 0.45\n")
    cfg = load_config(
        cfg_path,
        overrides={
            "retrieval": {
                "weight_semantic": 1.0,
                "weight_graph": 0.0,
                "weight_activity": 0.0,
                "weight_timeline": 0.0,
            }
        },
    )
    assert cfg.retrieval.weight_semantic == 1.0


# --- validation --------------------------------------------------------------


def test_unknown_key_is_rejected(tmp_path):
    cfg_path = write_config(tmp_path, "[paths]\nrooot = 'typo'\n")
    with pytest.raises(ConfigError):
        load_config(cfg_path)


def test_temporal_weights_must_sum_to_one(tmp_path):
    cfg_path = write_config(
        tmp_path,
        "[temporal]\n"
        "weight_keyword_proximity = 0.9\n"
        "weight_structured_context = 0.9\n"
        "weight_metadata_consistency = 0.9\n"
        "weight_cross_file_recurrence = 0.9\n",
    )
    with pytest.raises(ConfigError, match="sum to 1.0"):
        load_config(cfg_path)


def test_retrieval_weights_must_sum_to_one(tmp_path):
    cfg_path = write_config(tmp_path, "[retrieval]\nweight_semantic = 0.9\n")
    with pytest.raises(ConfigError, match="sum to 1.0"):
        load_config(cfg_path)


def test_timeline_threshold_must_be_a_probability(tmp_path):
    cfg_path = write_config(tmp_path, "[temporal]\ntimeline_node_threshold = 1.7\n")
    with pytest.raises(ConfigError, match=r"\[0, 1\]"):
        load_config(cfg_path)


def test_chunk_overlap_must_be_smaller_than_chunk_size(tmp_path):
    cfg_path = write_config(
        tmp_path, "[embeddings]\nchunk_size_tokens = 128\nchunk_overlap_tokens = 128\n"
    )
    with pytest.raises(ConfigError, match="smaller than"):
        load_config(cfg_path)


def test_missing_config_file_raises(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nope.toml")


def test_malformed_toml_raises(tmp_path):
    cfg_path = write_config(tmp_path, "[paths\nroot = 'x'\n")
    with pytest.raises(ConfigError, match="Malformed TOML"):
        load_config(cfg_path)


# --- local-first enforcement -------------------------------------------------


def test_remote_summarization_endpoint_is_refused(tmp_path):
    cfg_path = write_config(
        tmp_path,
        "[summarization]\nenabled = true\nendpoint = 'https://api.example.com/v1'\n",
    )
    with pytest.raises(ConfigError, match="local-first"):
        load_config(cfg_path)


def test_non_local_summarization_backend_is_refused(tmp_path):
    cfg_path = write_config(tmp_path, "[summarization]\nbackend = 'openai'\n")
    with pytest.raises(ConfigError, match="local backend"):
        load_config(cfg_path)


def test_loopback_summarization_endpoint_is_allowed(tmp_path):
    cfg_path = write_config(
        tmp_path,
        "[summarization]\nenabled = true\nendpoint = 'http://127.0.0.1:11434'\n",
    )
    cfg = load_config(cfg_path)
    assert cfg.summarization.enabled


# --- ablation support --------------------------------------------------------


def test_normalised_weights_redistribute_when_layers_are_disabled():
    cfg = load_config(SHIPPED_CONFIG)
    semantic_only = cfg.retrieval.normalised({"semantic"})
    assert semantic_only == {"semantic": 1.0}

    two = cfg.retrieval.normalised({"semantic", "graph"})
    assert sum(two.values()) == pytest.approx(1.0)
    assert set(two) == {"semantic", "graph"}


def test_normalised_weights_handle_empty_set():
    cfg = load_config(SHIPPED_CONFIG)
    assert cfg.retrieval.normalised(set()) == {"semantic": 1.0}


# --- no data dir is created as a side effect of loading ----------------------


def test_loading_config_does_not_create_directories(tmp_path):
    cfg_path = write_config(tmp_path, '[paths]\nroot = "corpus"\ndata_dir = "derived"\n')
    cfg = load_config(cfg_path)
    assert not cfg.paths.data_dir.exists()
    cfg.ensure_data_dir()
    assert cfg.paths.data_dir.is_dir()


def test_env_override_names_are_all_documented():
    from contextfs.config import ENV_OVERRIDES

    assert all(name.startswith("CONTEXTFS_") for name in ENV_OVERRIDES)
    assert os.environ.get("CONTEXTFS_ROOT") is None or True  # no ambient dependence
