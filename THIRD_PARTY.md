# Bundled tools and sources

SetupUnpack's own Python source is MIT licensed. The unmodified extractor
binaries are separate programs with their own licenses and source distributions.

| Component | Version | Source | License notice |
|---|---|---|---|
| 7-Zip (`7z.exe`, `7z.dll`) | 25.01 | https://www.7-zip.org/ and https://www.7-zip.org/a/7z2501-src.tar.xz | `vendor/7zip/License.txt`; LGPL with the stated BSD and unRAR conditions |
| innoextract | 1.9 | https://constexpr.org/innoextract/ and https://github.com/dscharrer/innoextract/tree/1.9 | `vendor/innoextract/license/`, including bundled library notices |
| Unshield | 1.6.2 | https://github.com/twogood/unshield/tree/1.6.2 | `vendor/unshield/unshield.LICENSE.txt` (MIT) |
| zlib runtime for Unshield | as supplied by the Unshield Windows package | https://github.com/madler/zlib | `vendor/unshield/zlib.LICENSE.txt` |
| Python / Tcl / Tk | supplied by the Python build environment | https://www.python.org/ and https://www.tcl.tk/ | Python license at the portable root; Tcl/Tk notices in the bundled runtime |
| PyInstaller bootloader | 6.17.0 build tool | https://pyinstaller.org/ | GPL with the bootloader distribution exception; see https://pyinstaller.org/en/stable/license.html |

The Unshield Windows package used here is maintained by Scoop:
https://raw.githubusercontent.com/ScoopInstaller/Binary/master/unshield/unshield-1.6.2-x64.7z

Its original package SHA-256 is
`662fefefc6988a3744ddc6b5b8d2e96ac21876d532f7d23a597c4ca797764a6b`.
The innoextract Windows package MD5 matched its author's published value:
`72d0d0dd874b6236eaa44411f4470ee1`.

`vendor/manifest.json` records SHA-256 for each executable and DLL. The app checks
these before calling a helper. In the portable build, vendor/ is in `_internal/`.
The binaries can be replaced when maintaining the project, provided their source,
license notices, version metadata and verified hashes are updated together.

The old InstallShield container layout was researched using
https://github.com/hifi/iss_extract. MODE1 disc sector layout was checked against
https://github.com/hessu/bchunk/blob/master/bchunk.c. The Python implementations
were written for this project; neither reference program is bundled.
