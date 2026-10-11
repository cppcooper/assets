# Asset download and extraction plan

> Temporary planning note from the 2026-10-11 conversation. Keep while building
> the asset recovery workflow; remove or replace it once the implementation has
> a durable home in the repository documentation.

## Goal

Make this repository reproducible from upstream packages wherever a source can
be identified and downloaded. Provide:

1. A download script that fetches all currently available source packages into
   the local package cache.
2. An extraction script that places each package's required files in every
   repository destination so a fresh checkout can recreate the current asset
   tree.
3. One master script that runs download and extraction for all available
   sources.

The complete directory `graphics/textures/misc/` is expressly out of scope.
Its asset origins are unknown; leave those files alone and do not make the
workflow depend on them.

## Repository facts found

- `.download-manifest/manifest.json` is the single source catalog. It keeps
  stable package labels, source pages, any known direct download URLs, and
  repository destinations together.
- The user deleted the five original cached ZIPs after extraction. The clean
  cache test downloaded all 12 manifest packages into
  `.download-manifest/packages/`. The two 0x72 packages have no extraction
  destinations in the current tree.
- The source pages for itch.io packages are not necessarily direct download
  URLs. Package filenames, sizes, and hashes are intentionally not catalog
  identity: those can change when a provider publishes a new version.
- The Minifantasy visual assets are correctly associated with Krishna
  Palacio's page. The old SFX record had incorrectly associated them with
  Leohpaz's audio page.
- `.gitignore` excludes downloaded archives and the specific asset collections
  and textures recreated by `init.sh`, including their license and source
  notes. Assets without download mappings remain eligible for Git. A normal
  Git checkout therefore does not contain the full local asset tree that the extraction
  workflow must recreate.
- Manifest coverage is not complete: `tilesets/tileset.png` has no identified
  source. The five `ui/` files and the three `fonts/` files are committed to
  the repository and are not supplied by downloads. Audit all assets outside the excluded
  `graphics/textures/misc/` tree; do not assume known sources account for every
  file.
- Some files are repository-owned, generated, or derived rather than copied
  directly from a package. Record those cases explicitly so extraction does
  not overwrite generated work or claim an upstream archive can reproduce it.

## Directory-name source searches

Searches used the immediate directory names under `graphics/textures/` and
then narrowed results with filenames found inside those directories. The
verification section records which package mappings now reproduce the local
assets; the remaining candidates still need package comparisons.

