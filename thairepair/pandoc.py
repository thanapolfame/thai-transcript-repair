"""Find pandoc, or fetch it — the converter's one external requirement.

Converting between Markdown and Word is pandoc's job, and pandoc is a binary
rather than a Python package.  Shipping it through pip was the obvious route and
does not work: ``pypandoc-binary``'s ``macosx_11_0_arm64`` wheel contains an
x86_64 executable, so on an Apple Silicon machine without Rosetta it cannot run
at all.  Every pandoc release, meanwhile, publishes a plain ``.zip`` or
``.tar.gz`` for each platform — no installer, no administrator rights, just an
executable inside an archive.

So this module does the small amount of work that buys back the certainty: it
looks for a pandoc that actually runs, and if there is none it downloads the
archive built for *this* machine and unpacks the one file it needs.  The
download is never automatic — :func:`install` is called from the page's button
or the CLI's ``--install-pandoc``, because fetching 40 MB is the user's decision
to make.

Nothing here is imported by the repair path.  A machine that only ever repairs
transcripts never touches pandoc and never notices it is missing.
"""

import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request
import zipfile
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path

#: Where a downloaded pandoc lives.  Under the home directory rather than in the
#: project, so it survives a re-clone and is never mistaken for source.
INSTALL_DIR = Path.home() / ".thairepair" / "pandoc"

#: The release list.  Asking for the newest by tag avoids pinning a version that
#: goes stale in a file nobody remembers to edit.
RELEASES_URL = "https://api.github.com/repos/jgm/pandoc/releases/latest"

#: How long any single network call may take, in seconds.
TIMEOUT = 60

#: A sanity bound on the archive: releases run 25-40 MB, and anything far past
#: that means the URL is not what we think it is.
MAX_ARCHIVE = 200 * 1024 * 1024

#: ``sys.platform`` and the machine architecture, mapped onto the suffix of the
#: release asset built for it.  Anything absent here has no published archive,
#: and the user is told to install pandoc themselves rather than being handed a
#: binary for the wrong machine.
ASSET_SUFFIXES = {
    ("darwin", "arm64"): "arm64-macOS.zip",
    ("darwin", "x86_64"): "x86_64-macOS.zip",
    ("win32", "x86_64"): "windows-x86_64.zip",
    ("linux", "x86_64"): "linux-amd64.tar.gz",
    ("linux", "arm64"): "linux-arm64.tar.gz",
}

#: What the executable is called inside the archive, and on disk afterwards.
EXECUTABLE = "pandoc.exe" if sys.platform == "win32" else "pandoc"


def _architecture() -> str:
    """The machine architecture, under the names the release assets use."""
    machine = platform.machine().lower()
    if machine in ("arm64", "aarch64"):
        return "arm64"
    if machine in ("x86_64", "amd64"):
        return "x86_64"
    return machine


def asset_suffix() -> str | None:
    """The release asset this machine needs, or ``None`` if none is published."""
    return ASSET_SUFFIXES.get((sys.platform, _architecture()))


