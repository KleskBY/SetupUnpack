from pathlib import Path
import hashlib
import json
import struct
import tempfile
import threading
import unittest
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import quote

from setupunpack.engine import (Context, Entry, UnpackError, Cancelled, relative_path,
                                inspect, extract, inspect_image, convert_image, pe_overlay, parse_7zip)
from setupunpack.download import download


class Paths(unittest.TestCase):
    def test_traversal_and_windows_special_paths(self):
        for value in ('../escape', '/absolute', 'C:\\escape', '\\\\host\\share', 'ok/../escape',
                      'a//b', 'a/./b', 'a:stream', 'CON.txt', 'x/LPT1', 'x. ', 'x\x00y', 'x|y'):
            with self.subTest(value=value), self.assertRaises(UnpackError):
                relative_path(value)
        self.assertEqual(relative_path('audio/American Wrestler.aud').as_posix(), 'audio/American Wrestler.aud')

    def test_case_collision_and_size_limit(self):
        with self.assertRaises(UnpackError):
            Context().validate_entries([Entry('a.txt'), Entry('A.TXT')])
        with self.assertRaises(UnpackError):
            Context().validate_entries([Entry('a'), Entry('a/b')])
        with self.assertRaises(UnpackError):
            Context(max_bytes=9).validate_entries([Entry('a', 10)])

    def test_link_listing_rejected(self):
        listing = 'Type = zip\n----------\nPath = link\nSize = 1\nSymbolic Link = ..\\escape\n'
        with self.assertRaises(UnpackError):
            parse_7zip(listing)


class Formats(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_real_archive_extraction_and_preserved_input(self):
        archive = self.root / 'example with spaces.zip'
        data = b'package data\0' * 100
        with zipfile.ZipFile(archive, 'w') as stream:
            stream.writestr('folder/file.bin', data)
            stream.writestr('empty/', b'')
        initial = hashlib.sha256(archive.read_bytes()).hexdigest()
        report = extract(archive, self.root / 'out')
        self.assertEqual(report['status'], 'complete')
        self.assertEqual((Path(report['output']) / 'files/folder/file.bin').read_bytes(), data)
        self.assertEqual(hashlib.sha256(archive.read_bytes()).hexdigest(), initial)
        second = extract(archive, self.root / 'out')
        self.assertNotEqual(report['output'], second['output'])
        self.assertFalse(report['executed_input'])

    def test_traversal_archive_never_extracts(self):
        archive = self.root / 'hostile.zip'
        with zipfile.ZipFile(archive, 'w') as stream:
            stream.writestr('../escape.txt', b'escape')
        with self.assertRaises(UnpackError):
            extract(archive, self.root / 'out')
        self.assertFalse((self.root / 'escape.txt').exists())
        manifests = list((self.root / 'out').glob('*/manifest.json'))
        self.assertEqual(json.loads(manifests[0].read_text())['status'], 'failed')

    def test_cancellation_is_reported(self):
        archive = self.root / 'test.zip'
        with zipfile.ZipFile(archive, 'w') as stream:
            stream.writestr('file.txt', b'test')
        event = threading.Event()
        event.set()
        with self.assertRaises(Cancelled):
            extract(archive, self.root / 'out', Context(cancel=event))
        report = json.loads(next((self.root / 'out').glob('*/manifest.json')).read_text())
        self.assertEqual(report['status'], 'cancelled')

    def test_raw_disc_conversion_and_mixed_mode_rejection(self):
        chunks = [bytearray(2048) for _ in range(20)]
        chunks[16][:7] = b'\x01CD001\x01'
        struct.pack_into('<I', chunks[16], 80, 20)
        struct.pack_into('>I', chunks[16], 84, 20)
        struct.pack_into('<H', chunks[16], 128, 2048)
        struct.pack_into('>H', chunks[16], 130, 2048)
        raw = b''.join(b'\0' + b'\xff' * 10 + b'\0' + b'\0\0\0\x01' + c + bytes(288) for c in chunks)
        image = self.root / 'disc.bin'
        image.write_bytes(raw)
        cue = self.root / 'disc.cue'
        cue.write_text('FILE "disc.bin" BINARY\n  TRACK 01 MODE1/2352\n  INDEX 01 00:00:00\n')
        info = inspect_image(cue)
        target = self.root / 'disc.iso'
        convert_image(info, target, Context())
        self.assertEqual(target.read_bytes(), b''.join(chunks))
        cue.write_text(cue.read_text() + '  TRACK 02 AUDIO\n  INDEX 01 00:01:00\n')
        with self.assertRaises(UnpackError):
            inspect_image(cue)
        broken = bytearray(raw)
        broken[2352] = 1
        image.write_bytes(broken)
        with self.assertRaises(UnpackError):
            convert_image(info, self.root / 'broken.iso', Context())

    def test_old_installshield_overlay_bounds(self):
        header = bytearray(512)
        header[:2] = b'MZ'
        struct.pack_into('<I', header, 60, 128)
        header[128:132] = b'PE\0\0'
        struct.pack_into('<H', header, 134, 1)
        struct.pack_into('<H', header, 148, 0)
        struct.pack_into('<II', header, 168, 320, 192)
        payload = b'hello'
        package = header + b'file.txt\0Disk1\\file.txt\0\x005\0' + payload
        source = self.root / 'synthetic.exe'
        source.write_bytes(package)
        entries = pe_overlay(source)
        self.assertEqual(entries[0].size, 5)
        self.assertEqual(source.read_bytes()[entries[0].offset:], payload)
        source.write_bytes(package[:-1])
        self.assertIsNone(pe_overlay(source))
        source.write_bytes(header + b'file.txt\0..\\file.txt\0\x005\0hello')
        with self.assertRaises(UnpackError):
            pe_overlay(source)


class Downloads(unittest.TestCase):
    def test_http_download_resume_and_expected_hash(self):
        data = bytes(range(256)) * 1024
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                start = int(self.headers.get('Range', 'bytes=0-')[6:].split('-')[0])
                self.send_response(206 if start else 200)
                self.send_header('Content-Length', str(len(data) - start))
                self.send_header('ETag', '"fixture-v1"')
                if start:
                    self.send_header('Content-Range', f'bytes {start}-{len(data)-1}/{len(data)}')
                self.end_headers()
                self.wfile.write(data[start:])
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as temp:
                folder = Path(temp)
                url = f'http://127.0.0.1:{server.server_port}/file.bin'
                (folder / 'file.bin.part').write_bytes(data[:100])
                (folder / 'file.bin.part.json').write_text(json.dumps({'url_sha256': hashlib.sha256(url.encode()).hexdigest(), 'validator': '"fixture-v1"', 'total': len(data)}))
                target = download(url, folder, expected_sha256=hashlib.sha256(data).hexdigest())
                self.assertEqual(target.read_bytes(), data)
                self.assertFalse((folder / 'file.bin.part').exists())
                with self.assertRaises(UnpackError):
                    download(url, folder)
                with self.assertRaises(UnpackError):
                    download(url, folder, filename='bad.bin', expected_sha256='0' * 64)
                self.assertFalse((folder / 'bad.bin').exists())
                self.assertTrue((folder / 'bad.bin.part').exists())
        finally:
            server.shutdown()
            server.server_close()


if __name__ == '__main__':
    unittest.main()
