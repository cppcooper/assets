"""Integration checks for asset recovery commands using disposable fixtures."""

from __future__ import annotations

import http.server
import contextlib
import html
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock
import zipfile


REPOSITORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY / "scripts"))
import _asset_packages as assets


class AssetScriptsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="asset-scripts-test-")
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.cache = self.directory / "cache"
        self.root = self.directory / "repo"
        self.cache.mkdir()
        self.root.mkdir()
        self.manifest = self.directory / "manifest.json"
        self.requests: list[str] = []
        self.posts: list[tuple[str, bytes, str | None]] = []
        self.responses: dict[str, tuple[int, bytes, int | None]] = {}
        self.server: http.server.ThreadingHTTPServer | None = None

    def serve(self) -> str:
        test = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                self.respond()

            def do_POST(self) -> None:
                body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                test.posts.append((self.path, body, self.headers.get("Cookie")))
                self.respond()

            def respond(self) -> None:
                test.requests.append(self.path)
                status, content, claimed_length = test.responses.get(self.path, (404, b"missing", None))
                self.send_response(status)
                self.send_header("Content-Length", str(len(content) if claimed_length is None else claimed_length))
                self.send_header("Set-Cookie", "test_session=present; Path=/")
                self.end_headers()
                self.wfile.write(content)
                self.close_connection = True

            def log_message(self, *_: object) -> None:
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        return f"http://127.0.0.1:{self.server.server_port}"

    def write_manifest(
        self, packages: list[dict], exclusions: list[str] | None = None,
        page: str = "https://example.invalid/assets",
    ) -> None:
        self.manifest.write_text(json.dumps({
            "exclude": exclusions or ["graphics/textures/misc/"],
            "sources": [{"page": page, "packages": packages}],
        }), encoding="utf-8")

    def package(self, name: str, kind: str, destinations: list[str], download: str | None = None) -> dict:
        value = {"name": name, "type": kind, "to": destinations}
        if download:
            value["download"] = download
        return value

    def zip_cache(self, name: str, members: dict[str, bytes]) -> Path:
        path = self.cache / f"{name}.zip"
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for member, content in members.items():
                archive.writestr(member, content)
        return path

    def run_init(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(REPOSITORY / "init.sh"), "--manifest", str(self.manifest),
             "--cache-dir", str(self.cache), "--root", str(self.root), *arguments],
            cwd="/tmp",
            env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            timeout=30,
        )

    def test_download_only_then_extract_only(self) -> None:
        base = self.serve()
        self.responses["/sheet.png"] = (200, b"sheet bytes", None)
        self.write_manifest([self.package("sheet", "file", ["graphics/sheet.png"], base + "/sheet.png")])
        download = self.run_init("--download-only")
        self.assertEqual(download.returncode, 0, download.stdout)
        self.assertEqual((self.cache / "sheet.png").read_bytes(), b"sheet bytes")
        self.assertEqual(list(self.root.iterdir()), [])
        extract = self.run_init("--extract-only")
        self.assertEqual(extract.returncode, 0, extract.stdout)
        self.assertEqual((self.root / "graphics/sheet.png").read_bytes(), b"sheet bytes")
        self.assertEqual(self.requests, ["/sheet.png"])

    def test_standalone_download_and_extraction_commands(self) -> None:
        (self.cache / "art.bin").write_bytes(b"art bytes")
        self.write_manifest([self.package("art", "file", ["graphics/art.png"])])

        def execute(script: str, *arguments: str) -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                [sys.executable, str(REPOSITORY / "scripts" / script), "--manifest", str(self.manifest),
                 "--cache-dir", str(self.cache), *arguments],
                cwd="/tmp", env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
                text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30,
            )

        download = execute("download-assets.py", "--offline")
        self.assertEqual(download.returncode, 0, download.stdout)
        self.assertIn("cached: art", download.stdout)
        preview = execute("extract-assets.py", "--root", str(self.root), "--dry-run")
        self.assertEqual(preview.returncode, 0, preview.stdout)
        self.assertIn("would add: graphics/art.png", preview.stdout)
        self.assertEqual(list(self.root.iterdir()), [])
        extract = execute("extract-assets.py", "--root", str(self.root))
        self.assertEqual(extract.returncode, 0, extract.stdout)
        self.assertEqual((self.root / "graphics/art.png").read_bytes(), b"art bytes")

    def test_master_downloads_and_extracts_zip(self) -> None:
        archive = self.zip_cache("upstream", {"release-v12/a.txt": b"a", "release-v12/b.txt": b"b"})
        content = archive.read_bytes()
        archive.unlink()
        base = self.serve()
        self.responses["/latest.zip"] = (200, content, None)
        self.write_manifest([self.package("art", "zip", ["graphics/art"], base + "/latest.zip")])
        result = self.run_init()
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual((self.root / "graphics/art/a.txt").read_bytes(), b"a")
        self.assertTrue((self.cache / "art.zip").is_file())

    def test_dry_run_does_not_download(self) -> None:
        base = self.serve()
        self.responses["/sheet.png"] = (200, b"sheet", None)
        self.write_manifest([self.package("sheet", "file", ["sheet.png"], base + "/sheet.png")])
        result = self.run_init("--dry-run")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("unavailable: sheet", result.stdout)
        self.assertEqual(self.requests, [])
        self.assertEqual(list(self.cache.iterdir()), [])
        self.assertEqual(list(self.root.iterdir()), [])

    def test_dry_run_distinguishes_same_changed_and_new_files(self) -> None:
        self.zip_cache("art", {"v1/same.txt": b"same", "v1/changed.txt": b"incoming", "v1/new.txt": b"new"})
        target = self.root / "graphics/art"
        target.mkdir(parents=True)
        (target / "same.txt").write_bytes(b"same")
        (target / "changed.txt").write_bytes(b"local edits")
        self.write_manifest([self.package("art", "zip", ["graphics/art"])])
        result = self.run_init("--extract-only", "--dry-run")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("unchanged: graphics/art/same.txt", result.stdout)
        self.assertIn("CONFLICT: graphics/art/changed.txt", result.stdout)
        self.assertIn("would add: graphics/art/new.txt", result.stdout)
        self.assertEqual((target / "changed.txt").read_bytes(), b"local edits")
        self.assertFalse((target / "new.txt").exists())

    def test_dry_run_reports_packages_with_different_contents_at_same_path(self) -> None:
        (self.cache / "first.bin").write_bytes(b"one")
        (self.cache / "second.bin").write_bytes(b"two")
        self.write_manifest([self.package("first", "file", ["same.png"]), self.package("second", "file", ["same.png"])])
        result = self.run_init("--dry-run")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("CONFLICT: same.png", result.stdout)
        self.assertIn("first and second", result.stdout)
        self.assertFalse((self.root / "same.png").exists())

    def test_dry_run_accepts_identical_packages_at_same_path(self) -> None:
        (self.cache / "first.bin").write_bytes(b"same")
        (self.cache / "second.bin").write_bytes(b"same")
        self.write_manifest([self.package("first", "file", ["same.png"]), self.package("second", "file", ["same.png"])])
        result = self.run_init("--dry-run")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertNotIn("CONFLICT:", result.stdout)

    def test_dry_run_reports_existing_file_blocking_parent_directory(self) -> None:
        (self.cache / "art.bin").write_bytes(b"art")
        (self.root / "images").write_bytes(b"blocking file")
        self.write_manifest([self.package("art", "file", ["images/sheet.png"])])
        result = self.run_init("--dry-run")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("CONFLICT:", result.stdout)
        self.assertIn("images", result.stdout)
        self.assertEqual((self.root / "images").read_bytes(), b"blocking file")

    def test_dry_run_reports_planned_file_and_directory_collision(self) -> None:
        (self.cache / "first.bin").write_bytes(b"file")
        (self.cache / "second.bin").write_bytes(b"child")
        packages = [self.package("first", "file", ["images/art"]), self.package("second", "file", ["images/art/sheet.png"])]
        for ordered_packages in (packages, list(reversed(packages))):
            with self.subTest(order=[package["name"] for package in ordered_packages]):
                self.write_manifest(ordered_packages)
                result = self.run_init("--dry-run")
                self.assertEqual(result.returncode, 0, result.stdout)
                self.assertIn("CONFLICT:", result.stdout)
                self.assertEqual(list(self.root.iterdir()), [])

    def test_dry_run_reports_existing_directory_at_file_target(self) -> None:
        (self.cache / "art.bin").write_bytes(b"art")
        (self.root / "art.png").mkdir()
        self.write_manifest([self.package("art", "file", ["art.png"])])
        result = self.run_init("--dry-run")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("CONFLICT: art.png: existing directory", result.stdout)
        self.assertTrue((self.root / "art.png").is_dir())

    def test_excludes_misc_and_preserves_unrelated_files_on_repeated_extraction(self) -> None:
        self.zip_cache("art", {"v1/misc/never.png": b"excluded", "v1/pack/sheet.png": b"art"})
        target = self.root / "graphics/textures"
        (target / "misc").mkdir(parents=True)
        (target / "misc/local.png").write_bytes(b"local")
        (target / "unrelated.txt").write_bytes(b"retain")
        self.write_manifest([self.package("art", "zip", ["graphics/textures"])])
        for _ in range(2):
            result = self.run_init("--extract-only")
            self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual((target / "pack/sheet.png").read_bytes(), b"art")
        self.assertEqual((target / "unrelated.txt").read_bytes(), b"retain")
        self.assertEqual((target / "misc/local.png").read_bytes(), b"local")
        self.assertFalse((target / "misc/never.png").exists())
        self.assertFalse((target / "v1").exists())

    def test_rejects_traversal_before_writing_valid_members(self) -> None:
        self.zip_cache("art", {"v1/valid.txt": b"valid", "../escape.txt": b"unsafe"})
        self.write_manifest([self.package("art", "zip", ["art"])])
        result = self.run_init("--extract-only")
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("unsafe ZIP member", result.stdout)
        self.assertEqual(list(self.root.iterdir()), [])
        self.assertFalse((self.directory / "escape.txt").exists())

    def test_rejects_zip_symlinks(self) -> None:
        with zipfile.ZipFile(self.cache / "art.zip", "w") as archive:
            member = zipfile.ZipInfo("link")
            member.create_system = 3
            member.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(member, "../../escape")
        self.write_manifest([self.package("art", "zip", ["art"])])
        result = self.run_init("--extract-only")
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("symbolic link", result.stdout)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_modes_and_strict_missing_sources(self) -> None:
        self.write_manifest([self.package("missing", "zip", ["art"])])
        self.assertEqual(self.run_init("--download-only", "--offline", "--strict").returncode, 2)
        self.assertEqual(self.run_init("--extract-only", "--strict").returncode, 2)
        self.assertEqual(self.run_init("--download-only", "--extract-only").returncode, 2)
        self.assertEqual(self.run_init("--download-only", "--dry-run").returncode, 2)

    def test_bad_zip_download_cleans_partial_cache(self) -> None:
        base = self.serve()
        self.responses["/bad.zip"] = (200, b"not a ZIP", None)
        self.write_manifest([self.package("art", "zip", ["art"], base + "/bad.zip")])
        result = self.run_init("--download-only")
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertEqual(list(self.cache.iterdir()), [])
        self.assertEqual(list(self.root.iterdir()), [])
        self.assertIn("Packages needing manual download:", result.stdout)
        self.assertEqual(result.stdout.strip().splitlines()[-1], "    URL: " + base + "/bad.zip")

    def test_master_prints_failed_package_urls_after_extraction(self) -> None:
        base = self.serve()
        self.responses["/good.png"] = (200, b"good", None)
        self.write_manifest([
            self.package("missing", "file", ["missing.png"], base + "/missing.png"),
            self.package("good", "file", ["good.png"], base + "/good.png"),
        ])
        result = self.run_init()
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertEqual((self.root / "good.png").read_bytes(), b"good")
        self.assertGreater(result.stdout.index("Packages needing manual download:"), result.stdout.index("extraction summary:"))
        self.assertEqual(result.stdout.count("    URL: " + base + "/missing.png"), 1)
        self.assertNotIn("    URL: " + base + "/good.png", result.stdout)

    def test_standalone_downloader_lists_missing_source_page_at_end(self) -> None:
        self.write_manifest([self.package("missing", "zip", ["graphics/art"])])
        result = subprocess.run(
            [sys.executable, str(REPOSITORY / "scripts/download-assets.py"), "--manifest", str(self.manifest),
             "--cache-dir", str(self.cache), "--offline"],
            cwd="/tmp", env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30,
        )
        self.assertIn("Packages needing manual download:", result.stdout)
        self.assertEqual(result.stdout.strip().splitlines()[-1], "    URL: https://example.invalid/assets")

    def test_truncated_download_preserves_previous_cache(self) -> None:
        base = self.serve()
        self.responses["/sheet.png"] = (200, b"truncated", 100)
        (self.cache / "sheet.png").write_bytes(b"original cached data")
        self.write_manifest([self.package("sheet", "file", ["sheet.png"], base + "/sheet.png")])
        result = self.run_init("--download-only", "--force")
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertEqual((self.cache / "sheet.png").read_bytes(), b"original cached data")
        self.assertEqual(list(self.cache.iterdir()), [self.cache / "sheet.png"])

    def itch_listing(self, uploads: list[tuple[str, int | None]], token: str = "page-token") -> bytes:
        content = f'<meta name="csrf_token" value="{token}">'
        for name, upload_id in uploads:
            button = f'<a data-upload_id="{upload_id}" class="download_btn">Download</a>' if upload_id else ""
            content += f'<div class="upload">{button}<div class="info_column"><strong class="name" title="{html.escape(name)}">{html.escape(name)}</strong></div></div>'
        return content.encode()

    def download_itch_fixture(self, force: bool = False) -> tuple[assets.RunReport, str]:
        packages, _ = assets.load_manifest(self.manifest)
        output = io.StringIO()
        with mock.patch.object(assets, "_is_itch_page", return_value=True), contextlib.redirect_stdout(output):
            report = assets.download_all(packages, self.cache, self.manifest, force=force)
        return report, output.getvalue()

    def test_itch_page_resolves_public_file_with_cookies(self) -> None:
        base = self.serve()
        self.responses["/pack"] = (200, self.itch_listing([("Sheet_v3.png", 7)]), None)
        self.responses["/pack/file/7"] = (200, json.dumps({"url": base + "/sheet.png"}).encode(), None)
        self.responses["/sheet.png"] = (200, b"sheet bytes", None)
        self.write_manifest([self.package("sheet", "file", ["graphics/textures/sheet.png"])], page=base + "/pack")
        report, output = self.download_itch_fixture()
        self.assertEqual(report.succeeded, ["sheet"], output)
        self.assertEqual((self.cache / "sheet.bin").read_bytes(), b"sheet bytes")
        self.assertEqual(self.posts, [("/pack/file/7", b"csrf_token=page-token", "test_session=present")])
        self.assertIn("resolved: sheet", output)

    def test_itch_donation_gate_resolves_free_zip_with_new_token(self) -> None:
        base = self.serve()
        archive = self.zip_cache("upstream", {"v3/art.png": b"art"})
        content = archive.read_bytes()
        archive.unlink()
        self.responses["/pack"] = (200, self.itch_listing([("Art_v3_Free_Version.zip", None)]), None)
        self.responses["/pack/download_url"] = (200, json.dumps({"url": base + "/pack/download/free"}).encode(), None)
        self.responses["/pack/download/free"] = (200, self.itch_listing([("Art_v3_Free_Version.rar", 4), ("Art_v3_Free_Version.zip", 5)], "fresh-token"), None)
        self.responses["/pack/file/5"] = (200, json.dumps({"url": base + "/art.zip"}).encode(), None)
        self.responses["/art.zip"] = (200, content, None)
        self.write_manifest([self.package("art-free", "zip", ["graphics/art"])], page=base + "/pack")
        report, output = self.download_itch_fixture()
        self.assertEqual(report.succeeded, ["art-free"], output)
        self.assertEqual((self.cache / "art-free.zip").read_bytes(), content)
        self.assertEqual([body for _, body, _ in self.posts], [b"csrf_token=page-token", b"csrf_token=fresh-token"])
        self.assertNotIn("/pack/file/4", self.requests)

    def test_itch_shared_source_reuses_page_and_force_refreshes_cache(self) -> None:
        base = self.serve()
        self.responses["/pack"] = (200, self.itch_listing([("First_v2.png", 1), ("Second_v5.png", 2)]), None)
        for name, upload_id in [("first", 1), ("second", 2)]:
            self.responses[f"/pack/file/{upload_id}"] = (200, json.dumps({"url": base + f"/{name}.png"}).encode(), None)
            self.responses[f"/{name}.png"] = (200, name.encode(), None)
            (self.cache / f"{name}.bin").write_bytes(b"older cache")
        self.write_manifest([self.package("first", "file", ["first.png"]), self.package("second", "file", ["second.png"])], page=base + "/pack")
        report, output = self.download_itch_fixture(force=True)
        self.assertEqual(report.succeeded, ["first", "second"], output)
        self.assertEqual(self.requests.count("/pack"), 1)
        self.assertEqual((self.cache / "first.bin").read_bytes(), b"first")
        self.assertEqual((self.cache / "second.bin").read_bytes(), b"second")

    def test_itch_matches_compound_lowercase_package_name(self) -> None:
        base = self.serve()
        self.responses["/pack"] = (200, self.itch_listing([("punyworld-overworld-tileset.png", 7)]), None)
        self.responses["/pack/file/7"] = (200, json.dumps({"url": base + "/sheet.png"}).encode(), None)
        self.responses["/sheet.png"] = (200, b"sheet bytes", None)
        name = "puny-world-overworld-tileset"
        self.write_manifest([self.package(name, "file", ["graphics/textures/tilesets/punyworld-overworld-tileset.png"])], page=base + "/pack")
        report, output = self.download_itch_fixture()
        self.assertEqual(report.succeeded, [name], output)

    def test_download_retries_rate_limit(self) -> None:
        response = io.BytesIO(b"file bytes")
        request = "https://example.invalid/file.png"
        error = assets.urllib.error.HTTPError(request, 429, "rate limit", {"Retry-After": "0"}, io.BytesIO())
        with mock.patch.object(assets.urllib.request, "urlopen", side_effect=[error, response]) as open_url, mock.patch.object(assets.time, "sleep") as sleep, contextlib.redirect_stdout(io.StringIO()):
            self.assertIs(assets._open_url(request), response)
            self.assertEqual(open_url.call_count, 2)
            sleep.assert_called_once_with(0)

    def test_itch_ambiguous_versions_are_reported_unavailable(self) -> None:
        base = self.serve()
        self.responses["/pack"] = (200, self.itch_listing([("Art_v1.zip", 1), ("Art_v2.zip", 2)]), None)
        self.write_manifest([self.package("art", "zip", ["graphics/art"])], page=base + "/pack")
        report, output = self.download_itch_fixture()
        self.assertEqual(report.unavailable, ["art"], output)
        self.assertEqual(report.failed, [])
        self.assertIn("multiple files match", output)
        self.assertEqual(self.posts, [])
        self.assertEqual(list(self.cache.iterdir()), [])

    def test_itch_purchase_required_is_reported_unavailable(self) -> None:
        base = self.serve()
        self.responses["/pack"] = (200, self.itch_listing([("Art.zip", None)]), None)
        self.responses["/pack/download_url"] = (200, b'{"errors": ["purchase required"]}', None)
        self.write_manifest([self.package("art", "zip", ["graphics/art"])], page=base + "/pack")
        report, output = self.download_itch_fixture()
        self.assertEqual(report.unavailable, ["art"], output)
        self.assertEqual(report.failed, [])
        self.assertIn("purchase required", output)
        self.assertEqual(list(self.cache.iterdir()), [])

    def test_dry_run_lists_unshadowed_assets_and_excludes_misc(self) -> None:
        self.zip_cache("art", {"v1/same.png": b"same", "v1/changed.png": b"incoming"})
        paths = {
            "graphics/textures/art/same.png": b"same",
            "graphics/textures/art/changed.png": b"local edits",
            "graphics/textures/art/extra.png": b"extra",
            "graphics/textures/ui/button.png": b"ui",
            "audio/sfx/orphan.wav": b"audio",
            "fonts/local.ttf": b"font",
            "graphics/textures/misc/exempt.png": b"exempt",
        }
        for relative, content in paths.items():
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        self.write_manifest([self.package("art", "zip", ["graphics/textures/art"])])
        result = self.run_init("--dry-run")
        self.assertEqual(result.returncode, 0, result.stdout)
        for relative in ["graphics/textures/art/extra.png", "graphics/textures/ui/button.png", "audio/sfx/orphan.wav", "fonts/local.ttf"]:
            self.assertIn(f"UNSHADOWED: {relative}", result.stdout)
        self.assertNotIn("UNSHADOWED: graphics/textures/art/changed.png", result.stdout)
        self.assertNotIn("UNSHADOWED: graphics/textures/misc/", result.stdout)
        self.assertIn("2 existing asset(s) shadowed, 4 unshadowed", result.stdout)
        self.assertIn("CONFLICT: graphics/textures/art/changed.png", result.stdout)
        for relative, content in paths.items():
            self.assertEqual((self.root / relative).read_bytes(), content)

    def test_missing_cache_leaves_assets_unshadowed_until_package_is_present(self) -> None:
        target = self.root / "graphics/textures/art/sheet.png"
        target.parent.mkdir(parents=True)
        target.write_bytes(b"sheet")
        self.write_manifest([self.package("art", "zip", ["graphics/textures/art"])])
        before = self.run_init("--dry-run")
        self.assertIn("UNSHADOWED: graphics/textures/art/sheet.png", before.stdout)
        self.zip_cache("art", {"v9/sheet.png": b"sheet"})
        after = self.run_init("--dry-run")
        self.assertEqual(after.returncode, 0, after.stdout)
        self.assertIn("1 existing asset(s) shadowed, 0 unshadowed", after.stdout)


if __name__ == "__main__":
    unittest.main()
