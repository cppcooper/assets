#!/usr/bin/env python3
"""Download asset packages from direct URLs or itch.io pages into the local cache."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from _asset_packages import DEFAULT_CACHE, DEFAULT_MANIFEST, ManifestError, download_all, load_manifest, print_manual_downloads, print_summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--force", action="store_true", help="redownload packages that already have a cache entry")
    parser.add_argument("--offline", action="store_true", help="report missing packages without making network requests")
    args = parser.parse_args()

    try:
        packages, _ = load_manifest(args.manifest)
    except ManifestError as exc:
        print(f"manifest error: {exc}", file=sys.stderr)
        return 1

    report = download_all(
        packages,
        cache_dir=args.cache_dir,
        manifest_path=args.manifest,
        force=args.force,
        offline=args.offline,
    )
    print_summary("download", report)
    print_manual_downloads(packages, [report], args.cache_dir)
    return 1 if report.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
