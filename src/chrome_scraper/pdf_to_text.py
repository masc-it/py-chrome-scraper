"""PDF detection and text extraction for browser-backed scrapers."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import urllib.parse
from pathlib import Path

from chrome_scraper.web_scrapers.base import BrowserTool, WebScraperError


def is_pdf_page(browser: BrowserTool, tab_ref: str, *, url: str, timeout: float) -> bool:
    """Detect direct PDF responses and Chrome's built-in PDF viewer."""
    if urllib.parse.urlparse(url).path.lower().endswith(".pdf"):
        return True
    try:
        result = browser.eval_js(
            tab_ref=tab_ref,
            expression="""
(() => document.contentType === 'application/pdf'
  || !!document.querySelector('embed[type="application/pdf"]'))()
""".strip(),
            timeout=timeout,
        )
    except WebScraperError:
        return False
    return result is True


def pdf_bytes_to_text(data: bytes) -> str:
    """Extract UTF-8 text from PDF bytes with Poppler's pdftotext."""
    if not data.startswith(b"%PDF-"):
        raise WebScraperError("Downloaded document is not a PDF")
    executable = shutil.which("pdftotext")
    if executable is None:
        raise WebScraperError(
            "pdftotext is required for PDF pages; install Poppler first"
        )
    with tempfile.TemporaryDirectory(prefix="chrome-scraper-pdf-") as tmp:
        pdf_path = Path(tmp) / "document.pdf"
        text_path = Path(tmp) / "document.txt"
        pdf_path.write_bytes(data)
        try:
            result = subprocess.run(
                [executable, "-enc", "UTF-8", str(pdf_path), str(text_path)],
                check=False,
                capture_output=True,
                text=True,
            )
        except OSError as exc:
            raise WebScraperError(f"Could not run pdftotext: {exc}") from exc
        if result.returncode != 0:
            detail = result.stderr.strip() or f"exit status {result.returncode}"
            raise WebScraperError(f"pdftotext failed: {detail}")
        return text_path.read_text(encoding="utf-8").strip() + "\n"
