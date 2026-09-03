from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import mmap
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
import zlib


class UnpackError(Exception):
    pass


class Cancelled(UnpackError):
    pass


def resource_root() -> Path:
    return Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent.parent))


def sha256(path: Path, ctx=None) -> str:
    result = hashlib.sha256()
    with path.open('rb') as stream:
        while block := stream.read(4 * 1024 * 1024):
            if ctx:
                ctx.check()
            result.update(block)
    return result.hexdigest()


def relative_path(value: str) -> Path:
    """Validate Windows paths even when tests run on a different OS."""
    value = value.replace('\\', '/')
    if value.endswith('/'):
        value = value[:-1]
    parts = value.split('/')
    for part in parts:
        if (not part or part in ('.', '..') or part.endswith((' ', '.'))
                or any(ord(c) < 32 or c in '<>:"|?*' for c in part)
                or re.match(r'(?i)^(CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])(?:\.|$)', part)):
            raise UnpackError(f'Unsafe archive path: {value!r}')
    return Path(*parts)


def safe_target(root: Path, relative: str) -> Path:
    result = root / relative_path(relative)
    if not result.resolve().is_relative_to(root.resolve()):
        raise UnpackError(f'Path escapes the output folder: {relative}')
    return result


def regular_files(root: Path):
    for directory, dirs, files in os.walk(root, followlinks=False):
        for name in dirs + files:
            path = Path(directory) / name
            if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()):
                raise UnpackError(f'Unexpected link in extracted files: {path}')
        for name in files:
            path = Path(directory) / name
            if not path.is_file():
                raise UnpackError(f'Unexpected extracted object: {path}')
            yield path


@dataclass
class Entry:
    path: str
    size: int = 0
    directory: bool = False
    checksum: str = ''
    algorithm: str = ''
    offset: int = 0
    raw_path: str = ''


@dataclass
class Inspection:
    source: Path
    kind: str
    entries: list[Entry]
    detail: str = ''
    image_source: Path | None = None
    sector_size: int = 0

    def summary(self):
        files = [entry for entry in self.entries if not entry.directory]
        return {'source': str(self.source), 'format': self.kind, 'detail': self.detail,
                'file_count': len(files), 'expanded_bytes': sum(e.size for e in files)}


