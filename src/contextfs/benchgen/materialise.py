"""Write planned files to disk with internally consistent times (fixes skeptic N1).

For each file:

* **content** from the provider, written by the shared format writers;
* **in-file times** that agree with the simulation truth: Office ``core.xml`` created/modified and
  lastModifiedBy, PDF ``/CreationDate`` and ``/ModDate`` (with the local offset), EXIF
  ``DateTimeOriginal`` (floating, the way cameras write it), and zip entry times;
* **all three NTFS times** set with ``SetFileTime``:
  - created = when it arrived here;
  - modified = the last edit here, or the arrival time for downloads (browsers stamp download
    time), or the capture time for photos imported from a phone (the copy keeps mtime, which T
    measured for CopyFile-family copies);
* **Mark-of-the-Web** (``Zone.Identifier``) on browser downloads;
* a **sealed truth manifest** in ``<persona>/truth/``, which is outside the scanned corpus root.
"""

from __future__ import annotations

import json
import random
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from contextfs.benchgen.model import FilePlan, Persona
from contextfs.datagen.filetimes import set_file_times
from contextfs.datagen.writers import WRITERS

__all__ = ["materialise", "MANIFEST_NAME"]

MANIFEST_NAME = "truth_manifest.jsonl"
_OOXML = {"docx", "pptx", "xlsx"}


def _utc(local: datetime) -> datetime:
    return local.astimezone().astimezone(timezone.utc)


def _ooxml_iso(local: datetime) -> str:
    return _utc(local).strftime("%Y-%m-%dT%H:%M:%SZ")


