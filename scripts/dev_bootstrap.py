r"""Developer bootstrap: check every prerequisite, install only what is missing.

Called by ``start.bat`` once the project-local Python and ``.venv`` exist (those two
steps must happen in batch, before any Python is available). Everything else lives
here because each check is a few lines of Python and would be fragile in batch -
the project path on the author's machine contains both a space and an ampersand.

Steps, each skipped when already satisfied:

1. PyTorch build - CUDA wheel if a usable NVIDIA GPU + driver is present, CPU otherwise.
2. The ``contextfs`` package with its extras (re-run only when ``pyproject.toml`` changes).
3. The spaCy model named in the config.
4. The embedding model weights named in the config (into ``models/hf``, loaded offline after).
5. The synthetic smoke corpus.

Every install location is inside the project (log.md Decision 89). On failure the
script exits non-zero with one line saying exactly what went wrong and what to do.

Usage:
    .venv\Scripts\python.exe scripts\dev_bootstrap.py [--cpu] [--check]
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import importlib.util
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
UV = PROJECT_ROOT / ".tools" / "uv" / "uv.exe"
PY = Path(sys.executable)
EXTRAS = "dev,datagen,gui"
DEPS_MARKER = PROJECT_ROOT / ".venv" / ".contextfs-deps"

#: Torch release and the CUDA wheel channels, newest first. A channel is used only if
#: the installed driver supports it (driver CUDA version >= channel version) and the
#: GPU's compute capability is in the wheel's arch list (sm_75+ for these channels).
TORCH_VERSION = "2.14.1"
CUDA_CHANNELS = (("cu130", (13, 0)), ("cu128", (12, 8)))
MIN_COMPUTE_CAPABILITY = (7, 5)

# Disk needed before a step starts, in GiB (generous: wheels unpack larger than they download).
DISK_NEEDED_GIB = {"torch_cuda": 8.0, "torch_cpu": 1.5, "package": 2.0, "models": 0.5}


class BootstrapError(RuntimeError):
    """A step failed; the message is shown to the user verbatim."""


def project_env() -> dict[str, str]:
    """Environment that keeps every cache inside the project (never on C:)."""
    env = dict(os.environ)
    env.setdefault("HF_HOME", str(PROJECT_ROOT / "models" / "hf"))
    env.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    # HF's Xet transfer backend stalled indefinitely on this machine (log Phase 29);
    # plain HTTPS downloads the same files reliably.
    env.setdefault("HF_HUB_DISABLE_XET", "1")
    env.setdefault("PIP_CACHE_DIR", str(PROJECT_ROOT / ".tools" / "cache" / "pip"))
    env.setdefault("UV_CACHE_DIR", str(PROJECT_ROOT / ".tools" / "cache" / "uv"))
    env.setdefault("UV_LINK_MODE", "copy")
    env.setdefault("PYTHONUTF8", "1")
    return env


def say(message: str) -> None:
    print(f"  [ SETUP ] {message}", flush=True)


def run(args: list[str], what: str) -> None:
    """Run a command; on failure raise with the last lines of its output."""
    proc = subprocess.run(
        [str(a) for a in args], cwd=PROJECT_ROOT, env=project_env(),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if proc.returncode != 0:
        tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-12:])
        hint = ""
        lowered = tail.lower()
        if "no space left" in lowered or "not enough space" in lowered or "errno 28" in lowered:
            hint = "\n  -> The disk is full. Free some space on the project drive and re-run."
        elif "microsoft visual c++" in lowered or "build tools" in lowered:
            hint = ("\n  -> A package needs compiling and no C++ toolchain is installed. Install"
                    " 'Desktop development with C++' from"
                    " https://visualstudio.microsoft.com/visual-cpp-build-tools/ and re-run.")
        elif any(s in lowered for s in ("connection", "timed out", "resolve", "network")):
            hint = "\n  -> Looks like a network problem. Check the internet connection and re-run."
        raise BootstrapError(f"{what} failed (exit {proc.returncode}):\n{tail}{hint}")


def require_network(step: str) -> None:
    for host in ("pypi.org", "download.pytorch.org", "huggingface.co"):
        try:
            socket.create_connection((host, 443), timeout=6).close()
        except OSError:
            raise BootstrapError(
                f"{step} needs to download files, but {host}:443 is unreachable. "
                "Connect to the internet (or fix the proxy/firewall) and re-run."
            ) from None


def require_disk(key: str) -> None:
    free = shutil.disk_usage(PROJECT_ROOT).free / 2**30
    if free < DISK_NEEDED_GIB[key]:
        raise BootstrapError(
            f"Only {free:.1f} GiB free on the project drive; this step needs about "
            f"{DISK_NEEDED_GIB[key]:.1f} GiB. Free some space and re-run."
        )


def installed_version(dist: str) -> str | None:
    try:
        return importlib.metadata.version(dist)
    except importlib.metadata.PackageNotFoundError:
        return None


# -- 1. torch -----------------------------------------------------------------------


def nvidia_gpu() -> tuple[str, tuple[int, int], tuple[int, int]] | None:
    """(name, compute capability, driver CUDA version) of the first NVIDIA GPU, or None."""
    smi = shutil.which("nvidia-smi")
    if not smi:
        return None
    try:
        query = subprocess.run(
            [smi, "--query-gpu=name,compute_cap", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=20,
        )
        header = subprocess.run([smi], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    if query.returncode != 0 or not query.stdout.strip():
        return None
    name, cap = (part.strip() for part in query.stdout.strip().splitlines()[0].split(",", 1))
    driver_cuda = (0, 0)
    for token in header.stdout.replace("|", " ").split("CUDA")[1:]:
        digits = token.replace("UMD", "").replace("Version:", "").split()
        if digits and digits[0].replace(".", "").isdigit():
            major, _, minor = digits[0].partition(".")
            driver_cuda = (int(major), int(minor or 0))
            break
    major, _, minor = cap.partition(".")
    return name, (int(major), int(minor or 0)), driver_cuda


def wanted_torch_channel(force_cpu: bool) -> tuple[str, str]:
    """Return (channel, reason)."""
    if force_cpu or os.environ.get("CONTEXTFS_TORCH", "").lower() == "cpu":
        return "cpu", "CPU build requested"
    gpu = nvidia_gpu()
    if gpu is None:
        return "cpu", "no NVIDIA GPU detected"
    name, capability, driver = gpu
    if capability < MIN_COMPUTE_CAPABILITY:
        return "cpu", f"{name} (sm_{capability[0]}{capability[1]}) is older than current CUDA wheels support"
    for channel, needs in CUDA_CHANNELS:
        if driver >= needs:
            return channel, f"{name}, sm_{capability[0]}{capability[1]}, driver CUDA {driver[0]}.{driver[1]}"
    return "cpu", f"{name} driver supports only CUDA {driver[0]}.{driver[1]}; update the NVIDIA driver for GPU support"


def ensure_torch(force_cpu: bool, check_only: bool) -> str:
    channel, reason = wanted_torch_channel(force_cpu)
    current = installed_version("torch")
    current_channel = current.partition("+")[2] if current else None
    if current and current_channel == channel:
        return f"torch {current} already installed ({reason})"
    if check_only:
        return f"NEEDED: torch +{channel} ({reason}); installed: {current or 'none'}"
    require_disk("torch_cpu" if channel == "cpu" else "torch_cuda")
    require_network("Installing PyTorch")
    say(f"installing torch {TORCH_VERSION}+{channel} - {reason} (several minutes, one time)")
    run([UV, "pip", "install", "--python", PY, f"torch=={TORCH_VERSION}",
         "--index-url", f"https://download.pytorch.org/whl/{channel}", "--reinstall-package", "torch"],
        "Installing PyTorch")
    return f"torch {installed_version('torch')} installed ({reason})"


# -- 2. package ----------------------------------------------------------------------


def deps_fingerprint() -> str:
    digest = hashlib.sha256((PROJECT_ROOT / "pyproject.toml").read_bytes())
    digest.update(EXTRAS.encode())
    return digest.hexdigest()


def ensure_package(check_only: bool) -> str:
    fingerprint = deps_fingerprint()
    if (
        DEPS_MARKER.is_file()
        and DEPS_MARKER.read_text(encoding="utf-8").strip() == fingerprint
        and installed_version("contextfs")
    ):
        return "contextfs and dependencies up to date"
    if check_only:
        return "NEEDED: contextfs package/dependencies (pyproject.toml changed or not installed)"
    require_disk("package")
    require_network("Installing dependencies")
    say(f"installing contextfs[{EXTRAS}] and its dependencies")
    run([UV, "pip", "install", "--python", PY, "-e", f".[{EXTRAS}]"], "Installing dependencies")
    DEPS_MARKER.write_text(fingerprint, encoding="utf-8")
    return "contextfs and dependencies installed"


# -- 3/4. models ---------------------------------------------------------------------


def configured_models() -> tuple[str, str]:
    sys.path.insert(0, str(PROJECT_ROOT / "src"))
    from contextfs.config import load_config

    cfg = load_config(PROJECT_ROOT / "contextfs.toml")
    return cfg.entities.spacy_model, cfg.embeddings.model


def ensure_spacy(model: str, check_only: bool) -> str:
    if importlib.util.find_spec(model) is not None:
        return f"spaCy model {model} present"
    if check_only:
        return f"NEEDED: spaCy model {model}"
    require_disk("models")
    require_network("Downloading the spaCy model")
    say(f"downloading spaCy model {model}")
    run([PY, "-m", "spacy", "download", model], f"Downloading spaCy model {model}")
    return f"spaCy model {model} installed"


def embedding_cached(model: str) -> bool:
    os.environ.update({k: v for k, v in project_env().items() if k.startswith("HF_")})
    from huggingface_hub import try_to_load_from_cache

    config = try_to_load_from_cache(model, "config.json")
    weights = [try_to_load_from_cache(model, name) for name in ("model.safetensors", "pytorch_model.bin")]
    return isinstance(config, str) and any(isinstance(w, str) for w in weights)


def ensure_embedding(model: str, check_only: bool) -> str:
    if embedding_cached(model):
        return f"embedding model {model} cached in models/hf"
    if check_only:
        return f"NEEDED: embedding model {model}"
    require_disk("models")
    require_network("Downloading the embedding model")
    say(f"downloading embedding model {model} into models/hf")
    run([PY, "-m", "contextfs", "fetch-models"], f"Downloading embedding model {model}")
    if not embedding_cached(model):
        raise BootstrapError(f"fetch-models finished but {model} is still not in models/hf")
    return f"embedding model {model} downloaded"


# -- 5. smoke corpus -----------------------------------------------------------------


def ensure_corpus(check_only: bool) -> str:
    corpus = PROJECT_ROOT / "tests" / "fixtures" / "smoke" / "corpus"
    if corpus.is_dir() and any(corpus.rglob("*.*")):
        return "smoke corpus present"
    if check_only:
        return "NEEDED: smoke corpus"
    say("generating the 40-file smoke corpus")
    run([PY, "scripts/generate_corpus.py"], "Generating the smoke corpus")
    return "smoke corpus generated"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cpu", action="store_true", help="install the CPU torch build even if a GPU exists")
    parser.add_argument("--check", action="store_true", help="report what is missing, install nothing")
    args = parser.parse_args()

    if not UV.is_file():
        print(f"  [ ERROR ] {UV} is missing - run start.bat, which downloads it.")
        return 1
    started = time.perf_counter()
    try:
        lines = [ensure_torch(args.cpu, args.check), ensure_package(args.check)]
        spacy_model, embed_model = configured_models()
        lines += [
            ensure_spacy(spacy_model, args.check),
            ensure_embedding(embed_model, args.check),
            ensure_corpus(args.check),
        ]
    except BootstrapError as exc:
        print(f"\n  [ ERROR ] {exc}\n", flush=True)
        return 1
    for line in lines:
        print(f"  [  OK   ] {line}")
    print(f"  prerequisites checked in {time.perf_counter() - started:.1f} s")
    return 2 if args.check and any(line.startswith("NEEDED") for line in lines) else 0


if __name__ == "__main__":
    raise SystemExit(main())
