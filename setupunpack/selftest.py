"""Diagnostic check for both the source app and the frozen portable app."""
from pathlib import Path
import json
import tempfile
import time
import traceback
import zipfile

from .gui import App, messagebox


def run(report_path):
    report = {'status': 'failed'}
    app = None
    original_error = messagebox.showerror
    errors = []
    messagebox.showerror = lambda title, message: errors.append(str(message))
    try:
        with tempfile.TemporaryDirectory(prefix='SetupUnpack-diagnostic-') as temporary:
            root = Path(temporary)
            source = root / 'example package.zip'
            with zipfile.ZipFile(source, 'w') as archive:
                archive.writestr('docs/readme.txt', 'A harmless diagnostic fixture.\n')
                archive.writestr('data/example.bin', bytes(range(128)))
            app = App()
            app.withdraw()
            app.add_source(source)
            app.output.set(str(root / 'output'))
            def wait():
                deadline = time.monotonic() + 30
                while app.busy:
                    app.update()
                    if time.monotonic() > deadline:
                        raise TimeoutError('GUI worker timed out')
                    time.sleep(0.02)
                app.update()
                if errors:
                    raise RuntimeError('; '.join(errors))
            app.verify_tools()
            wait()
            app.start('inspect')
            wait()
            assert len(app.tree.get_children()) == 2, 'File preview did not populate'
            app.start('extract')
            wait()
            assert app.last_output, 'No completed output folder'
            actual = (app.last_output / 'files/data/example.bin').read_bytes()
            assert actual == bytes(range(128)), 'Extracted bytes differ'
            assert str(app.mode_box['state']) == 'readonly', 'GUI controls did not reset'
            manifest = json.loads((app.last_output / 'manifest.json').read_text())
            assert manifest['status'] == 'complete' and manifest['source_unchanged']
            report = {'status': 'passed', 'tool_checksums': True, 'gui_preview': True,
                      'gui_extract': True, 'gui_controls_reset': True,
                      'source_unchanged': True, 'input_executed': False}
    except BaseException:
        report['error'] = traceback.format_exc()
    finally:
        messagebox.showerror = original_error
        if app:
            app.destroy()
        path = Path(report_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    return 0 if report['status'] == 'passed' else 1
