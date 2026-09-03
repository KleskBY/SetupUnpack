"""Exercise the actual Tk controls and worker queue with a harmless local ZIP."""
from pathlib import Path
import json
import sys
import tempfile
import time
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from setupunpack.gui import App


def wait_for(app, timeout=20):
    deadline = time.monotonic() + timeout
    while app.busy:
        app.update()
        if time.monotonic() > deadline:
            raise TimeoutError('GUI worker did not finish')
        time.sleep(0.02)
    app.update()


def main():
    with tempfile.TemporaryDirectory(prefix='SetupUnpack-GUI-') as temporary:
        folder = Path(temporary)
        source = folder / 'GUI example.zip'
        with zipfile.ZipFile(source, 'w') as bundle:
            bundle.writestr('docs/readme.txt', 'A harmless GUI test.\n')
            bundle.writestr('data/example.bin', bytes(range(128)))
        app = App()
        app.withdraw()
        app.add_source(source)
        app.output.set(str(folder / 'output'))
        app.start('inspect')
        wait_for(app)
        assert len(app.tree.get_children()) == 2, app.status.get()
        app.start('extract')
        wait_for(app)
        assert app.last_output and (app.last_output / 'files/docs/readme.txt').is_file(), app.status.get()
        assert 'Complete.' in app.status.get(), app.status.get()
        assert str(app.cancel_button['state']) == 'disabled'
        assert str(app.mode_box['state']) == 'readonly'
        print(json.dumps({'gui_inspect': 'passed', 'gui_extract': 'passed', 'controls_restored': True}))
        app.destroy()


if __name__ == '__main__':
    main()
