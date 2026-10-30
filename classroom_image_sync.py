"""Google Classroom -> local image sync.

Walks every post in a Classroom course (announcements, course-work materials
and assignments), collects the Drive files attached to them, and downloads the
images to a local folder.

A manifest of already-downloaded Drive file IDs makes repeat runs incremental,
so a weekly scheduled run only fetches what is new.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload

# classroom.courseworkmaterials.readonly is the scope name verified against the
# courses.courseWorkMaterials.list reference -- note it is all one lowercase
# word, not "courseWorkMaterials".
SCOPES = [
    "https://www.googleapis.com/auth/classroom.courses.readonly",
    "https://www.googleapis.com/auth/classroom.announcements.readonly",
    "https://www.googleapis.com/auth/classroom.courseworkmaterials.readonly",
    "https://www.googleapis.com/auth/classroom.coursework.me.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]

# Google does not always grant back the exact scope string it was asked for.
# classroom.coursework.me.readonly is returned as its legacy equivalent,
# classroom.student-submissions.me.readonly -- the same underlying permission
# under an older name. Two consequences, both handled here:
#
#   1. oauthlib raises on ANY difference between requested and granted scopes,
#      so it has to be told to relax or the token exchange fails outright.
#   2. A naive "are all requested scopes present?" check would fail forever
#      against the stored token, forcing a browser consent on every run and
#      quietly breaking unattended scheduling.
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")

SCOPE_ALIASES = {
    "https://www.googleapis.com/auth/classroom.coursework.me.readonly":
        "https://www.googleapis.com/auth/classroom.student-submissions.me.readonly",
}


def has_required_scopes(creds) -> bool:
    """True if every required scope was granted, allowing for Google's aliases."""
    granted = set(creds.scopes or [])
    return all(s in granted or SCOPE_ALIASES.get(s) in granted for s in SCOPES)

# The course ID identifies a real class, so it lives in a gitignored config file
# rather than in the source. Nothing in the public repo points at an actual course.
CONFIG_FILE = "config.json"
DEFAULT_OUT_DIR = "images"

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_AUTH_REQUIRED = 2
EXIT_LOCKED = 3

# How long to wait for another program holding out_dir_lock. Kept under the
# scheduled task's 2-hour limit so a long wait ends here, logged, rather than
# being killed by Task Scheduler.
LOCK_WAIT_MINUTES = 90
LOCK_RETRY_SECONDS = 30

log = logging.getLogger("classroom-sync")


# --------------------------------------------------------------------------
# configuration
# --------------------------------------------------------------------------

def resolve_course_id(args, root: Path) -> str:
    """Find the course ID from --course-id, the environment, or config.json."""
    if args.course_id:
        return args.course_id.strip()

    env = os.environ.get("CLASSROOM_COURSE_ID", "").strip()
    if env:
        return env

    path = root / CONFIG_FILE
    if path.exists():
        try:
            value = str(json.loads(path.read_text(encoding="utf-8")).get("course_id", "")).strip()
        except ValueError as exc:
            raise ValueError(f"{path} is not valid JSON: {exc}") from exc
        if value:
            return value

    raise ValueError(
        f"No course ID configured. Copy config.example.json to {CONFIG_FILE} and "
        "set course_id, or pass --course-id, or set CLASSROOM_COURSE_ID. "
        "The ID is the base64-decoded slug from the Classroom URL: for "
        "classroom.google.com/c/ABCdEF123 decode 'ABCdEF123'."
    )


# --------------------------------------------------------------------------
# auth
# --------------------------------------------------------------------------

def load_credentials(root: Path, allow_browser: bool) -> Credentials:
    """Return usable credentials, refreshing or re-consenting as needed."""
    secret_path = root / "client_secret.json"
    token_path = root / "state" / "token.json"
    creds = None

    if token_path.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
        except ValueError as exc:
            log.warning("Stored token unreadable (%s); re-authorising.", exc)
            creds = None

    # A token minted before SCOPES changed will refresh happily but 403 on the
    # new APIs, so treat a genuine scope shortfall as "no token". Uses the
    # alias-aware check: creds.has_scopes() would reject Google's substituted
    # scope names and re-prompt on every single run.
    if creds and not has_required_scopes(creds):
        log.warning("Stored token is missing required scopes; re-authorising.")
        creds = None

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            _save_token(token_path, creds)
            return creds
        except RefreshError as exc:
            log.warning("Refresh failed (%s).", exc)
            log.warning(
                "If the consent screen is still in Testing status this is "
                "expected: those refresh tokens die 7 days after consent."
            )
            creds = None

    if not allow_browser:
        raise PermissionError(
            "Interactive consent required but --no-browser was set. "
            f"Run this script manually once to refresh {token_path}."
        )

    if not secret_path.exists():
        raise FileNotFoundError(
            f"{secret_path} not found. Download the OAuth desktop-client JSON "
            "from the Google Cloud console and save it there (see README)."
        )

    flow = InstalledAppFlow.from_client_secrets_file(str(secret_path), SCOPES)
    creds = flow.run_local_server(port=0)
    _save_token(token_path, creds)
    return creds