class Context:
    """Only three fixed, checksum-checked helper executables can be launched."""
    executables = {'7zip': '7zip/7z.exe', 'inno': 'innoextract/innoextract.exe',
                   'unshield': 'unshield/unshield.exe'}

    def __init__(self, callback=None, cancel=None, max_bytes=30 * 1024**3,
                 max_files=200000, tool_root=None):
        self.callback = callback or (lambda message: None)
        self.cancel = cancel or threading.Event()
        self.max_bytes = max_bytes
        self.max_files = max_files
        self.tool_root = Path(tool_root) if tool_root else resource_root() / 'vendor'
        self.log_path = None
        self.warnings = []
        self._verified = False

    def check(self):
        if self.cancel.is_set():
            raise Cancelled('Cancelled. Partial output is retained with an incomplete report.')

    def log(self, message):
        self.callback(message)
        if self.log_path:
            with self.log_path.open('a', encoding='utf-8') as stream:
                stream.write(message + '\n')

    def verify_tools(self):
        manifest = json.loads((self.tool_root / 'manifest.json').read_text())
        for name, expected in manifest['files'].items():
            path = safe_target(self.tool_root, name)
            if not path.is_file() or sha256(path, self) != expected:
                raise UnpackError(f'Bundled tool missing or changed: {name}. Restore it from the original tool package.')
        self._verified = True
        return manifest['versions']

    def run(self, tool, args, allow_failure=False):
        self.check()
        if not self._verified:
            self.verify_tools()
        executable = self.tool_root / self.executables[tool]
        self.log(f'Using {tool} to read or unpack files…')
        # The selected installer is always an argument, never a program to launch.
        # The helper's directory is its working directory, never the input's folder.
        with tempfile.TemporaryFile() as capture:
            process = subprocess.Popen([str(executable), *map(str, args)], shell=False,
                                       cwd=executable.parent, stdin=subprocess.DEVNULL,
                                       stdout=capture, stderr=subprocess.STDOUT,
                                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            try:
                while process.poll() is None:
                    self.check()
                    if capture.tell() > 64 * 1024**2:
                        raise UnpackError('Tool output exceeded 64 MiB; stopped parsing.')
                    self.cancel.wait(0.1)
            except BaseException:
                process.kill()
                process.wait()
                raise
            capture.seek(0)
            output = capture.read(64 * 1024**2 + 1).decode('utf-8-sig', errors='replace')
        if self.log_path:
            with self.log_path.open('a', encoding='utf-8') as stream:
                stream.write(output + '\n')
        if process.returncode and not allow_failure:
            raise UnpackError(f'{tool} failed (exit {process.returncode}).\n{output[-3500:]}')
        self.check()
        return process.returncode, output.replace('\r\n', '\n')

    def validate_entries(self, entries):
        if len(entries) > self.max_files:
            raise UnpackError(f'Too many entries: limit is {self.max_files:,}.')
        seen = {}
        total = 0
        for entry in entries:
            key = relative_path(entry.path).as_posix().casefold()
            if key in seen:
                raise UnpackError(f'Duplicate or case-colliding archive path: {entry.path}')
            seen[key] = entry.directory
            if entry.size < 0:
                raise UnpackError('Negative file size in archive listing.')
            if not entry.directory:
                total += entry.size
        for key in seen:
            parts = key.split('/')
            for i in range(1, len(parts)):
                parent = '/'.join(parts[:i])
                if parent in seen and not seen[parent]:
                    raise UnpackError(f'File/directory path collision: {key}')
        if total > self.max_bytes:
            raise UnpackError(f'Expanded size {total / 1024**3:.2f} GiB exceeds the configured limit.')
        return total


def parse_7zip(text):
    text = text.replace('\r\n', '\n')
    if '----------\n' not in text:
        raise UnpackError('7-Zip did not return an archive file table.')
    header, body = text.split('----------\n', 1)
    match = re.search(r'^Type = (.+)$', header, re.M)
    kind = match[1] if match else 'unknown'
    if kind in ('PE', 'ELF', 'MachO'):
        raise UnpackError('This executable has no supported extractable installer payload. It will not be run.')
    entries = []
    for block in re.split(r'\n\s*\n', body.strip()):
        record = dict(line.split(' = ', 1) for line in block.splitlines() if ' = ' in line)
        if 'Path' not in record:
            continue
        if (record.get('Symbolic Link') or record.get('Hard Link') or record.get('Reparse')
                or record.get('Anti') == '+' or record.get('Alternate Stream') == '+'
                or re.search(r'\bl[rwx-]{9}', record.get('Attributes', ''))):
            raise UnpackError('Links, alternate streams and deletion entries are not supported.')
        if record.get('Encrypted') == '+':
            raise UnpackError('Password-protected archives are not supported in this version.')
        directory = record.get('Folder') == '+' or record.get('Attributes', '').startswith('D')
        size = record.get('Size', '')
        if not directory and not size.isdecimal():
            raise UnpackError('Archive listing lacks a file size; cannot verify extraction.')
        entries.append(Entry(record['Path'], int(size or 0), directory,
                             record.get('CRC', ''), 'crc32' if record.get('CRC') else ''))
    return kind, entries


def parse_inno(text):
    entries = []
    for line in text.splitlines():
        match = re.fullmatch(r'(\d+) (SHA-1|SHA-256|MD5|CRC32) ([0-9a-fA-F]+) (.+)', line)
        if match:
            entries.append(Entry(match[4], int(match[1]), False, match[3],
                                 match[2].lower().replace('-', '')))
        elif line.endswith(('\\', '/')):
            entries.append(Entry(line, directory=True))
        elif line.strip():
            raise UnpackError(f'Unsupported Inno listing line: {line[:180]}')
    if not entries:
        raise UnpackError('No readable Inno Setup file table found.')
    return entries


def parse_unshield(text):
    entries = []
    raw_seen = set()
    for line in text.splitlines():
        match = re.fullmatch(r'\s*(\d+)  (.+)', line)
        if not match:
            continue
        original = match[2].replace('\\', '/')
        # Unshield sanitizes directory names even with -R in this Windows build.
        # Restore legal spaces/brackets; angle brackets stay replaced for Windows.
        parts = original.split('/')
        raw_parts = [p.translate(str.maketrans({c: '_' for c in ' <>[]'})) for p in parts[:-1]] + parts[-1:]
        raw = '/'.join(raw_parts)
        logical = original.translate(str.maketrans({'<': '_', '>': '_'}))
        relative_path(logical)
        relative_path(raw)
        if raw.casefold() in raw_seen:
            raise UnpackError(f'InstallShield names collide after extractor normalization: {original}')
        raw_seen.add(raw.casefold())
        entries.append(Entry(logical, int(match[1]), raw_path=raw))
    if not entries:
        raise UnpackError('No InstallShield cabinet file table found.')
    return entries


def pe_overlay(path):
    """Read the old InstallShield four-string/raw-file overlay, using bounded fields."""
    with path.open('rb') as stream:
        if path.stat().st_size < 128:
            return None
        with mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) as data:
            try:
                if data[:2] != b'MZ':
                    return None
                pe = struct.unpack_from('<I', data, 60)[0]
                if pe + 24 > len(data) or data[pe:pe + 4] != b'PE\0\0':
                    return None
                count = struct.unpack_from('<H', data, pe + 6)[0]
                optional = struct.unpack_from('<H', data, pe + 20)[0]
                if not 1 <= count <= 96 or pe + 24 + optional + count * 40 > len(data):
                    return None
                cursor = pe + 24 + optional + count * 40
                for i in range(count):
                    size, offset = struct.unpack_from('<II', data, pe + 24 + optional + i * 40 + 16)
                    if offset + size > len(data):
                        return None
                    cursor = max(cursor, offset + size)
                entries = []
                while cursor < len(data):
                    fields = []
                    for _ in range(4):
                        end = data.find(b'\0', cursor, min(len(data), cursor + 2048))
                        if end < 0:
                            return None
                        value = data[cursor:end]
                        if any(byte < 32 or byte > 126 for byte in value):
                            return None
                        fields.append(value.decode('ascii'))
                        cursor = end + 1
                    name, stored, version, length = fields
                    if not length.isdecimal() or len(length) > 16:
                        return None
                    size = int(length)
                    if cursor + size > len(data) or not name or stored.replace('\\', '/').split('/')[-1] != name:
                        return None
                    relative_path(stored)
                    entries.append(Entry(stored, size, offset=cursor))
                    if len(entries) > 200000:
                        raise UnpackError('Too many embedded installer files.')
                    cursor += size
                return entries or None
            except struct.error:
                return None