def _runs(path: Path) -> bool:
    """Whether ``path`` is a pandoc that this machine can actually execute.

    Existence is not the test.  The broken wheel this module exists to route
    around put a perfectly present file on disk that raises ``OSError`` the
    moment it is executed, so the only trustworthy check is to run it.
    """
    try:
        result = subprocess.run(
            [str(path), "--version"],
            capture_output=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0 and result.stdout.startswith(b"pandoc")


def find(refresh: bool = False) -> Path | None:
    """The pandoc to use, or ``None``.

    A pandoc the user installed themselves wins over the downloaded copy: if
    someone has run ``brew install pandoc``, that is the one they maintain.
    """
    if refresh:
        _cached_find.cache_clear()
    return _cached_find()


@lru_cache(maxsize=1)
def _cached_find() -> Path | None:
    """The search itself.  Cached — it spawns processes, and the page asks often."""
    found = shutil.which(EXECUTABLE)
    if found and _runs(Path(found)):
        return Path(found)
    downloaded = INSTALL_DIR / EXECUTABLE
    if downloaded.exists() and _runs(downloaded):
        return downloaded
    return None


def available() -> bool:
    """Whether conversion can run at all."""
    return find() is not None


def version() -> str | None:
    """The version string of the pandoc in use, e.g. ``"3.10.1"``."""
    path = find()
    if path is None:
        return None
    result = subprocess.run(
        [str(path), "--version"], capture_output=True, timeout=30, check=False
    )
    match = re.search(rb"pandoc(?:\.exe)?\s+([0-9][0-9.]*)", result.stdout)
    return match.group(1).decode() if match else None


def _release_asset(suffix: str) -> tuple[str, str]:
    """The download URL and version tag of the asset ending in ``suffix``."""
    try:
        with urllib.request.urlopen(RELEASES_URL, timeout=TIMEOUT) as response:
            payload = json.loads(response.read())
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise ValueError(
            "ดาวน์โหลดไม่สำเร็จ ตรวจสอบการเชื่อมต่ออินเทอร์เน็ต "
            "(could not reach the pandoc release list)"
        ) from exc

    # The payload is untyped JSON; pin each field on the way out of it.
    tag: str = str(payload.get("tag_name", ""))
    for entry in payload.get("assets", []):
        name: str = str(entry.get("name", ""))
        if name.endswith(suffix):
            return str(entry.get("browser_download_url", "")), tag
    raise ValueError(
        f"ไม่พบไฟล์ติดตั้งสำหรับเครื่องนี้ (no pandoc release asset ending in {suffix})"
    )


def _download(url: str, target: Path, progress: Callable[[str], None]) -> None:
    """Fetch ``url`` to ``target``, reporting roughly every megabyte."""
    with urllib.request.urlopen(url, timeout=TIMEOUT) as response:
        total = int(response.headers.get("Content-Length") or 0)
        if total > MAX_ARCHIVE:
            raise ValueError("ไฟล์ติดตั้งใหญ่ผิดปกติ (release archive is implausibly large)")
        read = 0
        with target.open("wb") as handle:
            while chunk := response.read(1024 * 1024):
                read += len(chunk)
                if read > MAX_ARCHIVE:
                    raise ValueError(
                        "ไฟล์ติดตั้งใหญ่ผิดปกติ (release archive is implausibly large)"
                    )
                handle.write(chunk)
                if total:
                    progress(f"{read * 100 // total}%")


def _extract(archive: Path, into: Path) -> Path:
    """Pull the pandoc executable out of the release archive.

    Only the executable is taken.  The archives also carry manuals and data
    files that pandoc happily runs without, and unpacking a whole archive by
    name is how an archive entry escapes the directory it was meant to land in.
    """
    destination = into / EXECUTABLE

    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as bundle:
            member = _member(bundle.namelist())
            with bundle.open(member) as source, destination.open("wb") as target:
                shutil.copyfileobj(source, target)
    else:
        with tarfile.open(archive, "r:gz") as bundle:
            member = _member(bundle.getnames())
            source_file = bundle.extractfile(member)
            if source_file is None:
                raise ValueError("ไฟล์ติดตั้งเสียหาย (release archive is malformed)")
            with source_file, destination.open("wb") as target:
                shutil.copyfileobj(source_file, target)

    destination.chmod(destination.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP)
    return destination


def _member(names: list[str]) -> str:
    """The archive entry that is the executable, at ``bin/pandoc`` in every release."""
    for name in names:
        if name.endswith(f"bin/{EXECUTABLE}") or name.endswith(f"bin\\{EXECUTABLE}"):
            return name
    raise ValueError(f"ไม่พบ {EXECUTABLE} ในไฟล์ติดตั้ง (no {EXECUTABLE} inside the archive)")


def install(progress: Callable[[str], None] | None = None) -> Path:
    """Download pandoc for this machine and return the path to it.

    The archive is unpacked in a temporary directory and the executable moved
    into place only once it has been shown to run, so an interrupted download
    leaves no half-installed binary behind for :func:`find` to trip over.
    """
    report = progress if progress is not None else (lambda message: None)

    suffix = asset_suffix()
    if suffix is None:
        raise ValueError(
            "เครื่องนี้ไม่มีไฟล์ติดตั้งสำเร็จรูป ต้องติดตั้ง pandoc เอง จาก https://pandoc.org/installing.html "
            "(no prebuilt pandoc for this platform — install it yourself)"
        )

    report("กำลังค้นหาเวอร์ชันล่าสุด (finding the latest release)…")
    url, tag = _release_asset(suffix)

    INSTALL_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as workspace:
        staging = Path(workspace)
        archive = staging / suffix
        report(f"กำลังดาวน์โหลด pandoc {tag} (downloading)…")
        _download(url, archive, report)

        report("กำลังแตกไฟล์ (unpacking)…")
        unpacked = _extract(archive, staging)
        if not _runs(unpacked):
            raise ValueError(
                "ไฟล์ที่ดาวน์โหลดมาใช้งานไม่ได้บนเครื่องนี้ "
                "(the downloaded pandoc does not run on this machine)"
            )
        # Replace rather than write in place: os.replace is atomic, so a second
        # copy of the GUI installing at the same moment cannot produce a torn
        # executable.
        destination = INSTALL_DIR / EXECUTABLE
        os.replace(unpacked, destination)

    _cached_find.cache_clear()
    report(f"ติดตั้ง pandoc {tag} เรียบร้อย (installed)")
    return destination