def _save_token(token_path: Path, creds: Credentials) -> None:
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(creds.to_json(), encoding="utf-8")
    # Credentials are user-only; keep them out of any inherited group ACL.
    log.debug("Saved token to %s", token_path)


# --------------------------------------------------------------------------
# classroom traversal
# --------------------------------------------------------------------------

# (label, accessor, response field). The courseWorkMaterials list response
# names its array "courseWorkMaterial" -- singular -- unlike the other two.
POST_SOURCES = [
    ("announcement", lambda svc: svc.courses().announcements(), "announcements"),
    ("material", lambda svc: svc.courses().courseWorkMaterials(), "courseWorkMaterial"),
    ("assignment", lambda svc: svc.courses().courseWork(), "courseWork"),
]


def post_local_date(post) -> str:
    """Post date as YYYY-MM-DD in local time.

    creationTime is UTC, so slicing the string puts an evening post under the
    next day -- a 7pm post reads as tomorrow. Convert before formatting.
    """
    raw = post.get("creationTime") or ""
    if not raw:
        return "undated"
    try:
        return (datetime.fromisoformat(raw.replace("Z", "+00:00"))
                .astimezone().strftime("%Y-%m-%d"))
    except ValueError:
        return raw[:10] or "undated"


def resolve_out_dir(args, root: Path) -> Path:
    """Find the output folder from --out, the environment, or config.json.

    Same precedence as resolve_course_id. The default stays "images" beside the
    script so a fresh clone works with no config; a real install points this at
    a photo library instead. Relative values are taken as relative to the
    project folder, absolute ones are used as given.
    """
    value = args.out or os.environ.get("CLASSROOM_OUT_DIR", "").strip()

    if not value:
        path = root / CONFIG_FILE
        if path.exists():
            try:
                value = str(json.loads(path.read_text(encoding="utf-8"))
                            .get("out_dir", "")).strip()
            except ValueError as exc:
                raise ValueError(f"{path} is not valid JSON: {exc}") from exc

    value = value or DEFAULT_OUT_DIR
    out = Path(value).expanduser()
    return out if out.is_absolute() else root / out


def resolve_lock_file(root: Path) -> Path | None:
    """Find config.json out_dir_lock, or None when it is unset.

    Set it when another program also works in out_dir and guards that work
    with an exclusive lock file -- for example a backup that audits the folder
    and must not see a half-written download. Relative values are taken as
    relative to the project folder.
    """
    path = root / CONFIG_FILE
    if not path.exists():
        return None
    try:
        value = str(json.loads(path.read_text(encoding="utf-8"))
                    .get("out_dir_lock", "")).strip()
    except ValueError as exc:
        raise ValueError(f"{path} is not valid JSON: {exc}") from exc
    if not value:
        return None
    lock = Path(value).expanduser()
    return lock if lock.is_absolute() else root / lock


@contextlib.contextmanager
def exclusive_lock(path: Path, wait_minutes: int = LOCK_WAIT_MINUTES):
    """Hold path open with no sharing allowed for the duration of the block.

    This is the same lock as a .NET FileShare.None open: whichever process
    opens the file first owns it, and the other gets a sharing violation.
    Windows closes the handle if this process dies, so a crash cannot leave a
    stale lock behind. The file itself is left in place; deleting it could
    remove a lock another process has just taken.
    """
    if os.name != "nt":
        raise ValueError("out_dir_lock is only supported on Windows.")

    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                       ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    close = kernel.CloseHandle
    close.argtypes, close.restype = [wintypes.HANDLE], wintypes.BOOL
    invalid = wintypes.HANDLE(-1).value
    generic_read_write, share_none, open_always, normal = 0xC0000000, 0, 4, 0x80
    sharing_violation, lock_violation = 32, 33

    deadline = time.monotonic() + wait_minutes * 60
    waiting = False
    while True:
        handle = create(str(path), generic_read_write, share_none, None,
                        open_always, normal, None)
        if handle != invalid:
            break
        code = ctypes.get_last_error()
        if code not in (sharing_violation, lock_violation):
            raise OSError(f"Cannot open lock file {path}: {ctypes.FormatError(code)}")
        if time.monotonic() >= deadline:
            raise TimeoutError(f"{path} was still held by another program after "
                               f"{wait_minutes} minutes.")
        if not waiting:
            log.info("Another program holds %s; waiting up to %d minutes.", path, wait_minutes)
            waiting = True
        time.sleep(LOCK_RETRY_SECONDS)

    if waiting:
        log.info("Lock acquired; continuing.")
    try:
        yield
    finally:
        close(handle)


