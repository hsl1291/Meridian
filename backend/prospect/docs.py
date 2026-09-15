"""Getting text out of a recorded declaration.

`cmd_extract` used to bail with "this is likely a SCANNED image PDF" below 200
characters of embedded text. The target set is buildings recorded roughly
1965-1990, and essentially all of those declarations are scanned images -- so the
extractor gave up on precisely the documents it exists to read.

Three things matter here beyond running OCR:

  * **Cache the text.** OCR on a 90-page declaration is minutes, and the reviewer
    will open the same document repeatedly. The cache sits beside the PDF.
  * **Record which path produced it.** OCR output is noisier, and
    `find_threshold` must not report high confidence on a page that was guessed
    at character by character.
  * **Degrade honestly.** Where no OCR engine is installed, say so and name the
    install, rather than reporting an empty document as one with no terms in it.
"""
from __future__ import annotations

import hashlib
import shutil
import subprocess
import tempfile
from pathlib import Path

# Enough text that the page almost certainly carries an embedded text layer. A
# scanned declaration usually yields a handful of stray characters from a stamp.
EMBEDDED_MIN_CHARS = 200


class OcrUnavailable(RuntimeError):
    """No OCR engine installed. Distinct from 'the document is empty', because
    the two call for completely different actions."""


def _cache_path(pdf: Path) -> Path:
    """Beside the PDF, keyed on content -- so replacing a file with a better scan
    under the same name does not silently serve the old text."""
    digest = hashlib.sha1(pdf.read_bytes()).hexdigest()[:16]
    return pdf.with_suffix(f".{digest}.txt")


def embedded_text(pdf: Path) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:                       # pragma: no cover
        raise RuntimeError("pypdf is not installed — pip install -r requirements.txt") from exc
    reader = PdfReader(str(pdf))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def ocr_available() -> bool:
    return bool(shutil.which("tesseract") and shutil.which("pdftoppm"))


def ocr_text(pdf: Path, dpi: int = 300, max_pages: int = 400) -> str:
    """Rasterise and OCR. Uses the command-line tools rather than a Python
    binding so there is one dependency to install and it is the same one the
    error message names."""
    if not ocr_available():
        raise OcrUnavailable(
            "No OCR engine found. Install Tesseract and Poppler, then re-run: "
            "the declarations this tool targets were recorded between about 1965 "
            "and 1990 and are scans, so without OCR the extractor cannot read "
            "the documents it exists for. "
            "Windows: the Tesseract installer plus Poppler on PATH. "
            "Debian/Ubuntu: apt install tesseract-ocr poppler-utils. "
            "macOS: brew install tesseract poppler.")
    out = []
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(
            ["pdftoppm", "-r", str(dpi), "-gray", "-png", str(pdf), str(Path(tmp) / "p")],
            check=True, capture_output=True, timeout=1800)
        pages = sorted(Path(tmp).glob("p-*.png"))[:max_pages]
        for page in pages:
            r = subprocess.run(["tesseract", str(page), "stdout", "--psm", "6"],
                               capture_output=True, text=True, timeout=300)
            out.append(r.stdout)
    return "\n".join(out)


def extract(pdf: Path, use_cache: bool = True, allow_ocr: bool = True) -> tuple[str, str]:
    """Return (text, source) where source is 'embedded', 'ocr' or 'cache:<source>'.

    The caller needs the source: OCR output is noisier and confidence must be
    discounted accordingly.
    """
    pdf = Path(pdf)
    cache = _cache_path(pdf)
    if use_cache and cache.exists():
        body = cache.read_text(encoding="utf-8", errors="replace")
        head, _, rest = body.partition("\n")
        if head.startswith("#source="):
            return rest, f"cache:{head.split('=', 1)[1]}"
        return body, "cache:unknown"

    text = embedded_text(pdf)
    source = "embedded"
    if len(text.strip()) < EMBEDDED_MIN_CHARS:
        if not allow_ocr:
            return text, "embedded"
        text = ocr_text(pdf)          # raises OcrUnavailable, which is the point
        source = "ocr"

    if use_cache:
        try:
            cache.write_text(f"#source={source}\n{text}", encoding="utf-8")
        except OSError:
            pass                      # a read-only drop folder is not a failure
    return text, source
