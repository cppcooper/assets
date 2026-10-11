#!/usr/bin/env python3
"""Shared implementation for the repository asset download scripts."""

from __future__ import annotations

import difflib
import hashlib
import http.client
import http.cookiejar
import json
import os
import re
import shutil
import stat
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
import zlib
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = REPOSITORY_ROOT / ".download-manifest" / "manifest.json"
DEFAULT_CACHE = REPOSITORY_ROOT / ".download-manifest" / "packages"
USER_AGENT = "college-ports-assets/1.0"
DOWNLOAD_TIMEOUT_SECONDS = 90


class ManifestError(Exception):
    pass


class PackageError(Exception):
    pass


class SourceUnavailable(PackageError):
    pass


@dataclass(frozen=True)
class Package:
    name: str
    kind: str
    page: str
    download: str | None
    destinations: tuple[str, ...]


@dataclass
class RunReport:
    succeeded: list[str]
    unavailable: list[str]
    failed: list[str]
    conflicts: list[str]
    unshadowed: list[str]

    @classmethod
    def empty(cls) -> "RunReport":
        return cls(succeeded=[], unavailable=[], failed=[], conflicts=[], unshadowed=[])


def _safe_relative_path(value: Any, context: str) -> str:
    if not isinstance(value, str) or not value:
        raise ManifestError(f"{context} must be a non-empty relative path")
    normalized = value.replace("\\", "/")
    if "\x00" in normalized or normalized.startswith("/") or re.match(r"^[A-Za-z]:", normalized):
        raise ManifestError(f"{context} must be a relative path: {value!r}")
    parts = PurePosixPath(normalized).parts
    if not parts or any(part in ("", ".", "..") for part in parts):
        raise ManifestError(f"{context} contains an unsafe path component: {value!r}")
    return "/".join(parts)


def _is_excluded(path: str, exclusions: tuple[str, ...]) -> bool:
    return any(path == prefix or path.startswith(prefix + "/") for prefix in exclusions)


