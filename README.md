# SetupUnpack

⚠️ Vibecoding

A standalone Windows app for extracting installers, archives and data-disc images
without running setup. Includes a basic GUI, a command-line interface, bundled
extractors, source code and tests. No game files are included.

## Start

Double-click **Start-SetupUnpack.cmd**, or open
**portable/SetupUnpack/SetupUnpack.exe** directly. The portable build includes
Python and the extraction tools; Python and 7-Zip do not need to be installed.

To move it to another computer, copy the entire `portable/SetupUnpack` folder, or
unzip `release/SetupUnpack-1.0.0-Windows-x64.zip`. Keep `_internal` beside the EXE.
Built for 64-bit Windows; verified locally on Windows 11.

## Extract files

1. Add one or more files on the **Extract files** tab.
2. Choose an output parent folder. Each input gets a new subfolder, so an existing
   game or previous extraction is never overwritten.
3. Leave **Auto-detect** selected, or choose the format explicitly.
4. Use **Inspect selected** to preview the file table, then **Extract queue**.
5. Read the activity log and open the output folder when the job finishes.

The output contains `files/`, a `manifest.json` with SHA-256 values, and an
`extraction.log`. Old InstallShield self-extractors also retain their outer
`installer_files/`. BIN/CUE jobs retain a converted ISO in `image/`.

Cancellation stops the active helper and marks the job incomplete. Partial output
is retained for inspection. The app never opens an extracted program automatically.

## Supported workflows

| Input | Method | Notes |
|---|---|---|
| ZIP, RAR, 7z, CAB, ISO and other recognized archives | Bundled 7-Zip | File table, size, path and available CRC checks |
| Inno Setup EXE | Bundled innoextract 1.9 | Tested with Inno Setup 5.5.7; newer variants may be unsupported |
| InstallShield `data1.cab` and `.hdr` / additional CAB volumes | Bundled Unshield | Keeps file groups and restores legal spaces/brackets in names |
| Old InstallShield self-extracting EXE | Python container reader, then Unshield | Tested with one old-style patch installer; not all InstallShield EXEs use this layout |
| Single-track MODE1/2352 BIN/CUE | Python sector conversion, then 7-Zip | Checks every sector; no mounting or emulator |
| Single-track MODE1/2048 CUE | Verified data copy, then 7-Zip | Track must begin at 00:00:00 |
| HTTP/HTTPS downloads | Python downloader | Guarded resume, size limit, SHA-256 report and optional expected hash |

Mixed audio/data discs, nonzero track starts, MODE2 images, password-protected
archives, unsafe paths and unsupported executable formats stop with a clear error.
The app never falls back to running an installer. MSI/CAB extraction does not
reproduce registry entries, shortcuts, custom actions or other installation logic.
Successful extraction therefore does not guarantee a runnable application.

## Multiple discs

For InstallShield, select `data1.cab` from the first disc. Keep its `.hdr` and CAB
volumes together. If another disc contains additional CAB volumes, use **Add
companion CAB folder**. Conflicting filenames with different contents are rejected.

Extraction keeps the component-group layout. Nested archives are left as files,
except for the main `data1.cab` inside the supported old InstallShield
self-extractor.

## Download

Paste one URL per line on the **Download** tab. For a single URL you may provide a
filename and expected SHA-256. Downloads go into the output folder's `downloads/`
directory. Enable **Extract after downloading** to process the completed files.

Interrupted downloads retain `.part` and `.part.json` files. Repeating the same URL
resumes only when the server supplies a matching ETag or Last-Modified validator
and a valid byte range. If the server cannot resume safely, choose another filename
to start fresh. Completed filenames are never overwritten. URL query strings and
credentials are not written into receipts or logs; receipts identify the URL by hash.

## Command line and development

Source checkout requires Python 3.11+ with Tkinter. The build was tested with
Python 3.14.0 and PyInstaller 6.17.0. Runtime Python code uses only the standard
library; bundled tools are already included.

```powershell
python -m setupunpack gui
python -m setupunpack tools
python -m setupunpack inspect "C:\Packages\setup.exe"
python -m setupunpack extract "C:\Packages\setup.exe" -o "C:\Unpacked"
python -m setupunpack extract "C:\Discs\disc.cue" -o "C:\Unpacked" --convert-only
python -m setupunpack extract "C:\Disc1\data1.cab" -o "C:\Unpacked" --cab-folder "C:\Disc2"
python -m setupunpack download "https://example.org/package.zip" -o "C:\Downloads" --sha256 YOUR_64_CHARACTER_HASH

python -m unittest discover -s tests -v
python scripts/smoke_gui.py
python -m pip install -r requirements-build.txt
python scripts/build_portable.py
```

The portable EXE also accepts `--self-test path-to-report.json` for a hidden GUI
diagnostic. It verifies the bundled tools and performs preview/extraction on a
small ZIP created specifically for the check. Normal launch opens the GUI.

The default limit is 30 GiB expanded or downloaded per operation. Use the GUI limit
field or CLI `--max-gib` to change it. The entry limit is 200,000. Nested layers are
checked individually; allow extra space for temporary copies and the converted ISO.

Code lives in `setupunpack/`; the GUI delegates work to a background thread. Helpers
can only be started through a fixed executable map in `Context.run`, using argument
arrays with no shell. Temporary extraction data is scoped to the new job folder.

See `docs/VALIDATION.md` for recorded test results and `THIRD_PARTY.md` for tool
versions, licenses and upstream source links.
