#!/usr/bin/env python3
"""Extract cached asset packages to their manifest destinations."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from _asset_packages import (
    DEFAULT_CACHE,
    DEFAULT_MANIFEST,
    ManifestError,
    REPOSITORY_ROOT,
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
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="show files that would be added, conflicts, and unshadowed assets without writing files",
    )
    args = parser.parse_args()

    try:
        packages, exclusions = load_manifest(args.manifest)
    except ManifestError as exc:
        print(f"manifest error: {exc}", file=sys.stderr)
        return 1

    report = extract_all(
        packages,
        exclusions,
        root=args.root,
        cache_dir=args.cache_dir,
        manifest_path=args.manifest,
        dry_run=args.dry_run,
    )
    print_summary("extraction", report)
    print_manual_downloads(packages, [report], args.cache_dir)
    return 1 if report.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