def load_manifest(path: Path = DEFAULT_MANIFEST) -> tuple[list[Package], tuple[str, ...]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ManifestError(f"cannot read manifest {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ManifestError(f"invalid JSON in {path}: {exc}") from exc

    if not isinstance(data, dict):
        raise ManifestError("manifest root must be a JSON object")

    raw_exclusions = data.get("exclude", [])
    if not isinstance(raw_exclusions, list):
        raise ManifestError("manifest 'exclude' must be an array")
    exclusions = tuple(
        _safe_relative_path(item, "excluded path").rstrip("/")
        for item in raw_exclusions
    )

    raw_sources = data.get("sources")
    if not isinstance(raw_sources, list):
        raise ManifestError("manifest 'sources' must be an array")

    packages: list[Package] = []
    seen_names: set[str] = set()
    for source_index, source in enumerate(raw_sources):
        if not isinstance(source, dict):
            raise ManifestError(f"sources[{source_index}] must be an object")
        page = source.get("page")
        if not isinstance(page, str) or not page:
            raise ManifestError(f"sources[{source_index}].page must be a non-empty string")
        raw_packages = source.get("packages")
        if not isinstance(raw_packages, list):
            raise ManifestError(f"sources[{source_index}].packages must be an array")

        for package_index, raw_package in enumerate(raw_packages):
            context = f"sources[{source_index}].packages[{package_index}]"
            if not isinstance(raw_package, dict):
                raise ManifestError(f"{context} must be an object")
            name = raw_package.get("name")
            if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9._-]+", name):
                raise ManifestError(f"{context}.name must use letters, digits, dot, underscore, or hyphen")
            if name in seen_names:
                raise ManifestError(f"duplicate package name: {name}")
            seen_names.add(name)

            kind = raw_package.get("type")
            if kind not in ("zip", "file"):
                raise ManifestError(f"{context}.type must be 'zip' or 'file'")

            download = raw_package.get("download")
            if download is not None:
                if not isinstance(download, str):
                    raise ManifestError(f"{context}.download must be a URL string")
                parsed = urllib.parse.urlparse(download)
                if parsed.scheme not in ("http", "https") or not parsed.netloc:
                    raise ManifestError(f"{context}.download must be an HTTP or HTTPS URL")

            raw_destinations = raw_package.get("to", [])
            if not isinstance(raw_destinations, list):
                raise ManifestError(f"{context}.to must be an array")
            destinations: list[str] = []
            for destination in raw_destinations:
                safe_destination = _safe_relative_path(destination, f"{context}.to entry")
                if _is_excluded(safe_destination, exclusions):
                    raise ManifestError(f"{context}.to points into an excluded path: {safe_destination}")
                destinations.append(safe_destination)

            packages.append(
                Package(
                    name=name,
                    kind=kind,
                    page=page,
                    download=download,
                    destinations=tuple(destinations),
                )
            )

    return packages, exclusions


def _file_suffix(package: Package) -> str:
    if package.kind == "zip":
        return ".zip"
    if package.download:
        suffix = Path(urllib.parse.urlparse(package.download).path).suffix.lower()
        if re.fullmatch(r"\.[a-z0-9]{1,10}", suffix):
            return suffix
    return ".bin"


def canonical_cache_path(package: Package, cache_dir: Path = DEFAULT_CACHE) -> Path:
    return cache_dir / f"{package.name}{_file_suffix(package)}"


def _words(value: str) -> list[str]:
    separated = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", value)
    return re.findall(r"[a-z]+|[0-9]+", separated.lower())


def _tokens_match(expected: str, actual: str) -> bool:
    if expected == actual:
        return True
    if len(expected) >= 5 and len(actual) >= 5:
        return difflib.SequenceMatcher(None, expected, actual).ratio() >= 0.9
    return False


def _legacy_name_matches(package: Package, path: Path) -> bool:
    expected = _words(package.name)
    actual = _words(path.stem)
    if not expected or not actual:
        return False
    cursor = 0
    expected_cursor = 0
    while expected_cursor < len(expected):
        match = next(
            (
                (index, count)
                for index in range(cursor, len(actual))
                for count in range(1, len(expected) - expected_cursor + 1)
                if _tokens_match("".join(expected[expected_cursor:expected_cursor + count]), actual[index])
            ),
            None,
        )
        if match is None:
            return False
        match_index, count = match
        cursor = match_index + 1
        expected_cursor += count
    return True


def find_cached_package(
    package: Package,
    cache_dir: Path = DEFAULT_CACHE,
    manifest_path: Path = DEFAULT_MANIFEST,
) -> tuple[Path | None, str | None]:
    canonical = canonical_cache_path(package, cache_dir)
    if canonical.is_file():
        return canonical, None

    if package.kind != "zip":
        return None, None

    legacy_root = manifest_path.parent
    candidates = [
        path
        for path in legacy_root.rglob("*.zip")
        if path.is_file()
        and not path.is_symlink()
        and path.resolve() != canonical.resolve()
        and _legacy_name_matches(package, path)
    ]
    if len(candidates) == 1:
        return candidates[0], "using a matching pre-manifest ZIP cache name"
    if len(candidates) > 1:
        listing = ", ".join(str(path.relative_to(legacy_root)) for path in candidates)
        return None, f"multiple local ZIPs match this package ({listing}); copy one to {canonical}"
    return None, None


def _zip_member_parts(name: str) -> tuple[str, ...]:
    normalized = name.replace("\\", "/")
    if "\x00" in normalized or normalized.startswith("/") or re.match(r"^[A-Za-z]:", normalized):
        raise PackageError(f"unsafe ZIP member path: {name!r}")
    parts = PurePosixPath(normalized).parts
    if not parts or any(part in ("", ".", "..") for part in parts):
        raise PackageError(f"unsafe ZIP member path: {name!r}")
    if any(":" in part for part in parts):
        raise PackageError(f"ZIP member contains a platform-specific path component: {name!r}")
    return parts


def _validate_zip(path: Path) -> None:
    try:
        with zipfile.ZipFile(path) as archive:
            names: set[str] = set()
            for info in archive.infolist():
                parts = _zip_member_parts(info.filename.rstrip("/"))
                if info.filename in names:
                    raise PackageError(f"duplicate ZIP member: {info.filename!r}")
                names.add(info.filename)
                mode = info.external_attr >> 16
                if stat.S_ISLNK(mode):
                    raise PackageError(f"ZIP contains a symbolic link: {info.filename!r}")
            damaged_member = archive.testzip()
            if damaged_member is not None:
                raise PackageError(f"ZIP CRC check failed for {damaged_member!r}")
    except (OSError, EOFError, NotImplementedError, RuntimeError, zlib.error, zipfile.BadZipFile) as exc:
        raise PackageError(f"invalid or unreadable ZIP {path}: {exc}") from exc


@dataclass
class _ItchUpload:
    name: str = ""
    upload_id: str | None = None


class _ItchPage(HTMLParser):
    """Read the CSRF token and filenames beside itch.io's download buttons."""

    def __init__(self, content: str) -> None:
        super().__init__()
        self.csrf = ""
        self.uploads: list[_ItchUpload] = []
        self._depth = 0
        self._upload_depth = 0
        self._upload: _ItchUpload | None = None
        self.feed(content)

    def handle_starttag(self, tag: str, attributes: list[tuple[str, str | None]]) -> None:
        attrs = dict(attributes)
        classes = (attrs.get("class") or "").split()
        if tag == "meta" and attrs.get("name") == "csrf_token":
            self.csrf = attrs.get("value") or attrs.get("content") or ""
        if tag == "div":
            self._depth += 1
            if "upload" in classes and self._upload is None:
                self._upload = _ItchUpload()
                self._upload_depth = self._depth
        if self._upload is not None:
            if "name" in classes and attrs.get("title"):
                self._upload.name = attrs["title"] or ""
            upload_id = attrs.get("data-upload_id")
            if upload_id and upload_id.isdecimal():
                self._upload.upload_id = upload_id

    def handle_endtag(self, tag: str) -> None:
        if tag == "div":
            if self._upload is not None and self._depth == self._upload_depth:
                if self._upload.name:
                    self.uploads.append(self._upload)
                self._upload = None
            self._depth -= 1


def _is_itch_page(page: str) -> bool:
    parsed = urllib.parse.urlparse(page)
    return parsed.scheme in ("http", "https") and (parsed.hostname or "").endswith(".itch.io")


def _open_url(request: Any, opener: Any = None) -> Any:
    open_url = opener.open if opener is not None else urllib.request.urlopen
    for attempt in range(3):
        try:
            return open_url(request, timeout=DOWNLOAD_TIMEOUT_SECONDS)
        except urllib.error.HTTPError as exc:
            if exc.code not in (429, 503) or attempt == 2:
                raise
            retry_after = exc.headers.get("Retry-After", "")
            delay = int(retry_after) if retry_after.isdecimal() else 2 * (attempt + 1)
            if delay > 60:
                raise
            exc.close()
            print(f"retrying: HTTP {exc.code}; waiting {delay} second(s)")
            time.sleep(delay)


class _ItchSource:
    """Resolve public downloads with the same requests as the site's buttons."""

    def __init__(self, page: str) -> None:
        self.page = page.rstrip("/")
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )
        self.opener.addheaders = [("User-Agent", USER_AGENT)]
        self.current_url = self.page
        self.current = self._read_page(self.page)
        self._opened_downloads = False

    def _read_page(self, url: str) -> _ItchPage:
        with _open_url(url, self.opener) as response:
            content = response.read().decode("utf-8")
        return _ItchPage(content)

    def _post(self, url: str) -> dict[str, Any]:
        if not self.current.csrf:
            raise SourceUnavailable("itch.io did not provide a download token; a browser session may be required")
        request = urllib.request.Request(
            url,
            data=urllib.parse.urlencode({"csrf_token": self.current.csrf}).encode("ascii"),
            headers={"Referer": self.current_url, "X-Requested-With": "XMLHttpRequest"},
        )
        with _open_url(request, self.opener) as response:
            try:
                result = json.load(response)
            except (ValueError, UnicodeError) as exc:
                raise PackageError("itch.io returned an invalid download response") from exc
        if not isinstance(result, dict):
            raise PackageError("itch.io returned an invalid download response")
        return result

    def _response_url(self, result: dict[str, Any]) -> str:
        value = result.get("url")
        if not isinstance(value, str) or not value:
            errors = result.get("errors")
            detail = "; ".join(str(error) for error in errors) if isinstance(errors, list) else "no public download link; purchase or browser interaction may be required"
            raise SourceUnavailable(f"itch.io: {detail}")
        url = urllib.parse.urljoin(self.page + "/", value)
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise PackageError("itch.io returned an invalid download URL")
        return url

    def resolve(self, package: Package) -> str:
        def matches(upload: _ItchUpload) -> bool:
            suffix = Path(upload.name).suffix.lower()
            if package.kind == "zip" and suffix != ".zip":
                return False
            if package.kind == "file":
                destination_suffixes = {Path(path).suffix.lower() for path in package.destinations}
                if len(destination_suffixes) == 1 and suffix not in destination_suffixes:
                    return False
                if suffix in (".zip", ".rar", ".7z", ".tar", ".gz"):
                    return False
            return _legacy_name_matches(package, Path(upload.name))

        matching = [upload for upload in self.current.uploads if upload.upload_id and matches(upload)]
        if not matching and not self._opened_downloads:
            self.current_url = self._response_url(self._post(self.page + "/download_url"))
            self.current = self._read_page(self.current_url)
            self._opened_downloads = True
            matching = [upload for upload in self.current.uploads if upload.upload_id and matches(upload)]
        if len(matching) != 1:
            available = ", ".join(upload.name for upload in self.current.uploads) or "none"
            reason = "multiple files match" if matching else "no public file matches"
            raise SourceUnavailable(f"{reason} {package.name}; files listed by itch.io: {available}")
        upload = matching[0]
        print(f"resolved: {package.name} — {upload.name}")
        return self._response_url(self._post(self.page + "/file/" + str(upload.upload_id)))