def _pdf_date(local: datetime) -> str:
    aware = local.astimezone()
    off = aware.utcoffset()
    minutes = int(off.total_seconds() // 60) if off else 0
    sign = "+" if minutes >= 0 else "-"
    hh, mm = divmod(abs(minutes), 60)
    return aware.strftime("D:%Y%m%d%H%M%S") + f"{sign}{hh:02d}'{mm:02d}'"


def _patch_core_xml(path: Path, created: datetime, modified: datetime, author: str) -> None:
    """Rewrite docProps/core.xml in an OOXML package with the planned times and author."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    with zipfile.ZipFile(path) as src, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as dst:
        for item in src.infolist():
            data = src.read(item.filename)
            if item.filename == "docProps/core.xml":
                xml = data.decode("utf-8")
                for tag, value in (
                    ("created", _ooxml_iso(created)),
                    ("modified", _ooxml_iso(modified)),
                ):
                    pattern = rf"(<dcterms:{tag}[^>]*>)[^<]*(</dcterms:{tag}>)"
                    if re.search(pattern, xml):
                        xml = re.sub(pattern, rf"\g<1>{value}\g<2>", xml)
                    else:
                        xml = xml.replace(
                            "</cp:coreProperties>",
                            f'<dcterms:{tag} xsi:type="dcterms:W3CDTF">{value}</dcterms:{tag}></cp:coreProperties>',
                        )
                safe = author.replace("&", "&amp;").replace("<", "&lt;")
                for tag in ("dc:creator", "cp:lastModifiedBy"):
                    pattern = rf"(<{tag}[^>]*>)[^<]*(</{tag}>)"
                    if re.search(pattern, xml):
                        xml = re.sub(pattern, rf"\g<1>{safe}\g<2>", xml)
                    else:
                        xml = xml.replace(
                            "</cp:coreProperties>", f"<{tag}>{safe}</{tag}></cp:coreProperties>"
                        )
                data = xml.encode("utf-8")
            info = zipfile.ZipInfo(item.filename, date_time=modified.timetuple()[:6])
            info.compress_type = zipfile.ZIP_DEFLATED
            dst.writestr(info, data)
    tmp.replace(path)


def _patch_pdf(path: Path, created: datetime, modified: datetime, author: str) -> None:
    from pypdf import PdfReader, PdfWriter

    writer = PdfWriter(clone_from=PdfReader(str(path)))
    writer.add_metadata(
        {
            "/CreationDate": _pdf_date(created),
            "/ModDate": _pdf_date(modified),
            "/Author": author,
            "/Producer": "ContextFS benchgen",
        }
    )
    with open(path, "wb") as fh:
        writer.write(fh)


def _write_jpg(path: Path, shot: datetime, rng: random.Random) -> None:
    from PIL import Image

    im = Image.new("RGB", (64, 48), tuple(rng.randint(0, 255) for _ in range(3)))
    exif = Image.Exif()
    exif.get_ifd(0x8769)[36867] = shot.strftime("%Y:%m:%d %H:%M:%S")  # floating, like most phones
    exif[0x0110] = rng.choice(["Pixel 7", "Galaxy S23", "iPhone 13", "Redmi Note 12"])
    im.save(path, exif=exif, quality=70)


def _write_zip(path: Path, plan: FilePlan) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        info = zipfile.ZipInfo("README.txt", date_time=plan.truth.authored.timetuple()[:6])
        zf.writestr(info, f"{plan.title}\n")


def _motw(path: Path) -> None:
    with open(str(path) + ":Zone.Identifier", "w", encoding="utf-8") as fh:
        fh.write("[ZoneTransfer]\nZoneId=3\n")


def fs_times_for(plan: FilePlan) -> tuple[datetime, datetime]:
    """(created, modified) the file system should show before any damage."""
    t = plan.truth
    if plan.origin == "received":  # phone photos: the import copy keeps capture time as mtime
        return t.arrived_here, t.authored
    if plan.origin == "downloaded":
        return t.arrived_here, t.arrived_here
    return t.arrived_here, t.last_user_edit or t.arrived_here


def infile_times_for(plan: FilePlan) -> tuple[datetime, datetime]:
    """(created, modified) the file's own metadata should carry."""
    t = plan.truth
    if plan.origin == "authored":
        return t.authored, t.last_user_edit or t.authored
    return t.authored, t.authored


def materialise(persona: Persona, plans: list[FilePlan], provider, out_dir: Path) -> Path:
    """Write the corpus to ``out_dir/corpus`` and the sealed truth to ``out_dir/truth``."""
    corpus = out_dir / "corpus"
    truth_dir = out_dir / "truth"
    corpus.mkdir(parents=True, exist_ok=False)
    truth_dir.mkdir(parents=True, exist_ok=True)
    rng = random.Random(persona.seed * 7919 + 1)

    for plan in plans:
        path = corpus / plan.rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        if plan.kind == "jpg":
            _write_jpg(path, plan.truth.authored, rng)
        elif plan.kind == "zip":
            _write_zip(path, plan)
        else:
            plan.content = provider.payload(plan, persona, rng)
            WRITERS[plan.kind](path, plan.content)
            in_created, in_modified = infile_times_for(plan)
            if plan.kind in _OOXML:
                _patch_core_xml(path, in_created, in_modified, plan.infile_author or persona.name)
            elif plan.kind == "pdf":
                _patch_pdf(path, in_created, in_modified, plan.infile_author or persona.name)
        if plan.origin == "downloaded":
            _motw(path)
        created, modified = fs_times_for(plan)
        set_file_times(path, created=created, modified=modified, accessed=modified)

    # Directories get the earliest arrival inside them, so folder times are not "now".
    for d in sorted(
        {p.parent for p in corpus.rglob("*") if p.is_file()}, key=lambda x: -len(x.parts)
    ):
        stamps = [fs_times_for(pl)[0] for pl in plans if (corpus / pl.rel_path).parent == d]
        if stamps:
            set_file_times(d, created=min(stamps), modified=max(stamps), accessed=max(stamps))

    with open(truth_dir / MANIFEST_NAME, "w", encoding="utf-8") as fh:
        for plan in plans:
            fh.write(json.dumps(plan.truth_record(), default=str, sort_keys=True) + "\n")
    return corpus