def _paged(collection, field: str, **kwargs):
    """Yield every item across all pages of a Classroom list endpoint."""
    page_token = None
    while True:
        response = collection.list(pageSize=100, pageToken=page_token, **kwargs).execute()
        yield from response.get(field, [])
        page_token = response.get("nextPageToken")
        if not page_token:
            return


def iter_attachments(classroom, course_id: str):
    """Yield (post_label, post_title, post_date, drive_file) for every Drive attachment."""
    for label, accessor, field in POST_SOURCES:
        try:
            posts = list(_paged(accessor(classroom), field, courseId=course_id))
        except HttpError as exc:
            # A missing scope or a role that cannot see this collection should
            # not sink the whole run -- the other two sources may still work.
            log.warning("Could not list %ss: %s", label, _http_reason(exc))
            continue

        log.info("Found %d %s(s).", len(posts), label)
        for post in posts:
            title = post.get("title") or post.get("text") or ""
            date = post_local_date(post)
            for material in post.get("materials", []):
                drive_file = material.get("driveFile", {}).get("driveFile")
                if drive_file and drive_file.get("id"):
                    yield label, title, date, drive_file


# --------------------------------------------------------------------------
# downloading
# --------------------------------------------------------------------------

_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def slugify(text: str, limit: int = 40) -> str:
    cleaned = _ILLEGAL.sub("", (text or "").strip())
    cleaned = re.sub(r"\s+", "-", cleaned).strip("-. ")
    return cleaned[:limit] or "untitled"


def build_filename(date: str, title: str, original: str) -> str:
    """Compose a sortable, Windows-safe filename capped well under MAX_PATH."""
    stem = Path(original).stem
    suffix = Path(original).suffix or ".bin"
    name = f"{date}_{slugify(title)}_{slugify(stem, 60)}"
    return name[:110] + suffix


def date_subdir(date: str) -> Path:
    """YYYY/MM folders from a YYYY-MM-DD post date.

    Matches the year/month layout the photo library already uses. post_local_date
    returns "undated" when a post has no creationTime, and a malformed date must
    not scatter files into folders named after a fragment of it -- both land in
    an "undated" folder instead.
    """
    parts = date.split("-")
    if len(parts) >= 2 and len(parts[0]) == 4 and parts[0].isdigit() and parts[1].isdigit():
        return Path(parts[0]) / f"{int(parts[1]):02d}"
    return Path("undated")