def _download_to_cache(
    package: Package, cache_path: Path, download_url: str | None = None, opener: Any = None
) -> None:
    url = download_url or package.download
    if not url:
        raise PackageError("no download URL was resolved")
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    part_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", prefix=f".{package.name}.", suffix=".part", dir=cache_path.parent, delete=False
        ) as temporary:
            part_path = Path(temporary.name)
            request = urllib.request.Request(
                url,
                headers={"User-Agent": USER_AGENT},
            )
            with _open_url(request, opener) as response:
                status = getattr(response, "status", 200)
                if status != 200:
                    raise PackageError(f"download returned HTTP status {status}")
                content_length = response.headers.get("Content-Length")
                expected_length = None
                if content_length is not None:
                    try:
                        expected_length = int(content_length)
                    except ValueError as exc:
                        raise PackageError(f"invalid download Content-Length: {content_length!r}") from exc
                shutil.copyfileobj(response, temporary, length=1024 * 1024)
                received_length = temporary.tell()
                if expected_length is not None and received_length != expected_length:
                    raise PackageError(
                        f"incomplete download: expected {expected_length} bytes, received {received_length}"
                    )

        if not part_path.is_file() or part_path.stat().st_size == 0:
            raise PackageError("download returned an empty file")
        if package.kind == "zip":
            _validate_zip(part_path)
        os.replace(part_path, cache_path)
        part_path = None
    except (
        OSError,
        EOFError,
        http.client.HTTPException,
        NotImplementedError,
        TimeoutError,
        urllib.error.URLError,
        zlib.error,
        zipfile.BadZipFile,
    ) as exc:
        raise PackageError(f"download failed: {exc}") from exc
    finally:
        if part_path is not None:
            part_path.unlink(missing_ok=True)


