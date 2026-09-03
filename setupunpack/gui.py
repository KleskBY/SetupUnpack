from pathlib import Path
from queue import Queue, Empty
import ctypes
import math
import os
import sys
import threading
import traceback
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from . import __version__
from .engine import Context, Cancelled, extract, inspect
from .download import download


MODES = {'Auto-detect': 'auto', 'Archive or ISO': 'archive', 'Inno Setup': 'inno',
         'InstallShield cabinet': 'installshield-cab', 'InstallShield self-extractor': 'installshield-exe',
         'BIN / CUE data track': 'bin-cue'}


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f'SetupUnpack {__version__}')
        height = min(940, self.winfo_screenheight() - 100)
        self.geometry(f'1040x{height}')
        self.minsize(900, min(800, height))
        self.configure(background='#eef2f6')
        self.events = Queue()
        self.cancel_event = threading.Event()
        self.busy = False
        self.last_output = None
        self.sources = []
        self.cab_folders = []
        self.controls = []
        self.output = tk.StringVar(value=str(self.app_root() / 'extracted'))
        self.mode = tk.StringVar(value='Auto-detect')
        self.max_gib = tk.StringVar(value='30')
        self.convert_only = tk.BooleanVar(value=False)
        self.download_extract = tk.BooleanVar(value=False)
        self.filename = tk.StringVar()
        self.expected_hash = tk.StringVar()
        self.status = tk.StringVar(value='Ready. Choose a file to inspect or extract.')
        self.build_ui()
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.after(80, self.drain)

    @staticmethod
    def app_root():
        return Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent.parent

    def button(self, parent, text, command, **kwargs):
        button = ttk.Button(parent, text=text, command=command, **kwargs)
        self.controls.append(button)
        return button

    def build_ui(self):
        style = ttk.Style(self)
        style.theme_use('clam')
        style.configure('.', font=('Segoe UI', 10))
        style.configure('TFrame', background='#eef2f6')
        style.configure('TLabel', background='#eef2f6', foreground='#203349')
        style.configure('TButton', padding=(12, 7))
        style.configure('Accent.TButton', background='#2265c7', foreground='white')
        style.map('Accent.TButton', background=[('active', '#174b98'), ('disabled', '#9aa8bb')])
        header = tk.Frame(self, background='#142b49', padx=24, pady=18)
        header.pack(fill='x')
        tk.Label(header, text='SetupUnpack', font=('Segoe UI', 23, 'bold'), bg='#142b49', fg='white').pack(anchor='w')
        tk.Label(header, text='Open the package. Keep the installer asleep.', font=('Segoe UI', 11), bg='#142b49', fg='#c1d4ed').pack(anchor='w', pady=(3, 0))
        outer = ttk.Frame(self, padding=(20, 14))
        outer.pack(fill='both', expand=True)
        self.tabs = ttk.Notebook(outer)
        self.tabs.pack(fill='x')
        extract_tab = ttk.Frame(self.tabs, padding=12)
        download_tab = ttk.Frame(self.tabs, padding=12)
        tools_tab = ttk.Frame(self.tabs, padding=16)
        self.tabs.add(extract_tab, text='  Extract files  ')
        self.tabs.add(download_tab, text='  Download  ')
        self.tabs.add(tools_tab, text='  About & tools  ')

        top = ttk.Frame(extract_tab)
        top.pack(fill='x')
        self.button(top, 'Add files…', self.add_files).pack(side='left')
        self.button(top, 'Add cabinet folder…', self.add_cab_source).pack(side='left', padx=6)
        self.button(top, 'Remove selected', self.remove_selected).pack(side='right')
        list_frame = ttk.Frame(extract_tab)
        list_frame.pack(fill='x', pady=8)
        self.source_list = tk.Listbox(list_frame, height=4, selectmode='extended', font=('Segoe UI', 10),
                                     background='white', relief='flat', activestyle='none', exportselection=False)
        self.source_list.pack(side='left', fill='x', expand=True)
        scroll = ttk.Scrollbar(list_frame, orient='vertical', command=self.source_list.yview)
        scroll.pack(side='right', fill='y')
        self.source_list.configure(yscrollcommand=scroll.set)
        settings = ttk.Frame(extract_tab)
        settings.pack(fill='x')
        ttk.Label(settings, text='Format').grid(row=0, column=0, sticky='w')
        self.mode_box = ttk.Combobox(settings, textvariable=self.mode, values=list(MODES), state='readonly', width=27)
        self.mode_box.grid(row=0, column=1, sticky='w', padx=(8, 18))
        self.controls.append(self.mode_box)
        extra = ttk.Frame(extract_tab)
        extra.pack(fill='x', pady=(8, 0))
        self.button(extra, 'Add companion CAB folder…', self.add_companion).pack(side='left')
        self.button(extra, 'Clear companions', self.clear_companions).pack(side='left', padx=6)
        self.companions_label = ttk.Label(extra, text='None added')
        self.companions_label.pack(side='left')
        convert = ttk.Checkbutton(extract_tab, text='BIN/CUE: convert to ISO only', variable=self.convert_only)
        convert.pack(anchor='w', pady=(7, 0))
        self.controls.append(convert)
        ttk.Label(extract_tab, text='Each input gets a new folder. Setup programs and extracted files are never launched.', foreground='#52647b').pack(anchor='w', pady=(7, 0))

        ttk.Label(download_tab, text='HTTP / HTTPS URL — one per line').pack(anchor='w')
        self.urls = tk.Text(download_tab, height=4, wrap='word', font=('Segoe UI', 10), relief='flat')
        self.urls.pack(fill='x', pady=6)
        row = ttk.Frame(download_tab)
        row.pack(fill='x')
        ttk.Label(row, text='Filename (optional, single URL)').grid(row=0, column=0, sticky='w')
        filename_entry = ttk.Entry(row, textvariable=self.filename, width=32)
        filename_entry.grid(row=0, column=1, padx=8, sticky='ew')
        ttk.Label(row, text='Expected SHA-256 (optional)').grid(row=1, column=0, sticky='w', pady=6)
        hash_entry = ttk.Entry(row, textvariable=self.expected_hash, width=64)
        hash_entry.grid(row=1, column=1, padx=8, sticky='ew')
        self.controls.extend((filename_entry, hash_entry))
        download_check = ttk.Checkbutton(download_tab, text='Extract after downloading (uses the selected format and layout)', variable=self.download_extract)
        download_check.pack(anchor='w', pady=5)
        self.controls.append(download_check)
        ttk.Label(download_tab, text='Interrupted downloads retain .part files. Resume requires the server to confirm the same file.', foreground='#52647b').pack(anchor='w', pady=4)

        about = ('A portable workbench for extracting packages without running setup.\n\n'
                 'Bundled tools: 7-Zip 25.01 · innoextract 1.9 · Unshield 1.6.2\n'
                 'Plus a reader for old InstallShield self-extractors and a MODE1 BIN/CUE converter.\n\n'
                 'Archive paths and sizes are checked before extraction. Completed jobs include a\n'
                 'SHA-256 manifest and tool log. Failed/cancelled jobs remain clearly marked.\n'
                 'No mounting, registry edits, game patch application or antivirus exceptions.\n\n'
                 'Newer or unusual installers, encrypted archives and mixed audio/data tracks\n'
                 'may be unsupported. Extraction does not reproduce installer actions.')
        ttk.Label(tools_tab, text=about, justify='left').pack(anchor='w')
        self.button(tools_tab, 'Verify bundled tools', self.verify_tools).pack(anchor='w', pady=(10, 0))

        output_row = ttk.Frame(outer)
        output_row.pack(fill='x', pady=(12, 8))
        ttk.Label(output_row, text='Save to').pack(side='left')
        output_entry = ttk.Entry(output_row, textvariable=self.output)
        output_entry.pack(side='left', fill='x', expand=True, padx=8)
        self.controls.append(output_entry)
        self.button(output_row, 'Browse…', self.choose_output).pack(side='left')
        ttk.Label(output_row, text='Limit (GiB)').pack(side='left', padx=(14, 6))
        limit = ttk.Entry(output_row, textvariable=self.max_gib, width=6)
        limit.pack(side='left')
        self.controls.append(limit)
        actions = ttk.Frame(outer)
        actions.pack(fill='x', pady=(0, 10))
        self.button(actions, 'Inspect selected', lambda: self.start('inspect')).pack(side='left')
        self.button(actions, 'Extract queue', lambda: self.start('extract'), style='Accent.TButton').pack(side='left', padx=8)
        self.button(actions, 'Download URLs', lambda: self.start('download')).pack(side='left')
        self.cancel_button = ttk.Button(actions, text='Cancel', command=self.cancel_job, state='disabled')
        self.cancel_button.pack(side='left', padx=8)
        self.open_button = ttk.Button(actions, text='Open output folder', command=self.open_output, state='disabled')
        self.open_button.pack(side='right')
        self.progress = ttk.Progressbar(outer, mode='indeterminate')
        self.progress.pack(fill='x')
        ttk.Label(outer, textvariable=self.status, wraplength=970).pack(anchor='w', pady=(6, 8))

        self.results = ttk.Notebook(outer)
        self.results.pack(fill='both', expand=True)
        log_tab = ttk.Frame(self.results)
        file_tab = ttk.Frame(self.results)
        self.results.add(log_tab, text='  Activity log  ')
        self.results.add(file_tab, text='  Inspected files  ')
        self.log_view = tk.Text(log_tab, height=8, wrap='word', background='#172638', foreground='#e1ebf6',
                                font=('Consolas', 10), relief='flat', state='disabled')
        self.log_view.pack(side='left', fill='both', expand=True)
        scrollbar = ttk.Scrollbar(log_tab, command=self.log_view.yview)
        scrollbar.pack(side='right', fill='y')
        self.log_view.configure(yscrollcommand=scrollbar.set)
        self.tree = ttk.Treeview(file_tab, columns=('path', 'size'), show='headings', height=8)
        self.tree.heading('path', text='Path (first 5,000 entries)')
        self.tree.heading('size', text='Bytes')
        self.tree.column('path', width=720)
        self.tree.column('size', width=130, anchor='e')
        self.tree.pack(side='left', fill='both', expand=True)
        tree_scroll = ttk.Scrollbar(file_tab, command=self.tree.yview)
        tree_scroll.pack(side='right', fill='y')
        self.tree.configure(yscrollcommand=tree_scroll.set)

    def add_files(self):
        paths = filedialog.askopenfilenames(title='Choose packages to inspect or extract', filetypes=[
            ('Packages and images', '*.exe *.rar *.zip *.7z *.cab *.iso *.cue *.bin *.msi *.tar *.gz'), ('All files', '*.*')])
        for path in paths:
            self.add_source(path)

    def add_source(self, path):
        path = str(Path(path).resolve())
        if path not in self.sources:
            self.sources.append(path)
            self.source_list.insert('end', path)

    def add_cab_source(self):
        folder = filedialog.askdirectory(title='Folder containing data1.cab')
        if folder:
            self.add_source(str(Path(folder) / 'data1.cab'))

    def remove_selected(self):
        for index in reversed(self.source_list.curselection()):
            self.sources.pop(index)
            self.source_list.delete(index)

    def add_companion(self):
        folder = filedialog.askdirectory(title='Additional disc folder with CAB/HDR volumes')
        if folder and folder not in self.cab_folders:
            self.cab_folders.append(folder)
        self.companions_label.configure(text=f'{len(self.cab_folders)} folder(s)')

    def clear_companions(self):
        self.cab_folders.clear()
        self.companions_label.configure(text='None added')

    def choose_output(self):
        folder = filedialog.askdirectory(title='Parent folder for extracted files')
        if folder:
            self.output.set(folder)

    def start(self, action):
        if self.busy:
            return
        try:
            maximum = float(self.max_gib.get())
            if not math.isfinite(maximum) or maximum <= 0:
                raise ValueError()
        except ValueError:
            messagebox.showerror('Size limit', 'Enter a positive size limit in GiB.')
            return
        chosen = list(self.sources)
        if action == 'inspect':
            selected = self.source_list.curselection()
            chosen = [self.sources[selected[0] if selected else 0]] if self.sources else []
        urls = [line.strip() for line in self.urls.get('1.0', 'end').splitlines() if line.strip()]
        if action in ('extract', 'inspect') and not chosen:
            messagebox.showinfo('Choose a file', 'Add at least one package first.')
            return
        if action == 'download' and not urls:
            messagebox.showinfo('Enter a URL', 'Paste a URL on the Download tab first.')
            return
        if len(urls) > 1 and (self.filename.get().strip() or self.expected_hash.get().strip()):
            if action == 'download':
                messagebox.showerror('Multiple URLs', 'Filename and expected SHA-256 apply to a single URL. Clear them to download multiple files.')
                return
        options = dict(mode=MODES[self.mode.get()],
                       convert_only=self.convert_only.get(), extra_folders=tuple(self.cab_folders))
        output = self.output.get().strip()
        if not output:
            messagebox.showerror('Output folder', 'Choose an output folder.')
            return
        filename, checksum = self.filename.get().strip(), self.expected_hash.get().strip()
        also_extract = self.download_extract.get()
        def worker(ctx):
            if action == 'inspect':
                result = inspect(chosen[0], ctx, options['mode'])
                self.events.put(('inspection', result))
                return 'Inspection complete. No installer was run.'
            items = chosen
            if action == 'download':
                items = []
                for url in urls:
                    path = download(url, Path(output) / 'downloads', ctx, filename, checksum)
                    items.append(str(path))
                    self.events.put(('downloaded', str(path)))
                    self.events.put(('output', path.parent))
                if not also_extract:
                    return f'{len(items)} download(s) completed.'
            for index, item in enumerate(items, 1):
                ctx.log(f'Package {index} of {len(items)}')
                report = extract(item, output, ctx, **options)
                self.events.put(('output', Path(report['output'])))
                ctx.log_path = None
            return f'Complete. {len(items)} package(s) extracted and verified.'
        self.launch(worker, maximum)

    def launch(self, work, maximum=30):
        self.busy = True
        self.cancel_event.clear()
        for widget in self.controls:
            widget.configure(state='disabled')
        self.urls.configure(state='disabled')
        self.cancel_button.configure(state='normal')
        self.progress.start(15)
        self.status.set('Working…')
        self.results.select(0)
        def run():
            ctx = Context(lambda text: self.events.put(('log', text)), self.cancel_event,
                          max_bytes=int(maximum * 1024**3))
            try:
                message = work(ctx)
                self.events.put(('done', message))
            except Cancelled as error:
                self.events.put(('done', str(error)))
            except Exception as error:
                self.events.put(('log', traceback.format_exc()))
                if ctx.log_path:
                    self.events.put(('output', ctx.log_path.parent))
                self.events.put(('error', str(error)))
        threading.Thread(target=run, daemon=True).start()

    def verify_tools(self):
        def worker(ctx):
            versions = ctx.verify_tools()
            ctx.log('Verified bundled binaries: ' + ', '.join(f'{name} {version}' for name, version in versions.items()))
            return 'All bundled extractor checksums match.'
        self.launch(worker)

    def cancel_job(self):
        self.cancel_event.set()
        self.status.set('Cancelling… Any active extractor will stop; network reads may take up to 15 seconds.')
        self.cancel_button.configure(state='disabled')

    def drain(self):
        try:
            for _ in range(100):
                event, value = self.events.get_nowait()
                if event == 'log':
                    self.log_view.configure(state='normal')
                    self.log_view.insert('end', value + '\n')
                    if int(self.log_view.index('end-1c').split('.')[0]) > 2500:
                        self.log_view.delete('1.0', '500.0')
                    self.log_view.see('end')
                    self.log_view.configure(state='disabled')
                elif event == 'inspection':
                    self.tree.delete(*self.tree.get_children())
                    for entry in value.entries[:5000]:
                        self.tree.insert('', 'end', values=(entry.path, 'Folder' if entry.directory else f'{entry.size:,}'))
                    summary = value.summary()
                    self.events.put(('log', f'{value.kind} · {summary["file_count"]:,} files · {summary["expanded_bytes"] / 1024**2:,.1f} MiB\n{value.detail}'))
                    self.results.select(1)
                elif event == 'output':
                    self.last_output = Path(value)
                    self.open_button.configure(state='normal')
                elif event == 'downloaded':
                    self.add_source(value)
                elif event in ('done', 'error'):
                    self.busy = False
                    self.progress.stop()
                    self.cancel_button.configure(state='disabled')
                    for widget in self.controls:
                        widget.configure(state='readonly' if isinstance(widget, ttk.Combobox) else 'normal')
                    self.urls.configure(state='normal')
                    self.status.set(value if event == 'done' else 'Stopped: ' + value[:250])
                    if event == 'error':
                        messagebox.showerror('Extraction stopped', value)
        except Empty:
            pass
        self.after(80, self.drain)

    def open_output(self):
        if self.last_output and self.last_output.is_dir():
            os.startfile(str(self.last_output))

    def close(self):
        if self.busy:
            self.cancel_job()
            self.status.set('Stopping the current job. Close the window after it stops.')
        else:
            self.destroy()


def main():
    if os.name == 'nt':
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass
    app = App()
    app.mainloop()


if __name__ == '__main__':
    main()