def download_drive_file(drive, file_id: str, dest: Path) -> int:
    """Stream a Drive file to disk, writing via .part so partials are never final."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    request = drive.files().get_media(fileId=file_id, supportsAllDrives=True)
    with tmp.open("wb") as handle:
        downloader = MediaIoBaseDownload(handle, request, chunksize=1024 * 1024)
        done = False
        while not done:
            _, done = downloader.next_chunk()
    tmp.replace(dest)
    return dest.stat().st_size


def _http_reason(exc: HttpError) -> str:
    status = getattr(exc.resp, "status", "?")
    try:
        detail = json.loads(exc.content).get("error", {}).get("message", "")
    except (ValueError, AttributeError):
        detail = ""
    return f"HTTP {status} {detail}".strip()


# --------------------------------------------------------------------------
# manifest
# --------------------------------------------------------------------------

def load_manifest(path: Path) -> dict:
    if not path.exists():
        return {"version": 1, "files": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        log.warning("Manifest at %s was corrupt; starting a fresh one.", path)
        return {"version": 1, "files": {}}
    data.setdefault("files", {})
    return data


def save_manifest(path: Path, manifest: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    manifest["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def sync(args, root: Path) -> int:
    try:
        course_id = resolve_course_id(args, root)
        out_dir = resolve_out_dir(args, root)
    except ValueError as exc:
        log.error("%s", exc)
        return EXIT_ERROR

    log.info("Saving to %s", out_dir)
    manifest_path = root / "state" / "manifest.json"
    manifest = load_manifest(manifest_path)
    seen = manifest["files"]

    try:
        creds = load_credentials(root, allow_browser=not args.no_browser)
    except PermissionError as exc:
        log.error("%s", exc)
        return EXIT_AUTH_REQUIRED
    except FileNotFoundError as exc:
        log.error("%s", exc)
        return EXIT_ERROR

    classroom = build("classroom", "v1", credentials=creds, cache_discovery=False)
    drive = build("drive", "v3", credentials=creds, cache_discovery=False)

    try:
        course = classroom.courses().get(id=course_id).execute()
        log.info("Course: %s (%s)", course.get("name", "?"), course_id)
    except HttpError as exc:
        log.error("Cannot read course %s: %s", course_id, _http_reason(exc))
        return EXIT_ERROR

    new_files = skipped = failed = 0
    # Names claimed during this run. Drive allows duplicate file names, and a
    # dry run writes nothing, so dest.exists() alone would let two files claim
    # the same target and misreport what a real run would produce.
    planned: set[str] = set()

    for label, title, date, drive_file in iter_attachments(classroom, course_id):
        file_id = drive_file["id"]

        if file_id in seen and (root / seen[file_id]["path"]).exists():
            skipped += 1
            continue

        try:
            meta = drive.files().get(
                fileId=file_id,
                fields="id,name,mimeType,size",
                supportsAllDrives=True,
            ).execute()
        except HttpError as exc:
            # This is the documented case where permission to read a post does
            # not extend to its materials. Leave it out of the manifest so a
            # later run retries if sharing is fixed.
            log.warning("Skipping %s (%s): %s", drive_file.get("title", file_id),
                        label, _http_reason(exc))
            failed += 1
            continue

        mime = meta.get("mimeType", "")
        if not args.all_files and not mime.startswith("image/"):
            log.debug("Not an image, skipping: %s (%s)", meta.get("name"), mime)
            continue

        filename = build_filename(date, title, meta.get("name", file_id))
        dest_dir = out_dir / date_subdir(date)
        dest = dest_dir / filename
        if dest.exists() or str(dest) in planned:
            dest = dest_dir / f"{dest.stem}_{file_id[:6]}{dest.suffix}"
        planned.add(str(dest))

        if args.dry_run:
            log.info("[dry-run] would download %s -> %s", meta.get("name"), dest.name)
            new_files += 1
            continue

        try:
            size = download_drive_file(drive, file_id, dest)
        except HttpError as exc:
            log.warning("Download failed for %s: %s", meta.get("name"), _http_reason(exc))
            failed += 1
            continue

        log.info("Downloaded %s (%s bytes)", dest.name, f"{size:,}")
        seen[file_id] = {
            "name": meta.get("name"),
            "path": str(dest.relative_to(root)) if dest.is_relative_to(root) else str(dest),
            "mime_type": mime,
            "post_type": label,
            "post_title": title[:200],
            "post_date": date,
            "downloaded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        new_files += 1

    if not args.dry_run:
        manifest["course_id"] = course_id
        save_manifest(manifest_path, manifest)

    log.info("Done. %d new, %d already had, %d unreadable.", new_files, skipped, failed)
    return EXIT_OK


def main(argv=None) -> int:
    root = Path(__file__).resolve().parent

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--course-id", default=None,
                        help="Numeric Classroom course ID. Overrides CLASSROOM_COURSE_ID "
                             "and config.json.")
    parser.add_argument("--out", default=None,
                        help="Output folder. Overrides CLASSROOM_OUT_DIR and "
                             "config.json out_dir. Files land in <out>/YYYY/MM.")
    parser.add_argument("--all-files", action="store_true",
                        help="Download every attachment, not just images")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report what would be downloaded without writing files")
    parser.add_argument("--no-browser", action="store_true",
                        help="Fail instead of opening a consent window (use for scheduled runs)")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    log_dir = root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_dir / "sync.log", encoding="utf-8"),
        ],
    )

    try:
        lock_file = None if args.dry_run else resolve_lock_file(root)
    except ValueError as exc:
        log.error("%s", exc)
        return EXIT_ERROR

    try:
        with exclusive_lock(lock_file) if lock_file else contextlib.nullcontext():
            return sync(args, root)
    except TimeoutError as exc:
        log.error("%s Nothing was downloaded; the next run will catch up.", exc)
        return EXIT_LOCKED
    except KeyboardInterrupt:
        log.error("Interrupted.")
        return EXIT_ERROR
    except Exception:
        log.exception("Unexpected failure")
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
