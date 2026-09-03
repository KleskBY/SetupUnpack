import argparse
from dataclasses import asdict
import json
import math
import sys

from .engine import Context, UnpackError, extract, inspect
from .download import download


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
    parser = argparse.ArgumentParser(description='Extract installers and disc images without running their contents.')
    sub = parser.add_subparsers(dest='command')
    sub.add_parser('gui', help='Open the desktop interface')
    sub.add_parser('tools', help='Verify bundled extractor checksums')
    for name in ('inspect', 'extract'):
        command = sub.add_parser(name)
        command.add_argument('source')
        command.add_argument('--mode', default='auto', choices=('auto', 'archive', 'inno', 'installshield-cab', 'installshield-exe', 'bin-cue'))
        command.add_argument('--max-gib', type=float, default=30)
        if name == 'extract':
            command.add_argument('--output', '-o', required=True, help='Parent folder; a fresh subfolder is created')
            command.add_argument('--convert-only', action='store_true', help='Keep ISO without unpacking it')
            command.add_argument('--cab-folder', action='append', default=[], help='Additional cabinet volume folder; repeatable')
        else:
            command.add_argument('--full', action='store_true', help='Include the entire file listing in JSON')
    command = sub.add_parser('download')
    command.add_argument('url')
    command.add_argument('--output', '-o', required=True)
    command.add_argument('--filename', default='')
    command.add_argument('--sha256', default='')
    command.add_argument('--max-gib', type=float, default=30)
    args = parser.parse_args(argv)
    if not args.command or args.command == 'gui':
        from .gui import main as gui
        return gui()
    if not math.isfinite(getattr(args, 'max_gib', 30)) or getattr(args, 'max_gib', 30) <= 0:
        parser.error('--max-gib must be positive')
    ctx = Context(lambda message: print(message, file=sys.stderr, flush=True),
                  max_bytes=int(getattr(args, 'max_gib', 30) * 1024**3))
    try:
        if args.command == 'tools':
            result = {'verified': True, 'versions': ctx.verify_tools()}
        elif args.command == 'inspect':
            info = inspect(args.source, ctx, args.mode)
            result = info.summary()
            result['entries'] = [asdict(e) for e in (info.entries if args.full else info.entries[:20])]
        elif args.command == 'extract':
            report = extract(args.source, args.output, ctx, args.mode, args.convert_only, args.cab_folder)
            result = {key: value for key, value in report.items() if key != 'files'}
        else:
            result = {'downloaded': str(download(args.url, args.output, ctx, args.filename, args.sha256))}
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except (UnpackError, OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        ctx.cancel.set()
        return 130


if __name__ == '__main__':
    raise SystemExit(main())