def inspect_image(source):
    image = source
    sector = 2352
    if source.suffix.lower() == '.cue':
        text = source.read_text(encoding='utf-8-sig')
        tracks = re.findall(r'^\s*TRACK\s+\d+\s+(\S+)\s*$', text, re.I | re.M)
        files = re.findall(r'^\s*FILE\s+(?:"([^"]+)"|(\S+))\s+BINARY\s*$', text, re.I | re.M)
        indexes = re.findall(r'^\s*INDEX\s+(\d+)\s+(\S+)\s*$', text, re.I | re.M)
        if (len(tracks) != 1 or tracks[0].upper() not in ('MODE1/2352', 'MODE1/2048')
                or len(files) != 1 or indexes != [('01', '00:00:00')]
                or re.search(r'^\s*(PREGAP|POSTGAP)\b', text, re.I | re.M)):
            raise UnpackError('Only one MODE1/2352 or MODE1/2048 data track at 00:00:00 is supported. Mixed-mode/audio discs are not converted.')
        image = safe_target(source.parent, files[0][0] or files[0][1])
        sector = int(tracks[0].split('/')[1])
    if not image.is_file() or image.is_symlink():
        raise UnpackError('The CUE data file is missing or is a link.')
    size = image.stat().st_size
    if size == 0 or size % sector:
        raise UnpackError('Incomplete or empty disc sector data.')
    offset = 16 if sector == 2352 else 0
    with image.open('rb') as stream:
        stream.seek(16 * sector + offset)
        descriptor = stream.read(2048)
    if descriptor[:7] != b'\x01CD001\x01':
        raise UnpackError('No ISO 9660 primary volume descriptor in the data track.')
    sectors = struct.unpack_from('<I', descriptor, 80)[0]
    if (sectors != struct.unpack_from('>I', descriptor, 84)[0]
            or struct.unpack_from('<H', descriptor, 128)[0] != 2048
            or struct.unpack_from('>H', descriptor, 130)[0] != 2048
            or sectors > size // sector):
        raise UnpackError('Invalid ISO volume size or block size.')
    return Inspection(source, 'bin-cue', [Entry(source.stem + '.iso', size // sector * 2048)],
                      f'Single MODE1/{sector} track · {descriptor[40:72].decode("ascii", errors="replace").strip()}', image, sector)


def inspect(source, ctx=None, mode='auto'):
    ctx = ctx or Context()
    source = Path(source).resolve(strict=True)
    if source.is_dir():
        source = source / 'data1.cab'
    if not source.is_file():
        raise UnpackError('Choose a file, or a folder containing data1.cab.')
    ctx.log(f'Inspecting {source.name}…')
    if mode == 'bin-cue' or (mode == 'auto' and source.suffix.lower() in ('.cue', '.bin')):
        result = inspect_image(source)
    else:
        with source.open('rb') as stream:
            signature = stream.read(4)
        overlay = pe_overlay(source) if signature[:2] == b'MZ' and mode in ('auto', 'installshield-exe') else None
        if overlay:
            result = Inspection(source, 'installshield-exe', overlay, 'Old InstallShield self-extractor; files are embedded as data')
        elif mode == 'installshield-exe':
            raise UnpackError('This is not a supported old InstallShield self-extractor.')
        elif mode == 'installshield-cab' or (mode == 'auto' and signature == b'ISc('):
            _, text = ctx.run('unshield', ['l', source])
            result = Inspection(source, 'installshield-cab', parse_unshield(text), 'InstallShield cabinet file groups')
        else:
            inno = None
            if mode == 'inno' or (mode == 'auto' and signature[:2] == b'MZ'):
                code, text = ctx.run('inno', ['--list', '--list-sizes', '--list-checksums', '--silent', '--no-extract-unknown',
                                              '--collisions=rename', '--color=0', '--progress=0', source], True)
                if code == 0:
                    inno = parse_inno(text)
                elif mode == 'inno':
                    raise UnpackError(f'Inno Setup could not read this installer.\n{text[-1500:]}')
            if inno:
                result = Inspection(source, 'inno', inno, 'Inno Setup; installer actions are not applied')
            else:
                code, text = ctx.run('7zip', ['l', '-slt', '-sccUTF-8', source], True)
                if code not in (0, 1):
                    raise UnpackError(f'Unsupported or damaged archive.\n{text[-1800:]}')
                kind, entries = parse_7zip(text)
                result = Inspection(source, 'archive', entries, kind)
    ctx.validate_entries(result.entries)
    return result


def verify_output(root, entries, ctx):
    expected = set()
    for index, entry in enumerate(entries):
        ctx.check()
        path = safe_target(root, entry.path)
        if entry.directory:
            if not path.is_dir():
                raise UnpackError(f'Missing extracted directory: {entry.path}')
            continue
        expected.add(relative_path(entry.path).as_posix().casefold())
        if not path.is_file() or path.is_symlink() or path.stat().st_size != entry.size:
            raise UnpackError(f'Missing file or size mismatch: {entry.path}. Check the log and antivirus history; blocked files are not restored.')
        if entry.checksum:
            value = 0 if entry.algorithm == 'crc32' else hashlib.new(entry.algorithm)
            with path.open('rb') as stream:
                while block := stream.read(4 * 1024**2):
                    ctx.check()
                    if entry.algorithm == 'crc32':
                        value = zlib.crc32(block, value)
                    else:
                        value.update(block)
            actual = f'{value:08x}' if entry.algorithm == 'crc32' else value.hexdigest()
            if actual.lower() != entry.checksum.lower():
                raise UnpackError(f'Checksum mismatch: {entry.path}')
        if index and index % 1000 == 0:
            ctx.log(f'Verified {index:,} entries…')
    actual = {p.relative_to(root).as_posix().casefold() for p in regular_files(root)}
    if actual != expected:
        raise UnpackError('Extracted file set differs from the archive listing.')


def convert_image(info, target, ctx):
    sync = b'\0' + b'\xff' * 10 + b'\0'
    sector_count = 0
    with info.image_source.open('rb') as reader, target.open('xb') as writer:
        while block := reader.read(info.sector_size * 4096):
            ctx.check()
            if info.sector_size == 2048:
                writer.write(block)
                sector_count += len(block) // 2048
                continue
            converted = bytearray()
            for offset in range(0, len(block), 2352):
                sector = block[offset:offset + 2352]
                if len(sector) != 2352 or sector[:12] != sync or sector[15] != 1:
                    raise UnpackError(f'Invalid MODE1/2352 sector {sector_count}; conversion stopped.')
                converted.extend(sector[16:2064])
                sector_count += 1
            writer.write(converted)
    if target.stat().st_size != info.entries[0].size:
        raise UnpackError('Converted ISO size mismatch.')
    ctx.log(f'Converted and checked {sector_count:,} sectors.')


def extract_overlay(info, destination, ctx):
    destination.mkdir()
    with info.source.open('rb') as reader:
        for entry in info.entries:
            ctx.check()
            target = safe_target(destination, entry.path)
            target.parent.mkdir(parents=True, exist_ok=True)
            reader.seek(entry.offset)
            remaining = entry.size
            digest = hashlib.sha256()
            with target.open('xb') as writer:
                while remaining:
                    ctx.check()
                    block = reader.read(min(4 * 1024**2, remaining))
                    if not block:
                        raise UnpackError('Truncated installer payload.')
                    writer.write(block)
                    digest.update(block)
                    remaining -= len(block)
            if sha256(target, ctx) != digest.hexdigest():
                raise UnpackError('Embedded file copy did not verify.')
    verify_output(destination, info.entries, ctx)


def cabinet_source(source, folders, scratch, ctx):
    if not folders:
        return source
    staging = scratch / 'cabinets'
    staging.mkdir()
    for folder in [source.parent, *map(Path, folders)]:
        if not folder.is_dir():
            raise UnpackError(f'Cabinet folder does not exist: {folder}')
        for path in sorted(folder.iterdir()):
            if path.suffix.lower() not in ('.cab', '.hdr') or not path.is_file():
                continue
            ctx.check()
            destination = safe_target(staging, path.name)
            if destination.exists():
                if sha256(path, ctx) != sha256(destination, ctx):
                    raise UnpackError(f'Different cabinet files share the name {path.name}.')
            else:
                ctx.log(f'Staging companion {path.name}…')
                shutil.copy2(path, destination)
    return staging / source.name


def extract_cab(info, destination, scratch, ctx, extra_folders=()):
    source = cabinet_source(info.source, extra_folders, scratch, ctx)
    raw = scratch / 'unshield-output'
    raw.mkdir()
    ctx.run('unshield', ['-R', '-d', raw, 'x', source])
    raw_entries = [Entry(e.raw_path, e.size) for e in info.entries]
    verify_output(raw, raw_entries, ctx)
    destination.mkdir()
    output_entries = [Entry(entry.path, entry.size) for entry in info.entries]
    if not output_entries:
        raise UnpackError('The cabinet contains no files.')
    ctx.validate_entries(output_entries)
    for entry in info.entries:
        source_file = safe_target(raw, entry.raw_path)
        target = safe_target(destination, entry.path)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_file, target)
    verify_output(destination, output_entries, ctx)


def unique_job(parent, stem):
    parent.mkdir(parents=True, exist_ok=True)
    stem = re.sub(r'[^\w .-]', '_', stem).strip(' .')[:80] or 'extraction'
    stem = 'unpacked_' + stem
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    for number in range(10000):
        job = parent / f'{stem}_{stamp}{"_" + str(number) if number else ""}'
        try:
            job.mkdir()
            return job
        except FileExistsError:
            continue
    raise UnpackError('Could not create a unique output folder.')


def extract(source, output, ctx=None, mode='auto', convert_only=False, extra_folders=()):
    ctx = ctx or Context()
    ctx.warnings = []
    job = unique_job(Path(output).resolve(), Path(source).stem)
    ctx.log_path = job / 'extraction.log'
    report = {'version': 1, 'status': 'incomplete', 'started_utc': datetime.now(timezone.utc).isoformat(),
              'source': str(Path(source).resolve()), 'output': str(job), 'executed_input': False,
              'files': []}
    def save():
        (job / 'manifest.json').write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    save()
    try:
        report['tools'] = ctx.verify_tools()
        info = inspect(source, ctx, mode)
        report.update(info.summary())
        ctx.log(f'Detected {info.kind}: {len(info.entries):,} entries.')
        expanded = ctx.validate_entries(info.entries)
        if shutil.disk_usage(job).free < expanded * (2 if info.kind == 'installshield-cab' else 1) + 64 * 1024**2:
            raise UnpackError('Not enough free space for the listed files and temporary extraction data.')
        inputs = [info.source]
        if info.image_source and info.image_source != info.source:
            inputs.append(info.image_source)
        if info.kind == 'installshield-cab':
            for folder in [info.source.parent, *map(Path, extra_folders)]:
                if folder.is_dir():
                    inputs.extend(p.resolve() for p in folder.iterdir()
                                  if p.is_file() and p.suffix.lower() in ('.cab', '.hdr'))
        elif info.kind == 'inno':
            inputs.extend(p.resolve() for p in info.source.parent.glob(info.source.stem + '*.bin') if p.is_file())
        inputs = list(dict.fromkeys(inputs))
        report['inputs'] = []
        for path in inputs:
            ctx.log(f'Hashing source {path.name}…')
            report['inputs'].append({'path': str(path), 'bytes': path.stat().st_size, 'sha256': sha256(path, ctx)})
        save()
        with tempfile.TemporaryDirectory(prefix='_work_', dir=job) as temporary:
            scratch = Path(temporary)
            destination = job / 'files'
            if info.kind == 'installshield-exe':
                outer = job / 'installer_files'
                extract_overlay(info, outer, ctx)
                cabinets = [p for p in regular_files(outer) if p.name.lower() == 'data1.cab']
                if cabinets:
                    if len(cabinets) != 1:
                        raise UnpackError('More than one data1.cab was embedded; extract cabinets individually.')
                    cabinet = inspect(cabinets[0], ctx)
                    ctx.validate_entries(cabinet.entries)
                    if shutil.disk_usage(job).free < sum(e.size for e in cabinet.entries) * 2 + 64 * 1024**2:
                        raise UnpackError('Not enough free space for the embedded cabinet payload.')
                    if cabinet.kind != 'installshield-cab':
                        raise UnpackError('Embedded cabinet is not an InstallShield cabinet.')
                    extract_cab(cabinet, destination, scratch, ctx, extra_folders)
            elif info.kind == 'installshield-cab':
                extract_cab(info, destination, scratch, ctx, extra_folders)
            elif info.kind == 'bin-cue':
                iso_folder = job / 'image'
                iso_folder.mkdir()
                iso = safe_target(iso_folder, info.entries[0].path)
                convert_image(info, iso, ctx)
                if not convert_only:
                    image_info = inspect(iso, ctx, 'archive')
                    ctx.validate_entries(image_info.entries)
                    if shutil.disk_usage(job).free < sum(e.size for e in image_info.entries) + 64 * 1024**2:
                        raise UnpackError('Not enough free space to extract the converted ISO.')
                    destination.mkdir()
                    code, _ = ctx.run('7zip', ['x', '-y', '-bd', '-bsp0', '-sccUTF-8', f'-o{destination}', iso], True)
                    if code not in (0, 1):
                        raise UnpackError(f'ISO extraction failed (exit {code}).')
                    if code == 1:
                        ctx.warnings.append('7-Zip reported a warning; see extraction.log.')
                    verify_output(destination, image_info.entries, ctx)
            elif info.kind == 'inno':
                destination.mkdir()
                ctx.run('inno', ['--extract', '--test', '--collisions=rename', '--no-extract-unknown', '--color=0', '--progress=0',
                                 '--quiet', '--output-dir', destination, info.source])
                verify_output(destination, info.entries, ctx)
            else:
                destination.mkdir()
                code, _ = ctx.run('7zip', ['x', '-y', '-bd', '-bsp0', '-sccUTF-8', f'-o{destination}', info.source], True)
                if code not in (0, 1):
                    raise UnpackError(f'Archive extraction failed (exit {code}).')
                if code == 1:
                    ctx.warnings.append('7-Zip reported a warning; see extraction.log.')
                verify_output(destination, info.entries, ctx)
        ctx.log('Writing SHA-256 manifest and verifying the original source…')
        for subfolder in ('files', 'installer_files', 'image'):
            folder = job / subfolder
            if folder.exists():
                for index, path in enumerate(regular_files(folder)):
                    report['files'].append({'path': path.relative_to(job).as_posix(),
                                            'bytes': path.stat().st_size, 'sha256': sha256(path, ctx)})
                    if index and index % 1000 == 0:
                        ctx.log(f'Hashed {index:,} files in {subfolder}…')
        for item in report['inputs']:
            if sha256(Path(item['path']), ctx) != item['sha256']:
                raise UnpackError('Source changed during extraction.')
        report.update(status='complete', source_unchanged=True, warnings=ctx.warnings,
                      completed_utc=datetime.now(timezone.utc).isoformat(),
                      extracted_file_count=len(report['files']), extracted_bytes=sum(f['bytes'] for f in report['files']))
        save()
        ctx.log(f'Complete: {len(report["files"]):,} files. Output: {job}')
        return report
    except BaseException as error:
        report['status'] = 'cancelled' if isinstance(error, Cancelled) else 'failed'
        report['error'] = str(error)
        save()
        ctx.log(str(error))
        raise
