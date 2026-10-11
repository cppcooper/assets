#!/usr/bin/env python3
"""Download available packages, then extract all cached packages."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from _asset_packages import (
    DEFAULT_CACHE,
    DEFAULT_MANIFEST,
    ManifestError,
    REPOSITORY_ROOT,
    download_all,
    extract_all,
    load_manifest,
    print_manual_downloads,
    print_summary,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--root", type=Path, default=REPOSITORY_ROOT, help="repository root for extraction")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--download-only", action="store_true", help="download packages without extracting them")
    modes.add_argument("--extract-only", action="store_true", help="extract cached packages without downloading")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="show extraction conflicts and unshadowed assets from the local cache; do not download or write files",
    )
    parser.add_argument("--force", action="store_true", help="redownload packages from direct URLs or supported source pages")
    parser.add_argument("--offline", action="store_true", help="skip network requests")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="return status 2 when any package is unavailable from the cache or its source",
    )
    args = parser.parse_args()

    if args.download_only and args.dry_run:
        parser.error("--dry-run previews extraction and cannot be combined with --download-only")
    if (args.extract_only or args.dry_run) and args.force:
        parser.error("--force only applies when downloading packages")

    try:
        packages, exclusions = load_manifest(args.manifest)
    except ManifestError as exc:
        print(f"manifest error: {exc}", file=sys.stderr)
        return 1

    download_report = None
    extraction_report = None
    if not args.extract_only and not args.dry_run:
        download_report = download_all(
            packages,
            cache_dir=args.cache_dir,
            manifest_path=args.manifest,
            force=args.force,
            offline=args.offline,
        )
        print_summary("download", download_report)
    if not args.download_only:
        extraction_report = extract_all(
            packages,
            exclusions,
            root=args.root,
            cache_dir=args.cache_dir,
            manifest_path=args.manifest,
            dry_run=args.dry_run,
        )
        print_summary("extraction", extraction_report)

    reports = [report for report in (download_report, extraction_report) if report is not None]
    print_manual_downloads(packages, reports, args.cache_dir)

    if (download_report and download_report.failed) or (extraction_report and extraction_report.failed):
        return 1
    if args.strict and (
        (download_report and download_report.unavailable)
        or (extraction_report and extraction_report.unavailable)
    ):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