def download_all(
    packages: list[Package],
    cache_dir: Path = DEFAULT_CACHE,
    manifest_path: Path = DEFAULT_MANIFEST,
    force: bool = False,
    offline: bool = False,
) -> RunReport:
    report = RunReport.empty()
    itch_sources: dict[str, _ItchSource] = {}
    for package in packages:
        cache_path = canonical_cache_path(package, cache_dir)
        cached, cache_note = find_cached_package(package, cache_dir, manifest_path)
        supports_page = _is_itch_page(package.page)
        if cached is not None and (not force or not (package.download or supports_page)):
            note = f" ({cache_note})" if cache_note else ""
            print(f"cached: {package.name} — {cached}{note}")
            report.succeeded.append(package.name)
            continue
        if not package.download and not supports_page:
            reason = cache_note or "no cached package, direct download URL, or supported source-page resolver"
            print(f"unavailable: {package.name} — {reason}; source page: {package.page}")
            report.unavailable.append(package.name)
            continue
        if offline:
            print(f"unavailable: {package.name} — offline mode; source: {package.download or package.page}")
            report.unavailable.append(package.name)
            continue
        try:
            if package.download:
                _download_to_cache(package, cache_path)
            else:
                if package.page not in itch_sources:
                    itch_sources[package.page] = _ItchSource(package.page)
                source = itch_sources[package.page]
                _download_to_cache(package, cache_path, source.resolve(package), source.opener)
            print(f"downloaded: {package.name} — {cache_path}")
            report.succeeded.append(package.name)
        except SourceUnavailable as exc:
            print(f"unavailable: {package.name} — {exc}; source page: {package.page}")
            report.unavailable.append(package.name)
        except (PackageError, OSError, http.client.HTTPException, UnicodeError) as exc:
            print(f"failed: {package.name} — {exc}")
            report.failed.append(package.name)
    return report


