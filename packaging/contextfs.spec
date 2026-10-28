# PyInstaller spec for the portable one-dir ContextFS app (Decision 95).
# Build with:  .venv\Scripts\python.exe scripts\build_app.py
# Output:      dist\ContextFS\ContextFS.exe  (copy the whole folder; nothing is installed)
# ruff: noqa
from pathlib import Path

ROOT = Path(SPECPATH).parent
MODEL = ROOT / "models" / "onnx" / "minilm-l6-v2"

# The v2 app never uses the v1 stack (torch / sentence-transformers / spaCy / LanceDB): the engine
# embeds with ONNX Runtime. Lazy imports inside v1 modules would otherwise drag them in.
EXCLUDES = [
    "torch", "torchvision", "torchaudio", "sentence_transformers", "transformers", "spacy", "thinc",
    "lancedb", "pyarrow", "pandas", "scipy", "sklearn", "networkx", "matplotlib", "tkinter",
    "IPython", "jupyter", "notebook", "pytest", "reportlab", "huggingface_hub", "datasets",
    "onnxruntime.transformers", "onnxruntime.quantization", "onnxruntime.tools", "onnx",
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtQml", "PySide6.QtQuick",
    "PySide6.Qt3DCore", "PySide6.QtMultimedia", "PySide6.QtCharts", "PySide6.QtDataVisualization",
    "PySide6.QtPdf", "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtOpenGL", "PySide6.QtNetwork",
    "contextfs.cli", "contextfs.gui", "contextfs.benchgen", "contextfs.embed_st",
]

a = Analysis(
    [str(ROOT / "src" / "contextfs" / "app" / "__main__.py")],
    pathex=[str(ROOT / "src")],
    datas=[
        (str(MODEL / "model_fp32.onnx"), "models/minilm-l6-v2"),
        (str(MODEL / "tokenizer.json"), "models/minilm-l6-v2"),
        (str(MODEL / "tokenizer_config.json"), "models/minilm-l6-v2"),
    ],
    hiddenimports=["contextfs.extract.extractors", "contextfs.engine.sessions"],
    excludes=EXCLUDES,
    noarchive=False,
)
pyz = PYZ(a.pure)
# Two launchers over one set of binaries: ContextFS.exe (windowed; also runs the indexer child, so
# no console window flashes) and ContextFS-console.exe (for --selftest and support).
def _exe(name, console):
    return EXE(pyz, a.scripts, [], exclude_binaries=True, name=name, console=console,
               upx=False, icon=None, version=None)


gui_exe = _exe("ContextFS", console=False)
console_exe = _exe("ContextFS-console", console=True)
coll = COLLECT(gui_exe, console_exe, a.binaries, a.datas, strip=False, upx=False, name="ContextFS")
