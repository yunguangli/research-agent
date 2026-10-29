"""Text extraction for corpus documents.

``pypdf`` and ``python-docx`` are optional (``pip install "tradingagents[docs]"``)
and imported lazily, so a core install still imports this module. When the extra
is missing the caller gets a typed error naming the fix, never a silent empty
string: a page with no text and a parser that is not installed are different
facts, and the report must say which one happened.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from pathlib import Path

from .manifest import CorpusError

_PAGE_MARKER = "\n\n--- page {number} ---\n\n"


@dataclass
class ExtractedText:
    text: str
    pages: int = 0
    empty_pages: int = 0
    warnings: list[str] = field(default_factory=list)

    @property
    def image_only(self) -> bool:
        """True when most pages carried no text layer, i.e. a scan needs OCR."""
        return self.pages > 0 and self.empty_pages >= max(1, self.pages // 2)


def _missing(extra: str, package: str) -> CorpusError:
    return CorpusError(
        f"Text extraction needs the optional dependency '{package}'. "
        f'Install it with: pip install "tradingagents[{extra}]"'
    )


def _read_text_file(path: Path) -> str:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise CorpusError(
            f"Cannot read {path.name}: {exc}. If it is open in another application, close it "
            "and retry; the rest of the corpus is still usable."
        ) from exc
    for encoding in ("utf-8-sig", "cp932", "cp1252"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _strip_html(text: str) -> str:
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", text)
    text = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>", "\n", text)
    text = re.sub(r"(?i)</t[dh]>", " | ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    return html.unescape(text)


def _extract_pdf(path: Path) -> ExtractedText:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - depends on the install extra
        raise _missing("docs", "pypdf") from exc

    try:
        reader = PdfReader(str(path))
    except Exception as exc:
        raise CorpusError(f"Cannot open PDF {path.name}: {exc}") from exc

    chunks: list[str] = []
    empty = 0
    for number, page in enumerate(reader.pages, start=1):
        try:
            page_text = page.extract_text() or ""
        except Exception as exc:
            page_text = ""
            chunks.append(_PAGE_MARKER.format(number=number))
            chunks.append(f"[extraction failed: {exc}]\n")
            empty += 1
            continue
        if not page_text.strip():
            empty += 1
        chunks.append(_PAGE_MARKER.format(number=number))
        chunks.append(page_text)

    result = ExtractedText("".join(chunks), pages=len(reader.pages), empty_pages=empty)
    if result.image_only:
        result.warnings.append(
            f"{empty}/{len(reader.pages)} pages have no text layer; OCR is required for those pages"
        )
    return result


def _extract_docx(path: Path) -> ExtractedText:
    try:
        import docx
    except ImportError as exc:  # pragma: no cover - depends on the install extra
        raise _missing("docs", "python-docx") from exc

    try:
        document = docx.Document(str(path))
    except OSError as exc:
        raise CorpusError(
            f"Cannot open DOCX {path.name}: {exc}. If it is open in Word, close it; the rest of "
            "the corpus is still usable."
        ) from exc
    except Exception as exc:
        raise CorpusError(f"Cannot open DOCX {path.name}: {exc}") from exc

    lines: list[str] = [paragraph.text for paragraph in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            cells = [cell.text.strip().replace("\n", " ") for cell in row.cells]
            if any(cells):
                lines.append(" | ".join(cells))
        lines.append("")

    text = "\n".join(lines)
    result = ExtractedText(text, pages=1 if text.strip() else 0, empty_pages=0 if text.strip() else 1)
    if not text.strip():
        result.warnings.append("DOCX produced no text")
    return result


def extract(path: str | Path) -> ExtractedText:
    """Extract text from one corpus file, keeping page anchors for citations."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return _extract_pdf(path)
    if suffix == ".docx":
        return _extract_docx(path)
    if suffix in {".md", ".txt"}:
        text = _read_text_file(path)
        return ExtractedText(text, pages=1, empty_pages=0 if text.strip() else 1)
    if suffix in {".html", ".htm"}:
        text = _strip_html(_read_text_file(path))
        return ExtractedText(text, pages=1, empty_pages=0 if text.strip() else 1)
    raise CorpusError(f"Unsupported corpus file type: {path.suffix} ({path.name})")


def clip(text: str, max_chars: int) -> str:
    """Trim to ``max_chars`` on a paragraph boundary and say that it was trimmed."""
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    cut = text.rfind("\n", 0, max_chars)
    if cut < max_chars // 2:
        cut = max_chars
    return text[:cut].rstrip() + "\n\n[truncated]"


_TRADITIONAL = set("們個這說時會來對開關與為國學經濟營運處發將產業務")
_SIMPLIFIED = set("们个这说时会来对开关与为国学经济营运处发将业务")


def detect_language(text: str) -> str:
    """Best-effort language of a document: ja, ko, zh-Hant, zh-Hans or en.

    Used to tell the reader which language a document is in, so a search uses
    words from that language rather than English keywords against Japanese text.
    """
    sample = text[:4000]
    if any("\uac00" <= char <= "\ud7af" for char in sample):
        return "ko"
    if any("\u3040" <= char <= "\u30ff" for char in sample):
        return "ja"
    cjk = [char for char in sample if "\u4e00" <= char <= "\u9fff"]
    if cjk:
        traditional = sum(char in _TRADITIONAL for char in cjk)
        simplified = sum(char in _SIMPLIFIED for char in cjk)
        return "zh-Hant" if traditional > simplified else "zh-Hans"
    return "en"
