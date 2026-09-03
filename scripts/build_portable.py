"""Build the Windows GUI and a ZIP containing its complete portable folder."""
from pathlib import Path
import hashlib
import json
import shutil
import subprocess
import sys
import zipfile


def main():
    project = Path(__file__).resolve().parent.parent
    distribution = project / 'portable'
    build_work = project / 'work' / 'build'
    for path in (distribution, build_work):
        if not path.resolve().is_relative_to(project.resolve()):
            raise ValueError('Build output escapes the project.')
        path.mkdir(parents=True, exist_ok=True)
    app = distribution / 'SetupUnpack'
    if app.exists() and (app.is_symlink() or app.is_junction()):
        raise ValueError('Build destination must not be a link.')
    subprocess.run([
        sys.executable, '-m', 'PyInstaller', '--noconfirm', '--onedir', '--windowed',
        '--name', 'SetupUnpack', '--distpath', str(distribution),
        '--workpath', str(build_work), '--specpath', str(project / 'work'),
        '--add-data', f'{project / "vendor"};vendor',
        str(project / 'launch_gui.pyw')
    ], cwd=project, check=True)
    for name in ('README.md', 'LICENSE', 'THIRD_PARTY.md'):
        shutil.copy2(project / name, app / name)
    shutil.copytree(project / 'docs', app / 'docs', dirs_exist_ok=True)
    # Python's redistribution notice accompanies the bundled interpreter.
    python_license = Path(sys.base_prefix) / 'LICENSE.txt'
    if python_license.is_file():
        shutil.copy2(python_license, app / 'PYTHON-LICENSE.txt')
    release = project / 'release'
    release.mkdir(exist_ok=True)
    archive = release / 'SetupUnpack-1.0.0-Windows-x64.zip'
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
        for path in sorted(app.rglob('*')):
            if path.is_file():
                bundle.write(path, path.relative_to(distribution).as_posix())
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (release / 'SHA256SUMS.txt').write_text(f'{digest}  {archive.name}\n', encoding='ascii')
    print(json.dumps({'application': str(app / 'SetupUnpack.exe'), 'archive': str(archive),
                      'archive_bytes': archive.stat().st_size, 'sha256': digest}, indent=2))


if __name__ == '__main__':
    main()
