"""Configuration loading, validation, and path resolution for ContextFS.

Design contract (enforced by tests in ``tests/test_config.py``):

* **No scan path is ever hardcoded.** The root directory comes from a config
  file, an environment variable, or a CLI flag - in that order of increasing
  precedence.
* **Every relative path is resolved against the config file that declared it**,
  not against the current working directory. This keeps ``contextfs`` runnable
  from any directory without silently pointing at a different index.
* **All derived data is confined to ``paths.data_dir``.** Nothing is written
  outside it, ever.

Precedence, highest wins::

    1. CLI flag              --root / --config / --data-dir
    2. Environment variable  CONTEXTFS_ROOT, CONTEXTFS_DATA_DIR, ...
    3. contextfs.local.toml  (git-ignored; for real personal paths)
    4. contextfs.toml        (shipped defaults)
    5. Built-in defaults     (this module)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - only exercised on 3.10
    import tomli as tomllib

__all__ = [
    "ContextFSConfig",
    "ConfigError",
    "load_config",
    "find_config_file",
    "DEFAULT_CONFIG_NAMES",
]

#: Config file names searched for, in priority order. The ``.local`` variant is
#: git-ignored and is intended to hold real personal paths.
DEFAULT_CONFIG_NAMES = ("contextfs.local.toml", "contextfs.toml")

#: Environment variables that override individual settings.
ENV_OVERRIDES = {
    "CONTEXTFS_ROOT": ("paths", "root"),
    "CONTEXTFS_DATA_DIR": ("paths", "data_dir"),
    "CONTEXTFS_SPACY_MODEL": ("entities", "spacy_model"),
    "CONTEXTFS_EMBED_MODEL": ("embeddings", "model"),
    "CONTEXTFS_DEVICE": ("embeddings", "device"),
}

_WEIGHT_TOLERANCE = 1e-6


class ConfigError(RuntimeError):
    """Raised when configuration is missing, malformed, or internally inconsistent."""


class _Section(BaseModel):
    """Base for config sections: reject unknown keys so typos fail loudly."""

    model_config = ConfigDict(extra="forbid", frozen=False)


class GeneralConfig(_Section):
    """Identification of this index."""

    profile_name: str = "default"


class PathsConfig(_Section):
    """Filesystem locations. All are resolved to absolute paths at load time."""

    root: Path = Path("data/synthetic/corpus")
    data_dir: Path = Path(".contextfs")
    sqlite_path: Path = Path("contextfs.db")
    vector_path: Path = Path("vectors.lance")
    graph_path: Path = Path("graph.json")


class ScanConfig(_Section):
    """Traversal rules for the file scanner (Layer 1)."""

    ignore_dirs: list[str] = Field(default_factory=list)
    ignore_globs: list[str] = Field(default_factory=list)
    include_extensions: list[str] = Field(default_factory=list)
    max_file_size_mb: float = 50.0
    follow_symlinks: bool = False

    @property
    def max_file_size_bytes(self) -> int:
        """Size ceiling for content extraction, in bytes."""
        return int(self.max_file_size_mb * 1024 * 1024)


class ExtractionConfig(_Section):
    """Content extraction limits (Layer 2)."""

    max_chars_per_document: int = 400_000


class EntitiesConfig(_Section):
    """spaCy entity extraction settings (Layer 3)."""

    spacy_model: str = "en_core_web_md"
    max_keywords: int = 25
    drop_acronym_orgs: bool = True
    gazetteer_propagation: bool = True
    gazetteer_min_length: int = 4


class EmbeddingsConfig(_Section):
    """Embedding model and chunking settings (Layer 4)."""

    model: str = "sentence-transformers/all-MiniLM-L6-v2"
    dimension: int = 384
    chunk_size_tokens: int = 256
    chunk_overlap_tokens: int = 48
    batch_size: int = 16
    device: str = "cpu"
    backend: str = "transformers"
    num_threads: int = 0

    @model_validator(mode="after")
    def _check_backend(self) -> EmbeddingsConfig:
        if self.backend not in {"transformers", "sentence-transformers"}:
            raise ConfigError(
                f"[embeddings] backend must be 'transformers' or "
                f"'sentence-transformers', got {self.backend!r}"
            )
        return self

    @model_validator(mode="after")
    def _check_chunking(self) -> EmbeddingsConfig:
        if self.chunk_overlap_tokens >= self.chunk_size_tokens:
            raise ConfigError(
                f"[embeddings] chunk_overlap_tokens ({self.chunk_overlap_tokens}) must be "
                f"smaller than chunk_size_tokens ({self.chunk_size_tokens}); otherwise "
                "chunking never advances."
            )
        return self


class SummarizationConfig(_Section):
    """Optional local LLM summarisation (Layer 5). Never required to function."""

    enabled: bool = False
    backend: str = "ollama"
    model: str = "llama3.2:3b"
    endpoint: str = "http://127.0.0.1:11434"
    timeout_seconds: int = 120

    @model_validator(mode="after")
    def _check_local_only(self) -> SummarizationConfig:
        """Enforce the local-first constraint at the configuration boundary.

        This is a hard guard, not a lint: it makes it impossible to point
        ContextFS at a remote inference endpoint by editing a config file.
        """
        if self.backend not in {"ollama", "none"}:
            raise ConfigError(
                f"[summarization] backend must be a local backend ('ollama' or 'none'), "
                f"got {self.backend!r}. ContextFS forbids remote inference."
            )
        allowed_hosts = ("127.0.0.1", "localhost", "::1", "0.0.0.0")
        if self.enabled and not any(h in self.endpoint for h in allowed_hosts):
            raise ConfigError(
                f"[summarization] endpoint {self.endpoint!r} is not a loopback address. "
                "ContextFS is local-first: remote inference endpoints are forbidden."
            )
        return self


class GraphConfig(_Section):
    """Relationship graph construction thresholds (Layer 6)."""

    semantic_edge_threshold: float = 0.55
    semantic_edges_per_node: int = 8
    min_shared_entities: int = 2
    #: Jaccard threshold over word shingles - NOT a cosine threshold.
    duplicate_threshold: float = 0.25
    #: Cosine pre-filter for duplicate candidates.
    duplicate_candidate_similarity: float = 0.70


class TemporalConfig(_Section):
    """Meaningful-vs-incidental date classification (Layer 7).

    The four weights are the paper's scoring formula. They are validated to sum
    to 1.0 so the resulting relevance score is genuinely on a 0-1 scale rather
    than an arbitrary sum.
    """

    weight_keyword_proximity: float = 0.40
    weight_structured_context: float = 0.25
    weight_metadata_consistency: float = 0.20
    weight_cross_file_recurrence: float = 0.15
    timeline_node_threshold: float = 0.55
    keyword_window_tokens: int = 12
    metadata_consistency_window_days: int = 60
    #: Multiplier applied to a mention that carries only year precision.
    year_only_penalty: float = 0.35
    #: File count at which the cross-file recurrence signal saturates.
    recurrence_saturation: int = 4

    @model_validator(mode="after")
    def _check_weights(self) -> TemporalConfig:
        total = (
            self.weight_keyword_proximity
            + self.weight_structured_context
            + self.weight_metadata_consistency
            + self.weight_cross_file_recurrence
        )
        if abs(total - 1.0) > _WEIGHT_TOLERANCE:
            raise ConfigError(
                f"[temporal] signal weights must sum to 1.0, got {total:.6f}. "
                "A non-normalised sum makes the 0-1 relevance score meaningless."
            )
        if not 0.0 <= self.timeline_node_threshold <= 1.0:
            raise ConfigError(
                f"[temporal] timeline_node_threshold must be in [0, 1], "
                f"got {self.timeline_node_threshold}"
            )
        return self

    @property
    def weights(self) -> dict[str, float]:
        """Signal weights keyed by signal name, for reporting and ablation."""
        return {
            "keyword_proximity": self.weight_keyword_proximity,
            "structured_context": self.weight_structured_context,
            "metadata_consistency": self.weight_metadata_consistency,
            "cross_file_recurrence": self.weight_cross_file_recurrence,
        }


class ActivityConfig(_Section):
    """Activity session reconstruction (Layer 8)."""

    session_gap_hours: float = 72.0
    session_link_threshold: float = 0.35
    min_session_size: int = 2


class RetrievalConfig(_Section):
    """Hybrid ranking weights and traversal budgets (Layer 9).

    Weights are validated to sum to 1.0 so that a final score is comparable
    across ablation configurations (Phase 22) rather than drifting in scale
    whenever a signal is switched off.
    """

    weight_semantic: float = 0.45
    weight_graph: float = 0.20
    weight_activity: float = 0.20
    weight_timeline: float = 0.15
    feedback_max_boost: float = 0.15
    # A format hint ("the PDF", "that spreadsheet") is a *stated constraint*,
    # not a soft preference: the user is telling you something they know. The
    # values below are deliberately asymmetric - see Decision 84. Swept and
    # measured in log.md, Phase 27; the table lives beside them in
    # contextfs.toml.
    format_boost: float = 1.15
    format_penalty: float = 0.70
    max_hops: int = 2
    max_seed_nodes: int = 10
    max_expanded_nodes: int = 400
    top_k: int = 10

    @model_validator(mode="after")
    def _check_weights(self) -> RetrievalConfig:
        total = (
            self.weight_semantic + self.weight_graph + self.weight_activity + self.weight_timeline
        )
        if abs(total - 1.0) > _WEIGHT_TOLERANCE:
            raise ConfigError(f"[retrieval] ranking weights must sum to 1.0, got {total:.6f}")
        return self

    @property
    def weights(self) -> dict[str, float]:
        """Ranking weights keyed by signal name."""
        return {
            "semantic": self.weight_semantic,
            "graph": self.weight_graph,
            "activity": self.weight_activity,
            "timeline": self.weight_timeline,
        }

    def normalised(self, enabled: set[str]) -> dict[str, float]:
        """Re-normalise weights over a subset of enabled signals.

        Used by the ablation harness (Phase 22): switching a layer off must
        redistribute its weight rather than shrink every score toward zero,
        otherwise ablation rows are not comparable.

        Args:
            enabled: Signal names to keep, e.g. ``{"semantic", "graph"}``.

        Returns:
            Weights over ``enabled`` summing to 1.0. Falls back to
            all-semantic if nothing is enabled.
        """
        kept = {k: v for k, v in self.weights.items() if k in enabled}
        total = sum(kept.values())
        if total <= 0:
            return {"semantic": 1.0}
        return {k: v / total for k, v in kept.items()}


class EvalConfig(_Section):
    """Evaluation harness inputs and outputs (Phases 21-22)."""

    ground_truth: Path = Path("data/synthetic/ground_truth.json")
    results_dir: Path = Path("data/eval")
    k_values: list[int] = Field(default_factory=lambda: [1, 3, 5, 10])


class ContextFSConfig(BaseModel):
    """The fully resolved configuration for one ContextFS index."""

    model_config = ConfigDict(extra="forbid")

    general: GeneralConfig = Field(default_factory=GeneralConfig)
    paths: PathsConfig = Field(default_factory=PathsConfig)
    scan: ScanConfig = Field(default_factory=ScanConfig)
    extraction: ExtractionConfig = Field(default_factory=ExtractionConfig)
    entities: EntitiesConfig = Field(default_factory=EntitiesConfig)
    embeddings: EmbeddingsConfig = Field(default_factory=EmbeddingsConfig)
    summarization: SummarizationConfig = Field(default_factory=SummarizationConfig)
    graph: GraphConfig = Field(default_factory=GraphConfig)
    temporal: TemporalConfig = Field(default_factory=TemporalConfig)
    activity: ActivityConfig = Field(default_factory=ActivityConfig)
    retrieval: RetrievalConfig = Field(default_factory=RetrievalConfig)
    eval: EvalConfig = Field(default_factory=EvalConfig)

    #: Absolute path of the config file this was loaded from (None if defaults).
    source_file: Path | None = None
    #: Directory that relative paths were resolved against.
    base_dir: Path = Field(default_factory=Path.cwd)

    # -- derived absolute paths --------------------------------------------

    @property
    def db_path(self) -> Path:
        """Absolute path to the SQLite metadata store."""
        return _under(self.paths.data_dir, self.paths.sqlite_path)

    @property
    def vector_dir(self) -> Path:
        """Absolute path to the LanceDB vector store directory."""
        return _under(self.paths.data_dir, self.paths.vector_path)

    @property
    def graph_file(self) -> Path:
        """Absolute path to the serialised relationship graph."""
        return _under(self.paths.data_dir, self.paths.graph_path)

    def ensure_data_dir(self) -> Path:
        """Create the derived-data directory if absent and return it.

        This is the *only* directory ContextFS ever creates outside of
        explicitly requested output paths.
        """
        self.paths.data_dir.mkdir(parents=True, exist_ok=True)
        return self.paths.data_dir

    def describe(self) -> dict[str, Any]:
        """Return a flat, printable summary of the active configuration."""
        return {
            "profile": self.general.profile_name,
            "config_file": str(self.source_file) if self.source_file else "<defaults>",
            "scan_root": str(self.paths.root),
            "root_exists": self.paths.root.is_dir(),
            "data_dir": str(self.paths.data_dir),
            "sqlite": str(self.db_path),
            "vectors": str(self.vector_dir),
            "graph": str(self.graph_file),
            "embed_model": self.embeddings.model,
            "embed_dim": self.embeddings.dimension,
            "device": self.embeddings.device,
            "spacy_model": self.entities.spacy_model,
            "summarizer": (
                f"{self.summarization.backend}:{self.summarization.model}"
                if self.summarization.enabled
                else "disabled (extractive fallback)"
            ),
            "timeline_threshold": self.temporal.timeline_node_threshold,
            "retrieval_weights": self.retrieval.weights,
        }


def _under(parent: Path, child: Path) -> Path:
    """Resolve ``child`` relative to ``parent`` unless it is already absolute."""
    return child if child.is_absolute() else (parent / child)


def find_config_file(start: Path | None = None) -> Path | None:
    """Search upward from ``start`` for a ContextFS config file.

    Walking upward means ``contextfs query ...`` works from any subdirectory of
    a project, the way ``git`` does, instead of only from the project root.

    Args:
        start: Directory to begin the search from. Defaults to the cwd.

    Returns:
        The first matching config path, or ``None`` if none was found.
    """
    current = (start or Path.cwd()).resolve()
    for directory in (current, *current.parents):
        for name in DEFAULT_CONFIG_NAMES:
            candidate = directory / name
            if candidate.is_file():
                return candidate
    return None


def _apply_env_overrides(raw: dict[str, Any]) -> list[str]:
    """Apply ``CONTEXTFS_*`` environment overrides in place.

    Returns:
        Human-readable descriptions of the overrides that were applied.
    """
    applied: list[str] = []
    for env_name, (section, key) in ENV_OVERRIDES.items():
        value = os.environ.get(env_name)
        if value:
            raw.setdefault(section, {})[key] = value
            applied.append(f"{env_name} -> [{section}].{key}")
    return applied


def load_config(
    config_path: Path | None = None,
    *,
    root: Path | None = None,
    data_dir: Path | None = None,
    overrides: dict[str, dict[str, Any]] | None = None,
) -> ContextFSConfig:
    """Load, merge, validate, and path-resolve the ContextFS configuration.

    Args:
        config_path: Explicit config file. If ``None``, searches upward from
            the current directory for ``contextfs.local.toml`` then
            ``contextfs.toml``. If nothing is found, built-in defaults are used.
        root: CLI override for the scan root (highest precedence).
        data_dir: CLI override for the derived-data directory.
        overrides: Nested ``{section: {key: value}}`` overrides applied after
            the file but before ``root``/``data_dir``. Used by the evaluation
            harness to sweep weights without writing temporary files.

    Returns:
        A validated config with every path resolved to an absolute location.

    Raises:
        ConfigError: If the file is missing, malformed, contains unknown keys,
            or fails a consistency check (e.g. weights not summing to 1.0).
    """
    if config_path is not None:
        config_path = Path(config_path).expanduser()
        if not config_path.is_file():
            raise ConfigError(f"Config file not found: {config_path}")
    else:
        config_path = find_config_file()

    raw: dict[str, Any] = {}
    if config_path is not None:
        try:
            with open(config_path, "rb") as fh:
                raw = tomllib.load(fh)
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"Malformed TOML in {config_path}: {exc}") from exc

    _apply_env_overrides(raw)

    for section, values in (overrides or {}).items():
        raw.setdefault(section, {}).update(values)

    if root is not None:
        raw.setdefault("paths", {})["root"] = str(root)
    if data_dir is not None:
        raw.setdefault("paths", {})["data_dir"] = str(data_dir)

    # Relative paths resolve against the config file's directory, so the CLI
    # behaves identically no matter which directory it is invoked from.
    base_dir = config_path.parent.resolve() if config_path else Path.cwd().resolve()
    raw["source_file"] = str(config_path) if config_path else None
    raw["base_dir"] = str(base_dir)

    try:
        cfg = ContextFSConfig.model_validate(raw)
    except ConfigError:
        raise
    except Exception as exc:  # pydantic ValidationError and friends
        raise ConfigError(f"Invalid configuration in {config_path or '<defaults>'}: {exc}") from exc

    _resolve_paths(cfg, base_dir)
    return cfg


def _resolve_paths(cfg: ContextFSConfig, base_dir: Path) -> None:
    """Rewrite every relative path in ``cfg`` to an absolute path under ``base_dir``."""
    cfg.paths.root = _abs(cfg.paths.root, base_dir)
    cfg.paths.data_dir = _abs(cfg.paths.data_dir, base_dir)
    cfg.eval.ground_truth = _abs(cfg.eval.ground_truth, base_dir)
    cfg.eval.results_dir = _abs(cfg.eval.results_dir, base_dir)
    cfg.base_dir = base_dir


def _abs(path: Path, base_dir: Path) -> Path:
    """Expand ``~`` and resolve ``path`` against ``base_dir`` if relative."""
    expanded = Path(path).expanduser()
    return expanded if expanded.is_absolute() else (base_dir / expanded).resolve()
