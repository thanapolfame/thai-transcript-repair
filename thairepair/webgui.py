"""A local browser front end: drop in a transcript, get the repaired files back.

Started by ``gui.py``.  It binds to the loopback interface only — the transcript
is read, repaired and handed back inside the one machine, and nothing is written
to disk except by the browser's own download.

The page is served from ``webgui.html``; ``POST /repair`` takes the raw file
bytes and answers with JSON holding the repaired text and the report CSV, which
the page then saves as two downloads.
"""

import json
import webbrowser
from collections import Counter
from collections.abc import Mapping
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .lexicon import default_lexicon
from .repair import Change, load_overrides, repair_text, report_csv
from .spellcheck import Misspelling, find_misspellings, spell_report_csv

PAGE = Path(__file__).with_name("webgui.html")
DEFAULT_WORDS = Path(__file__).parent.parent / "resource" / "word.csv"

#: Office machines produce Thai text in either of these.  UTF-8 is tried first
#: and is strict enough to reject a cp874 file, so the order does the detecting.
ENCODINGS = ("utf-8-sig", "cp874")

#: A transcript is a text file; anything this large is a mistake, and the whole
#: body is held in memory.
MAX_BYTES = 32 * 1024 * 1024


def decode(raw: bytes) -> tuple[str, str]:
    """Decode uploaded bytes, returning the text and the encoding that worked."""
    for encoding in ENCODINGS:
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    # cp874 maps almost every byte, so reaching here means the file is binary.
    raise ValueError("ไม่สามารถอ่านไฟล์นี้เป็นข้อความได้ (not a text file)")


def _flag(params: Mapping[str, list[str]], name: str, default: bool) -> bool:
    """Read one checkbox out of the query string.

    ``parse_qs`` hands back untyped lists, so the value is pinned to ``str``
    here and every caller downstream sees a plain ``bool``.
    """
    values: list[str] = params.get(name, [])
    if not values:
        return default
    value: str = values[0]
    return value not in ("0", "false", "")


def repair_upload(raw: bytes, params: Mapping[str, list[str]]) -> dict[str, Any]:
    """Repair one uploaded file and build the JSON payload for the page."""
    text, encoding = decode(raw)
    overrides: dict[str, str] = (
        load_overrides(DEFAULT_WORDS) if DEFAULT_WORDS.exists() else {}
    )

    fixed, changes = repair_text(
        text,
        overrides=overrides,
        aggressive=_flag(params, "aggressive", False),
        do_normalize=_flag(params, "normalize", True),
        do_join_words=_flag(params, "join_words", True),
        do_collapse_spaces=_flag(params, "collapse_spaces", True),
        do_replace=_flag(params, "replace", True),
        do_yamok=_flag(params, "yamok", True),
        do_space_numbers=_flag(params, "space_numbers", True),
    )

    counts: Counter[str] = Counter(
        change.rule if change.applied else "unresolved" for change in changes
    )

    # Off by default: on a long transcript it is the slowest thing here, and it
    # is a review aid rather than part of the repair.  ``None`` — not an empty
    # string — is what tells the page there is no third file to offer, so a run
    # that legitimately finds nothing still downloads a header-only CSV.
    spell: list[Misspelling] | None = (
        find_misspellings(fixed) if _flag(params, "spell_check", False) else None
    )

    return {
        "fixed": fixed,
        "report": report_csv(changes),
        "encoding": encoding,
        "lines": text.count("\n") + 1,
        "counts": [{"rule": rule, "n": n} for rule, n in counts.most_common()],
        "unresolved": _unresolved_preview(changes),
        "spell_report": None if spell is None else spell_report_csv(spell),
        "spell": [] if spell is None else _spell_preview(spell),
    }


def _unresolved_preview(changes: list[Change]) -> list[dict[str, Any]]:
    """The review queue, capped — the page shows it, it does not page through it."""
    return [
        {"line": change.line, "before": change.before}
        for change in changes
        if not change.applied
    ][:50]


def _spell_preview(found: list[Misspelling]) -> list[dict[str, Any]]:
    """The unknown words, capped the same way — the CSV is the complete list."""
    return [
        {
            "word": item.word,
            "count": item.count,
            "line": item.line,
            "context": item.context,
        }
        for item in found
    ][:50]


class Handler(BaseHTTPRequestHandler):
    """The whole app: one page, one endpoint."""

    server_version = "thairepair"

    def do_GET(self) -> None:
        if urlparse(self.path).path != "/":
            self._send(HTTPStatus.NOT_FOUND, b"not found", "text/plain")
            return
        self._send(HTTPStatus.OK, PAGE.read_bytes(), "text/html; charset=utf-8")

    def do_POST(self) -> None:
        url = urlparse(self.path)
        if url.path != "/repair":
            self._send(HTTPStatus.NOT_FOUND, b"not found", "text/plain")
            return

        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BYTES:
            self._error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "ไฟล์ใหญ่เกินไป (file too large)")
            return

        raw = self.rfile.read(length)
        try:
            payload = repair_upload(raw, parse_qs(url.query))
        except ValueError as exc:
            self._error(HTTPStatus.BAD_REQUEST, str(exc))
            return

        self._send_json(HTTPStatus.OK, payload)

    def _error(self, status: HTTPStatus, message: str) -> None:
        self._send_json(status, {"error": message})

    def _send_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _send(self, status: HTTPStatus, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        # The page is regenerated on every start; a cached copy would strand the
        # user on an old one after an update.
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        """Silence the per-request log; the console shows the URL and errors only."""


def serve(host: str = "127.0.0.1", port: int = 0, open_browser: bool = True) -> None:
    """Serve the page until interrupted.

    ``port=0`` takes whatever the OS has free, so two copies never collide.
    """
    # Loading the 62k-entry lexicon takes a moment.  Do it before the browser
    # opens, so the first repair is not the one that waits for it.
    print("thairepair: กำลังโหลดพจนานุกรม (loading lexicon)…")
    default_lexicon()

    with ThreadingHTTPServer((host, port), Handler) as httpd:
        bound: int = int(httpd.server_address[1])
        url = f"http://{host}:{bound}/"
        print(f"thairepair: เปิดหน้าเว็บที่ {url}")
        print("thairepair: กด Ctrl+C เพื่อปิด (press Ctrl+C to stop)")
        if open_browser:
            webbrowser.open(url)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nthairepair: ปิดแล้ว (stopped)")
