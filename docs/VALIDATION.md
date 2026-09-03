# Validation record

Tested on Windows 11 x64 with Python 3.14.0. No original installer, extracted
launcher or game executable was run by these checks.

## Automated checks

`python -m unittest discover -s tests -v`: **9 tests passed**.

- Reject path traversal, absolute paths, Windows device names and alternate streams.
- Reject duplicate/case-colliding paths, file/directory collisions and oversized output.
- Reject archive links before extraction.
- Inspect and extract a real ZIP with 7-Zip; verify content and preserve the input.
- Refuse a hostile ZIP and retain a failed manifest.
- Mark cancellation correctly.
- Convert synthetic MODE1/2352 sectors exactly; reject mixed-mode CUE and bad sectors.
- Parse a synthetic old InstallShield overlay; reject truncation and traversal.
- Resume a local HTTP fixture with a matching validator; verify SHA-256, reject
  overwrites and retain a mismatched download as partial.

The Tk GUI smoke test exercises its actual worker queue and controls: add a source,
inspect, populate the file preview, extract, and restore controls after completion.
The portable executable includes the same diagnostic through `--self-test`.
`portable-self-test.json` records the packaged executable's result.

## Real input checks

The original user-provided files remain outside this project and are not bundled.

| Input | Result |
|---|---|
| Large Inno Setup 5.5.7 installer (game mod) | 7,574 files, 1,449,976,626 bytes; all stored SHA-1 values and sizes matched; input SHA-256 unchanged |
| Old InstallShield self-extracting patch installer | All 9 outer files parsed to exact end-of-file; cabinet payload extracted successfully |
| Single-track MODE1/2352 BIN/CUE disc image | 331,736 sectors checked; converted ISO is 679,395,328 bytes and matches the earlier conversion SHA-256 |
| Multi-part RAR archive of a disc | 3 files extracted; sizes/CRCs verified and input SHA-256 unchanged |

The Inno result also received a SHA-256 manifest for all extracted files. Installer
actions, game launch, patch installation and disc emulation are outside these tests.

The Extract, Download and About tabs were visually inspected. Preview and extract
were exercised through the GUI. The packaged app was checked separately from the
source checkout. This does not establish compatibility with every installer format
or a clean Windows machine; unsupported inputs remain explicit errors.