def _repository_destination(root: Path, relative_path: str) -> Path:
    resolved_root = root.resolve()
    destination = (root / Path(*PurePosixPath(relative_path).parts)).resolve()
    try:
        destination.relative_to(resolved_root)
    except ValueError as exc:
        raise PackageError(f"destination escapes repository: {relative_path}") from exc
    return destination


def _atomic_write(destination: Path, write_content: Any) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", prefix=f".{destination.name}.", suffix=".part", dir=destination.parent, delete=False
        ) as temporary:
            temporary_path = Path(temporary.name)
            write_content(temporary)
        os.replace(temporary_path, destination)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _hash_stream(stream: Any) -> str:
    digest = hashlib.sha256()
    while chunk := stream.read(1024 * 1024):
        digest.update(chunk)
    return digest.hexdigest()


def _hash_file(path: Path) -> str:
    with path.open("rb") as stream:
        return _hash_stream(stream)


def _preview_output(
    package: Package,
    relative_output: str,
    source_digest: str,
    root: Path,
    planned_outputs: dict[str, tuple[str, str]],
    report: RunReport,
) -> None:
    destination = _repository_destination(root, relative_output)
    resolved_root = root.resolve()
    output_key = destination.relative_to(resolved_root).as_posix()
    previous = planned_outputs.get(output_key)
    if previous is not None:
        previous_package, previous_digest = previous
        if previous_digest != source_digest:
            conflict = (
                f"{relative_output}: {previous_package} and {package.name} "
                "would write different contents"
            )
            report.conflicts.append(conflict)
            print(f"CONFLICT: {conflict}")
        else:
            print(f"same planned content: {relative_output} from {previous_package} and {package.name}")
        return

    blockers: list[str] = []
    for ancestor in destination.parents:
        if ancestor == resolved_root.parent:
            break
        ancestor_key = ancestor.relative_to(resolved_root).as_posix()
        if ancestor_key in planned_outputs:
            previous_package, _ = planned_outputs[ancestor_key]
            blockers.append(
                f"{relative_output}: requires directory {ancestor_key}, "
                f"but {previous_package} would write a file there"
            )
        if ancestor.exists() and not ancestor.is_dir():
            blockers.append(
                f"{relative_output}: existing file {ancestor_key} blocks a required directory"
            )
    for planned_path, (previous_package, _) in planned_outputs.items():
        if planned_path.startswith(output_key + "/"):
            blockers.append(
                f"{relative_output}: {package.name} would write a file where "
                f"{previous_package} requires a directory for {planned_path}"
            )

    planned_outputs[output_key] = (package.name, source_digest)
    if blockers:
        for conflict in blockers:
            report.conflicts.append(conflict)
            print(f"CONFLICT: {conflict}")
        return
    if destination.exists():
        if destination.is_file() and _hash_file(destination) == source_digest:
            print(f"unchanged: {relative_output}")
            return
        if destination.is_dir():
            conflict = f"{relative_output}: existing directory blocks a file from {package.name}"
        else:
            conflict = f"{relative_output}: existing content would be overwritten by {package.name}"
        report.conflicts.append(conflict)
        print(f"CONFLICT: {conflict}")
        return
    print(f"would add: {relative_output}")


