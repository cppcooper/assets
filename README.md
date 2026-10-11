# Assets

Artwork, audio, fonts, shaders, and graphics definitions used by college projects.
Git stores the definitions and assets that cannot be recovered from downloads.
Most textures and audio are restored using the [download manifest](.download-manifest/manifest.json).

## Initialize

Requires Bash and Python 3. The scripts use Python's standard library; no extra
Python packages are needed.

```sh
./init.sh
```

This downloads available packages into `.download-manifest/packages/`, then
extracts them to the locations listed in the manifest. Cached packages are reused.
The launcher works from any directory and runs with the repository as its working
directory.

Extraction overwrites files at its destination paths and keeps unrelated files.
Upstream downloads can change, so a newer package may add files or differ from a
previously extracted copy. To review the changes first:

```sh
./init.sh --download-only
./init.sh --dry-run
./init.sh --extract-only
```

### Extraction preview

`--dry-run` inspects the local cache without downloading or writing files. Download
the packages first to get complete coverage of the available sources.

| Output | Meaning |
| --- | --- |
| `unchanged` | The existing file matches the package contents. |
| `would add` | Extraction would create this file. |
| `CONFLICT` | Existing content would be overwritten, packages disagree on a destination, or a file/directory blocks extraction. |
| `UNSHADOWED` | An existing asset would not be written by any cached package. |

Coverage includes `audio/`, `fonts/`, `graphics/textures/`, and manifest
destinations. Unshadowed files can include deliberately tracked local assets;
missing cached packages also leave assets unshadowed.
`graphics/textures/misc/` is excluded from extraction and coverage checks.

Conflicts and unshadowed assets are reported for review; they do not by themselves
cause a nonzero exit status.

### Other options

| Option | Effect |
| --- | --- |
| `--force` | Download packages again instead of reusing the cache. |
| `--offline` | Use cached packages without making network requests. |
| `--strict` | Return status `2` if any package is unavailable. |
| `--manifest PATH` | Use another manifest. |
| `--cache-dir PATH` | Use another package cache. |
| `--root PATH` | Extract into another root, useful for inspecting a separate copy. |
| `--help` | Show all options. |

Operational failures return status `1`. Unavailable packages are reported but do
not cause a nonzero status unless `--strict` is used. `--download-only` and
`--extract-only` are mutually exclusive; `--dry-run` previews extraction only.

If a package fails or is unavailable, the final output lists its URL and expected
cache filename for manual downloading. Save the original package at that path
(keep ZIPs intact), then preview and extract it with the same commands above.

The individual stages can also be run as
`python3 scripts/download-assets.py` and `python3 scripts/extract-assets.py`.

## Manage download sources

[.download-manifest/manifest.json](.download-manifest/manifest.json) is the single
manifest for all sources and extraction destinations. Each entry in `sources`
has a creator/source `page` and a list of `packages`.

| Package field | Meaning |
| --- | --- |
| `name` | Unique, stable package identifier and cache filename stem. Leave version numbers out. |
| `type` | `zip` for an archive, or `file` for a single asset. |
| `download` | Optional direct download URL. |
| `to` | Destination directories for a ZIP, or exact destination filenames for a single file. An empty list retains the download without extracting it. |

Free itch.io downloads can be resolved from their source pages, obtaining fresh
download links when needed. Other sources need a direct `download` URL or a manual
download. Package names are matched against the files offered by a page; ambiguous
matches are reported as unavailable, with instructions for manual downloading.

A ZIP is unpacked into every directory in `to`. One shared outer folder is
removed when all files are nested beneath it; the remaining directory structure
is preserved. A `file` package is copied to every filename in `to`. Destinations
are relative to the extraction root.

ZIPs use `<name>.zip` in the cache. Single files use the direct URL's extension
when available, otherwise `.bin`. The manual download instructions print the
exact path to use.

The top-level `exclude` list protects paths from extraction and coverage checks.
`unresolved` records assets whose sources have not been found. The scripts check
the manifest themselves; the schemas in `schemas/` describe game engine assets.

## What belongs in Git

[.gitignore](.gitignore) ignores downloaded packages and the contents of
`audio/` and `graphics/textures/` by default. New packages under those directories
need no additional ignore rules. The manifest and selected assets that downloads
cannot reproduce have explicit exceptions.

The retained texture assets currently include the local artwork in
`graphics/textures/misc/`, the UI images, and `graphics/textures/tilesets/tileset.png`.
The entire `misc/` directory is exempt from source recovery and extraction.
Fonts, shaders, graphics definitions, and engine schemas are also tracked.

For a new asset that cannot be reproduced, add exceptions for its parent
directories and its filename before adding it to Git. For example:

```gitignore
!/graphics/textures/local-art/
!/graphics/textures/local-art/sprite.png
```

When an asset becomes reproducible, confirm that the cached download reports it
as `unchanged` in a dry run. Then remove it from Git's index with
`git rm --cached -- path/to/file` and remove its ignore exception. This keeps the
local copy. Ignore rules alone do not untrack files already committed.

## Repository layout

| Path | Contents |
| --- | --- |
| `audio/`, `graphics/textures/` | Downloaded assets and the retained local exceptions. |
| `graphics/definitions/` | Sprite and tileset definitions. |
| `graphics/graphics-manifests.json` | Graphics manifest index. |
| `graphics/fonts/`, `fonts/` | Bitmap fonts and the fallback TrueType font, with supporting data. |
| `graphics/shaders/`, `graphics/ui/` | Shaders and UI resources. |
| `schemas/` | JSON schemas for game engine graphics definitions and manifests. |
| `scripts/` | Download and extraction tools. |

## Credits and licenses

Some assets were collected for college projects before their credits and licenses
were recorded. Some older artwork still has no identified source or licensing
information and may need to be replaced.

Check each source page and any bundled license or credit files before reusing an
asset.