| Directory | Likely source | Evidence and follow-up |
| --- | --- | --- |
| `MiniWorldSprites/` | [Free 16x16 Mini World Sprites](https://merchant-shade.itch.io/16x16-mini-world-sprites) by Shade and octoshrimpy | The downloaded archive matches all 142 current files. It also supplies 66 additional files; extraction filtering still needs attention if exact reproduction is required. |
| `Minifantasy_ForgottenPlains/` | The old manifest mistakenly linked the visual package to Leohpaz's audio page. It is now recorded under Krishna Palacio's [Minifantasy: Forgotten Plains page](https://krishna-palacio.itch.io/minifantasy-forgotten-plains). | The Leohpaz page lists the SFX and music packages; Krishna Palacio's page lists the visual package. Preserve its license and credits; the free version is non-commercial and requires credit. The target path is now version-neutral. |
| `char-customizer/` | [Free 16x16 Mini World Sprites](https://merchant-shade.itch.io/16x16-mini-world-sprites) by Shade and octoshrimpy | `Character-Customizer.zip` matches all three current files, including the XCF, PNG, and ReadMe. |
| `tilesets/` — `dungeon_tiles.png` | [Dungeon tileset](https://lpc.opengameart.org/content/dungeon-tileset) by Buch | OpenGameArt lists a same-named PNG and describes a 16x16 orthographic dungeon sheet under CC0. Likely match to the 23-by-24 local sheet; verify dimensions/hash before wiring it in. |
| `tilesets/` — `magecity.png` | [Mage City Arcanos](https://opengameart.org/content/mage-city-arcanos) by Hyptosis | OpenGameArt lists the exact `magecity.png` file and CC0 licensing. Likely match; verify local bytes. |
| `tilesets/` — `punyworld-overworld-tileset.png` | [Free 16x16 Puny World Tileset](https://merchant-shade.itch.io/16x16-puny-world) by Shade | The standalone PNG matches the current file. Package matching handles `puny-world` and `punyworld` as the same words. |
| `tilesets/` — `tileset.png` | Unresolved | The local tileset definition refers to source data named `tileset.dat`; directory-name and filename searches did not identify a trustworthy upstream source. |
| `ui/` | Unresolved / possibly repository-made | Searches for `quadrants-alt.png`, `white_button.png`, and `WhiteBackdrop.png` found no reliable upstream match. Inspect project history or determine whether these were created for this repository. |
| `misc/` | Excluded | A few filename searches were inconclusive. Regardless of results, this entire directory remains excluded from source recovery and extraction per the project scope. |

The original itch.io source URLs were preserved as `page` during consolidation.
The first downloader read only `download`, leaving those pages unused. The
downloader now follows itch.io's public download buttons, including the free
download step on donation pages. It resolves current file links per run rather
than storing expiring links in the manifest. Compare package contents with the
local directory before declaring a collection reproducible.

## Source catalog work

For every in-scope asset collection, record:

- stable upstream project/page URI, plus a direct download URI when one is
  available;
- a stable package label that does not include the current release filename or
  version;
- whether the source page can be resolved automatically or needs provider
  support / manual acquisition;
- applicable license/credit files and any redistribution or credential limits;
- the repository paths that the package supplies, including renamed or
  selected archive members.

Keep unresolved sources visible as unresolved. Source-page resolution is
implemented for itch.io; other providers need a direct download URL or their
own resolver.

## Script responsibilities

- **Downloader:** read the source catalog, resolve the current package from its
  page or direct URL, and cache it under a stable local package label rather
  than its upstream versioned filename. Report sources that need interactive
  or provider-specific handling.
- **Extractor:** route each package to its `to` paths. For ZIPs, strip one
  common top-level wrapper directory when every archive member has one;
  otherwise preserve the archive's relative paths. Make repeated runs
  predictable, reject archive paths that escape the repository, and avoid
  deleting unrelated files. Do not touch `graphics/textures/misc/`.
- **Master script:** invoke the downloader and then extractor for every
  available package, summarize successes and unavailable sources, and return
  a failure status when a package marked available cannot be fetched, verified,
  or extracted.

## Current script implementation

The first implementation lives in `scripts/` and uses Python 3's standard
library only:

- `download-assets.py` downloads packages with a direct `download` URL or an
  itch.io `page` into
  `.download-manifest/packages/` under the stable package name. It reuses
  cached packages and validates ZIP integrity before retaining a new archive.
  It retains cookies and CSRF tokens for itch.io's public download flow,
  matches stable package names against current filenames, and reports
  ambiguous or restricted downloads as unavailable. Brief HTTP 429/503 errors
  are retried. Failed and unavailable packages are listed at the end with the
  URL for manual downloading and the expected cache filename.
- `extract-assets.py` copies `file` packages and extracts ZIP packages to each
  `to` destination. It strips one common wrapper directory, writes each output
  file atomically, rejects unsafe ZIP paths and symbolic links, and skips any
  output under an excluded path. Existing unrelated files are left in place.
- `download-and-extract-assets.py` runs both steps. `--strict` returns status 2
  when any package remains unavailable; operational download or extraction
  failures return status 1.
- The root `init.sh` forwards options to the master runner. `--download-only`
  fetches packages without extraction; `--extract-only` uses the local cache;
  `--dry-run` inspects the local cache without downloading or writing files.
  The dry run prints `CONFLICT` for existing files with different contents and
  for packages that would write different contents to the same path. It also
  reports files or directories that block a required destination, including
  conflicts between planned files and planned directories. `UNSHADOWED`
  identifies existing files that no cached package would write. Coverage scans
  `audio/`, `fonts/`, `graphics/textures/`, and manifest destinations; excluded
  paths are omitted. A missing package cache leaves its assets unshadowed
  until a package can actually be inspected.

From the repository root, run `./init.sh`, `./init.sh --download-only`,
`./init.sh --extract-only`, or `./init.sh --dry-run`. The launcher can also be
invoked by its path from another working directory. Existing ZIPs whose
filenames clearly match a stable package name are recognized, but ambiguous
matches are left for manual resolution.

## Verification on 2026-10-11

The integration checks in `tests/test_asset_scripts.py` use temporary
directories and a local HTTP server. Run them with
`PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v`.
All 27 cases passed, covering the initialization modes, standalone commands,
dry-run content and directory conflicts, repeated extraction, exclusions,
unsafe ZIP paths, symbolic links, failed downloads, and cache preservation.
The added cases cover itch.io's public and donation download flows, refreshed
CSRF tokens, cookie retention, ambiguous filenames, purchase requirements,
shared source pages, forced cache refreshes, compound lowercase names, rate
limit retries, unshadowed assets, and final manual download URL reporting.
Testing exposed and fixed missing file/directory conflict reporting and
acceptance of truncated downloads. Downloads now check the response's
`Content-Length` before replacing an existing cache entry.

The two direct URLs were downloaded into a temporary cache. Those files and
the five existing ZIP packages were extracted into a temporary repository
root, then compared with the current asset tree using SHA-256. All 2,196 files
matched; the verified destinations had no missing or extra files:

| Package | Matching files |
| --- | ---: |
| `minifantasy-forgotten-plains-free` | 23 |
| `buch-dungeon-tileset` | 1 |
| `mage-city-arcanos` | 1 |
| `dungeon-crawl-game-sounds` | 28 |
| `minifantasy-forgotten-plains-sfx` | 37 |
| `minifantasy-forgotten-plains-music` | 5 |
| `pixel-combat-single-files` | 2,101 |

The initial dry run with the temporary download cache reported seven available
packages, five unavailable packages, zero failures, and zero conflicts. The
current assets and source archives were unchanged by these checks, and an
excluded `graphics/textures/misc/` sentinel in the temporary root was preserved.
At that stage the downloader lacked itch.io resolution. After the user deleted
the old ZIP cache, the first live download attempt obtained ten packages,
failed on MiniWorldSprites with HTTP 429, and reported Puny World unavailable
because its lowercase compound filename did not match. After fixing matching
and adding rate limit retries, a follow-up run downloaded those two packages
and reused the other ten from cache. All 12 packages were obtained across
those runs. The final run reported zero unavailable packages and zero failures;
an additional offline check confirmed that all 12 cache entries were present.

The subsequent dry run against the current repository reported:

- 2,342 existing files supplied by downloads, all byte-identical;
- zero conflicts and zero unavailable packages;
- 66 additions from the MiniWorldSprites archive (the Cyan, Lime, Purple, Red,
  and Wood building sets plus `OtherLinks.docx`);
- nine unshadowed files: `fonts/DejaVuSans.ttf`, `fonts/DejaVuSansLicense.txt`,
  `fonts/README.md`, `graphics/textures/tilesets/tileset.png`, and the five
  files in `graphics/textures/ui/`.

Git tracks the three font files and five UI files. `tilesets/tileset.png`
remains the uncovered local asset. Exact reproduction still needs a decision
about the 66 additional MiniWorldSprites files. The snapshot of all 2,357
current asset files, including excluded files, was unchanged after the dry
run. The full log is `/tmp/assets-itch-after-dry-run.log`.

## Source manifest

`.download-manifest/manifest.json` is the only source manifest. Each page groups
its packages; each package has a stable `name`, a `type`, and zero or more
repository-relative `to` destinations. An optional `download` field holds a
known direct URL. The `exclude` list keeps `graphics/textures/misc/` out of the
workflow, and `unresolved` records assets without a located source.

The manifest deliberately omits upstream filenames, release versions, cache
paths, sizes, and checksums. Those values can change while the source and
package identity stay the same. The downloader uses the stable package name
for its local cache alias and resolves itch.io source pages when needed. The
Minifantasy destination is version-neutral.

For ZIP packages, the extractor removes a single common top-level archive
directory when present, so release-specific wrapper names do not need to be
recorded. The seven available package mappings were verified against the
current tree as described above. MiniWorldSprites, character-customizer, and
Puny World have now been downloaded and compared as well. The 0x72 packages have no
destination in the current tree.

## Minifantasy graphics definitions

The Minifantasy pack page identifies its art as an 8x8 tileset. The schema-backed
definition is registered at
`graphics/definitions/Minifantasy_ForgottenPlains/atlas.json`; it records the
8x8 grids for the current sheets, shadow layers, legacy sheets, and two static
reference composites. The source notes identify two-frame lake water and
four-frame river animation, but do not map individual tile sequences or
playback timing. The GIFs are animated demonstrations rather than static
sprite sheets, so they are not registered as grid textures.

The scripts use Python 3's standard-library JSON support, HTTP client, and ZIP
reader. They do not depend on `check-jsonschema`, Click, `jq`, `unzip`, or any
third-party Python package. The `check-jsonschema` CLI compatibility issue
therefore does not affect download or extraction.

## Git tracking policy

An audit compared current files with the cached manifest packages using
SHA-256. Ten tracked files are identical to package extraction outputs: three
music license/credit files, six Minifantasy texture license/source-note files,
and the character-customizer ReadMe. They were removed from Git's index;
their local copies remain available and `init.sh` recreates them.

Seven ignored files have no matching extraction output and are now tracked:

- `graphics/textures/misc/HyperMaze.png`;
- `graphics/textures/misc/invaders.png` and `invaders.xcf`;
- `graphics/textures/misc/player.png` and `player-large.png`;
- `graphics/textures/misc/rover.png`;
- `graphics/textures/tilesets/tileset.png`.

The six misc files remain excluded from download and extraction. Keeping them
in Git supplies their existing bytes to a fresh checkout. Fonts, UI resources,
definitions, shaders, and other authored files continue to be tracked.

The ignore rules now name the manifest's generated directories and individual
textures. They ignore every package-provided file, including metadata, while
allowing assets without download mappings to be tracked. The download cache
is ignored, and `.download-manifest/manifest.json` is explicitly retained.

## Completion criteria

- A clean checkout plus the master script reconstructs the same in-scope asset
  paths and bytes as the intended current repository asset tree, including
  ignored local assets, package-provided attribution files, and any documented
  transformations.
- `graphics/textures/misc/` is neither required nor modified by this workflow.
- Every in-scope asset is accounted for as package-supplied, generated or
  repository-owned, manually sourced, or unresolved. Missing sources and
  unverifiable package metadata are reported instead of silently skipped.
- The download and extraction steps can be rerun without corrupting cached
  packages or producing duplicate nested directories.
- The eventual extraction comparison confirms every planned destination and
  byte result before its manifest entry is considered verified.