def _extract_zip(
    package: Package,
    archive_path: Path,
    root: Path,
    exclusions: tuple[str, ...],
    dry_run: bool,
    planned_outputs: dict[str, tuple[str, str]],
    report: RunReport,
) -> int:
    _validate_zip(archive_path)
    with zipfile.ZipFile(archive_path) as archive:
        files: list[tuple[zipfile.ZipInfo, tuple[str, ...]]] = []
        names_seen: set[str] = set()
        for info in archive.infolist():
            if info.is_dir():
                continue
            parts = _zip_member_parts(info.filename)
            if info.filename in names_seen:
                raise PackageError(f"duplicate ZIP member: {info.filename!r}")
            names_seen.add(info.filename)
            files.append((info, parts))

        if not files:
            raise PackageError("ZIP contains no files")

        first_parts = {parts[0] for _, parts in files}
        strip_wrapper = len(first_parts) == 1 and all(len(parts) > 1 for _, parts in files)
        targets = list(dict.fromkeys(package.destinations))
        if not targets:
            print(f"no destinations: {package.name} — download is retained in the package cache")
            return 0

        planned: list[tuple[zipfile.ZipInfo, str, str]] = []
        output_paths: set[tuple[str, str]] = set()
        excluded_count = 0
        for info, original_parts in files:
            parts = original_parts[1:] if strip_wrapper else original_parts
            if not parts:
                continue
            member_path = "/".join(parts)
            for target in targets:
                relative_output = f"{target}/{member_path}"
                if _is_excluded(relative_output, exclusions):
                    excluded_count += 1
                    continue
                key = (target, member_path)
                if key in output_paths:
                    raise PackageError(f"multiple ZIP members map to {relative_output}")
                output_paths.add(key)
                _repository_destination(root, relative_output)
                planned.append((info, target, member_path))

        for info, target, member_path in planned:
            relative_output = f"{target}/{member_path}"
            if dry_run:
                with archive.open(info, "r") as source:
                    source_digest = _hash_stream(source)
                _preview_output(
                    package,
                    relative_output,
                    source_digest,
                    root,
                    planned_outputs,
                    report,
                )
            else:
                destination = _repository_destination(root, relative_output)

                def write_member(stream: Any, member: zipfile.ZipInfo = info) -> None:
                    with archive.open(member, "r") as source:
                        shutil.copyfileobj(source, stream, length=1024 * 1024)

                _atomic_write(destination, write_member)

        if excluded_count:
            print(f"  skipped {excluded_count} file(s) under excluded paths")
        return len(planned)


def _copy_file_package(
    package: Package,
    source: Path,
    root: Path,
    exclusions: tuple[str, ...],
    dry_run: bool,
    planned_outputs: dict[str, tuple[str, str]],
    report: RunReport,
) -> int:
    if not package.destinations:
        print(f"no destinations: {package.name} — download is retained in the package cache")
        return 0
    copied = 0
    source_digest = _hash_file(source) if dry_run else ""
    for relative_destination in package.destinations:
        if _is_excluded(relative_destination, exclusions):
            print(f"  skipped excluded path: {relative_destination}")
            continue
        if dry_run:
            _preview_output(
                package,
                relative_destination,
                source_digest,
                root,
                planned_outputs,
                report,
            )
        else:
            destination = _repository_destination(root, relative_destination)

            def write_file(stream: Any) -> None:
                with source.open("rb") as input_stream:
                    shutil.copyfileobj(input_stream, stream, length=1024 * 1024)

            _atomic_write(destination, write_file)
        copied += 1
    return copied


