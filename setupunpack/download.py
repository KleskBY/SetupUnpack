"""HTTP(S) downloads with guarded resume and an optional expected SHA-256."""
from pathlib import Path
from urllib.parse import urlsplit, unquote
from urllib.request import Request, urlopen
import hashlib
import json
import os
import re
import time

from .engine import Context, UnpackError, relative_path, sha256


def download(url, output, ctx=None, filename='', expected_sha256=''):
    ctx = ctx or Context()
    parts = urlsplit(url.strip())
    if parts.scheme.lower() not in ('http', 'https') or not parts.hostname or parts.username or parts.password:
        raise UnpackError('Enter an HTTP or HTTPS URL without embedded credentials.')
    if expected_sha256 and not re.fullmatch(r'[0-9a-fA-F]{64}', expected_sha256):
        raise UnpackError('Expected SHA-256 must contain 64 hexadecimal characters.')
    filename = filename.strip() or unquote(parts.path.rsplit('/', 1)[-1])
    if not filename:
        raise UnpackError('This URL has no filename. Enter an output filename.')
    name = relative_path(filename)
    if len(name.parts) != 1:
        raise UnpackError('Download filename must be a single filename, without folders.')
    folder = Path(output).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / name
    partial = target.with_name(target.name + '.part')
    metadata = target.with_name(target.name + '.part.json')
    receipt = target.with_name(target.name + '.download.json')
    if target.exists() or receipt.exists():
        raise UnpackError(f'{target.name} already exists. Choose another filename or folder.')
    for path in (partial, metadata):
        if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()):
            raise UnpackError('Download destination is a link.')
    identity = hashlib.sha256(url.encode('utf-8')).hexdigest()
    offset = partial.stat().st_size if partial.exists() else 0
    prior = {}
    headers = {'User-Agent': 'SetupUnpack/1.0', 'Accept-Encoding': 'identity'}
    if partial.exists():
        if not metadata.is_file():
            raise UnpackError('An unrelated .part file exists. Choose another filename.')
        prior = json.loads(metadata.read_text())
        if prior.get('url_sha256') != identity:
            raise UnpackError('Partial download belongs to a different URL.')
        if offset:
            if not prior.get('validator'):
                raise UnpackError('Server supplied no resume validator. Choose another filename to restart safely.')
            headers.update({'Range': f'bytes={offset}-', 'If-Range': prior['validator']})
    ctx.check()
    ctx.log(f'Downloading {filename} from {parts.hostname}' + (f' (resuming at {offset:,} bytes)…' if offset else '…'))
    with urlopen(Request(url, headers=headers), timeout=15) as response:
        if urlsplit(response.url).scheme.lower() not in ('http', 'https'):
            raise UnpackError('Unsupported download redirect.')
        if response.headers.get('Content-Encoding', 'identity').lower() != 'identity':
            raise UnpackError('Unexpected compressed HTTP transfer; cannot verify ranges.')
        length = response.headers.get('Content-Length')
        expected_transfer = int(length) if length is not None else None
        total = offset + expected_transfer if expected_transfer is not None else None
        if offset:
            match = re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)', response.headers.get('Content-Range', ''))
            if response.status != 206 or not match or int(match[1]) != offset or int(match[2]) != int(match[3]) - 1:
                raise UnpackError('Server refused a verified resume. Partial data was kept; choose another filename to restart.')
            total = int(match[3])
            if prior.get('total') is not None and prior['total'] != total:
                raise UnpackError('Remote file size changed during resume.')
            if expected_transfer is not None and expected_transfer != total - offset:
                raise UnpackError('Inconsistent Content-Range and Content-Length.')
        elif response.status != 200:
            raise UnpackError(f'Unexpected download status: HTTP {response.status}')
        if total is not None and total > ctx.max_bytes:
            raise UnpackError('Download exceeds the configured size limit.')
        etag = response.headers.get('ETag', '')
        validator = etag if etag and not etag.startswith('W/') else response.headers.get('Last-Modified', '')
        if offset and validator != prior['validator']:
            raise UnpackError('Remote file validator changed; refusing to combine different versions.')
        state = {'url_sha256': identity, 'host': parts.hostname, 'filename': filename,
                 'validator': validator, 'total': total}
        metadata.write_text(json.dumps(state, indent=2) + '\n')
        received = offset
        next_log = time.monotonic()
        with partial.open('ab' if offset else 'wb') as stream:
            while True:
                ctx.check()
                data = response.read(1024 * 1024)
                if not data:
                    break
                received += len(data)
                if received > ctx.max_bytes or (total is not None and received > total):
                    raise UnpackError('Server sent more bytes than allowed.')
                stream.write(data)
                if time.monotonic() >= next_log:
                    ctx.log(f'{filename}: {received / 1024**2:,.1f} MiB' + (f' / {total / 1024**2:,.1f} MiB' if total else ''))
                    next_log = time.monotonic() + 1
        if total is not None and received != total:
            raise UnpackError('Download ended early. Partial data is available for resume.')
    ctx.check()
    digest = sha256(partial, ctx)
    if expected_sha256 and digest.lower() != expected_sha256.lower():
        raise UnpackError('Downloaded SHA-256 does not match. Data remains marked .part.')
    # Windows rename refuses to overwrite an existing final file.
    if target.exists():
        raise UnpackError('Destination appeared while downloading; refusing to overwrite it.')
    partial.rename(target)
    state.update(status='complete', bytes=target.stat().st_size, sha256=digest,
                 expected_sha256_matched=bool(expected_sha256))
    receipt.write_text(json.dumps(state, indent=2) + '\n')
    metadata.unlink()
    ctx.log(f'Download complete: {target.name} · SHA-256 {digest}')
    return target