def _report_unshadowed_assets(
    packages: list[Package],
    root: Path,
    exclusions: tuple[str, ...],
    planned_outputs: dict[str, tuple[str, str]],
    report: RunReport,
) -> None:
    """List existing assets outside the paths supplied by cached packages."""
    scopes = {"audio", "fonts", "graphics/textures"}
    for package in packages:
        for destination in package.destinations:
            scope = destination if package.kind == "zip" else str(PurePosixPath(destination).parent)
            scopes.add(scope)
    existing: set[str] = set()
    resolved_root = root.resolve()
    for scope in sorted(scopes):
        if _is_excluded(scope, exclusions):
            continue
        for directory, subdirectories, filenames in os.walk(root / scope, followlinks=False):
            relative_directory = Path(directory).relative_to(root)
            subdirectories[:] = [
                name for name in subdirectories
                if not _is_excluded((relative_directory / name).as_posix(), exclusions)
            ]
            for name in filenames:
                relative = (relative_directory / name).as_posix()
                if not _is_excluded(relative, exclusions):
                    existing.add(relative)
    shadowed = 0
    for relative in sorted(existing):
        try:
            key = (root / relative).resolve().relative_to(resolved_root).as_posix()
        except ValueError:
            key = relative
        if key in planned_outputs:
            shadowed += 1
        else:
            report.unshadowed.append(relative)
            print(f"UNSHADOWED: {relative} — no cached package would write this asset")
    print(
        f"coverage: {shadowed} existing asset(s) shadowed, "
        f"{len(report.unshadowed)} unshadowed (excluded paths omitted)"
    )


def extract_all(
    packages: list[Package],
    exclusions: tuple[str, ...],
    root: Path = REPOSITORY_ROOT,
    cache_dir: Path = DEFAULT_CACHE,
    manifest_path: Path = DEFAULT_MANIFEST,
    dry_run: bool = False,
) -> RunReport:
    report = RunReport.empty()
    planned_outputs: dict[str, tuple[str, str]] = {}
    if dry_run:
        print("dry run: inspecting cached packages; no files will be written")
    for package in packages:
        cached, cache_note = find_cached_package(package, cache_dir, manifest_path)
        if cached is None:
            reason = cache_note or "package is not in the local cache"
            print(f"unavailable: {package.name} — {reason}; source page: {package.page}")
            report.unavailable.append(package.name)
            continue
        try:
            if package.kind == "zip":
                count = _extract_zip(
                    package,
                    cached,
                    root,
                    exclusions,
                    dry_run,
                    planned_outputs,
                    report,
                )
            else:
                count = _copy_file_package(
                    package,
                    cached,
                    root,
                    exclusions,
                    dry_run,
                    planned_outputs,
                    report,
                )
            note = f" ({cache_note})" if cache_note else ""
            action = "would extract" if dry_run else "extracted"
            print(f"{action}: {package.name} — {count} file(s){note}")
            report.succeeded.append(package.name)
        except (OSError, PackageError, zipfile.BadZipFile, RuntimeError) as exc:
            print(f"failed: {package.name} — {exc}")
            report.failed.append(package.name)
    if dry_run:
        _report_unshadowed_assets(packages, root, exclusions, planned_outputs, report)
    return report


def print_summary(label: str, report: RunReport) -> None:
    print(
        f"{label} summary: {len(report.succeeded)} succeeded, "
        f"{len(report.unavailable)} unavailable, {len(report.failed)} failed, "
        f"{len(report.conflicts)} conflict(s), {len(report.unshadowed)} unshadowed asset(s)"
    )


def print_manual_downloads(
    packages: list[Package], reports: list[RunReport], cache_dir: Path = DEFAULT_CACHE
) -> None:
    needed = {name for report in reports for name in report.failed + report.unavailable}
    if not needed:
        return
    print("Packages needing manual download:")
    for package in packages:
        if package.name in needed:
            print(f"  {package.name}")
            print(f"    cache file: {canonical_cache_path(package, cache_dir)}")
            print(f"    URL: {package.download or package.page}")
