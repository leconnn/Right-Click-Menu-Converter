import os
import sys
import time
import tempfile
import subprocess
import threading
import tkinter as tk
from tkinter import ttk
from PIL import Image, ImageOps, ImageTk

# Requires Pillow (see Installer/requirements.txt)

_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0

# --- Queue / lock helpers ---
# Explorer may invoke this script once per selected file instead of once for
# the whole selection (MultiSelectModel=Player isn't reliably honored on all
# Windows builds). So every invocation drops its files in a shared queue;
# whichever instance grabs the lock becomes the master that shows the UI
# with everyone's files, and the rest exit quietly.
_SCRIPT_ID = os.path.splitext(os.path.basename(__file__))[0]
_QUEUE_DIR = os.path.join(tempfile.gettempdir(), f'rcc_{_SCRIPT_ID}_queue')
_LOCK_FILE = os.path.join(tempfile.gettempdir(), f'rcc_{_SCRIPT_ID}.lock')


def _enqueue(files):
    os.makedirs(_QUEUE_DIR, exist_ok=True)
    for path in files:
        job = os.path.join(_QUEUE_DIR, f'{os.getpid()}_{time.time_ns()}.job')
        with open(job, 'w', encoding='utf-8') as f:
            f.write(path)


def _is_python_process(pid):
    try:
        out = subprocess.run(
            ['tasklist', '/FI', f'PID eq {pid}', '/FO', 'CSV', '/NH'],
            capture_output=True, text=True, creationflags=_NO_WINDOW, timeout=3
        ).stdout.lower()
        return 'python' in out
    except Exception:
        return True


def _try_lock():
    """Acquire the lock, stealing it only if the owning process is confirmed dead."""
    for _ in range(10):
        try:
            fd = os.open(_LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            return True
        except FileExistsError:
            try:
                with open(_LOCK_FILE, 'r') as _f:
                    content = _f.read().strip()
                if not content:
                    time.sleep(0.05)
                    continue
                owner_pid = int(content)
                os.kill(owner_pid, 0)
                if not _is_python_process(owner_pid):
                    raise ProcessLookupError
                return False
            except ProcessLookupError:
                try:
                    os.remove(_LOCK_FILE)
                except OSError:
                    pass
            except (OSError, ValueError):
                time.sleep(0.05)
    return False


def _unlock():
    try:
        os.remove(_LOCK_FILE)
    except OSError:
        pass


def _drain_all():
    os.makedirs(_QUEUE_DIR, exist_ok=True)
    files = []
    for job in sorted(os.listdir(_QUEUE_DIR)):
        job_path = os.path.join(_QUEUE_DIR, job)
        try:
            with open(job_path, 'r', encoding='utf-8') as f:
                path = f.read().strip()
            os.remove(job_path)
            if path:
                files.append(path)
        except OSError:
            continue
    return files


IMAGE_EXTENSIONS = ('.jpg', '.jpeg', '.png', '.bmp', '.gif', '.tiff', '.tif', '.webp')

# width, height in inches (portrait)
PAPER_SIZES_IN = {
    'A4': (8.27, 11.69),
    'Letter': (8.5, 11.0),
    'Legal': (8.5, 14.0),
    'Long (Folio, 8.5x13)': (8.5, 13.0),
    'A3': (11.69, 16.54),
    'A5': (5.83, 8.27),
    'Tabloid': (11.0, 17.0),
    'Original Image Size': None,
}

PAGE_DPI = 200
ORIGINAL_RESOLUTION = 96

THUMB_SIZE = 110
CARD_W = 130
CARD_H = 172


def to_rgb(img):
    if img.mode in ('RGBA', 'LA') or (img.mode == 'P' and 'transparency' in img.info):
        img = img.convert('RGBA')
        bg = Image.new('RGB', img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[-1])
        return bg
    return img.convert('RGB')


def fit_image(img, page_px, mode):
    pw, ph = page_px
    iw, ih = img.size
    scale = min(pw / iw, ph / ih) if mode == 'fit' else max(pw / iw, ph / ih)
    nw, nh = max(1, round(iw * scale)), max(1, round(ih * scale))
    resized = img.resize((nw, nh), Image.LANCZOS)
    if mode == 'fit':
        canvas = Image.new('RGB', (pw, ph), (255, 255, 255))
        canvas.paste(resized, ((pw - nw) // 2, (ph - nh) // 2))
        return canvas
    left = (nw - pw) // 2
    top = (nh - ph) // 2
    return resized.crop((left, top, left + pw, top + ph))


def build_pages(files, size_key, orientation, fit_mode, set_status=None, set_progress=None):
    pages = []
    total = len(files)
    for i, path in enumerate(files):
        if set_status:
            set_status(f'Processing {i + 1}/{total}: {os.path.basename(path)}')
        img = Image.open(path)
        img = ImageOps.exif_transpose(img)
        img = to_rgb(img)

        if size_key == 'Original Image Size':
            page = img
        else:
            w_in, h_in = PAPER_SIZES_IN[size_key]
            if orientation == 'Landscape':
                w_in, h_in = h_in, w_in
            page_px = (round(w_in * PAGE_DPI), round(h_in * PAGE_DPI))
            page = fit_image(img, page_px, fit_mode)

        pages.append(page)
        if set_progress:
            set_progress((i + 1) / total * 90)
    return pages


_INVALID_NAME_CHARS = '<>:"/\\|?*'


def sanitize_filename(name):
    name = ''.join(c for c in name if c not in _INVALID_NAME_CHARS).strip().rstrip('.')
    return name


def make_output_path(base_dir, base_name):
    base_name = sanitize_filename(base_name) or 'images'
    if base_name.lower().endswith('.pdf'):
        base_name = base_name[:-4]
    candidate = os.path.join(base_dir, base_name + '.pdf')
    n = 1
    while os.path.exists(candidate):
        candidate = os.path.join(base_dir, f'{base_name} ({n}).pdf')
        n += 1
    return candidate


def make_thumbnail(path):
    img = Image.open(path)
    img = ImageOps.exif_transpose(img)
    img.thumbnail((THUMB_SIZE, THUMB_SIZE), Image.LANCZOS)
    canvas = Image.new('RGB', (THUMB_SIZE, THUMB_SIZE), '#f0f0f0')
    if img.mode in ('RGBA', 'LA') or (img.mode == 'P' and 'transparency' in img.info):
        img = img.convert('RGBA')
        canvas.paste(img, ((THUMB_SIZE - img.width) // 2, (THUMB_SIZE - img.height) // 2), mask=img.split()[-1])
    else:
        img = img.convert('RGB')
        canvas.paste(img, ((THUMB_SIZE - img.width) // 2, (THUMB_SIZE - img.height) // 2))
    return ImageTk.PhotoImage(canvas)


class App:
    def __init__(self, root, files, skipped):
        self.root = root
        self.files = files
        self.thumb_cache = {}
        self.cards = {}  # path -> {'frame': ..., 'number': label}
        self.drag_path = None
        self.drag_ghost = None
        self.drag_offset_x = 0
        self.drag_offset_y = 0
        self.converting = False

        for path in list(self.files):
            try:
                self.thumb_cache[path] = make_thumbnail(path)
            except Exception:
                skipped.append(os.path.basename(path))
                self.files.remove(path)

        self.build_ui(skipped)
        for path in self.files:
            self.build_card(path)
        self.repack()

    def build_ui(self, skipped):
        root = self.root
        root.title('Images to PDF')
        root.resizable(False, False)

        outer = tk.Frame(root, padx=16, pady=14)
        outer.pack(fill='both', expand=True)

        tk.Label(outer, text='Drag thumbnails to set the page order',
                 anchor='w').pack(fill='x')

        canvas_frame = tk.Frame(outer)
        canvas_frame.pack(fill='x', pady=(6, 10))
        self.canvas = tk.Canvas(canvas_frame, height=CARD_H + 8, width=660,
                                 highlightthickness=1, highlightbackground='#ccc',
                                 bg='white')
        hbar = tk.Scrollbar(canvas_frame, orient='horizontal', command=self.canvas.xview)
        self.canvas.configure(xscrollcommand=hbar.set)
        self.canvas.pack(side='top', fill='x')
        hbar.pack(side='top', fill='x')

        self.inner = tk.Frame(self.canvas, bg='white')
        self.canvas.create_window((0, 0), window=self.inner, anchor='nw')
        self.inner.bind('<Configure>',
                         lambda e: self.canvas.configure(scrollregion=self.canvas.bbox('all')))

        opts = tk.Frame(outer)
        opts.pack(fill='x', pady=(4, 10))

        tk.Label(opts, text='File name:').grid(row=0, column=0, sticky='w', pady=(0, 8))
        name_row = tk.Frame(opts)
        name_row.grid(row=0, column=1, columnspan=2, sticky='w', padx=(6, 0), pady=(0, 8))
        default_name = os.path.splitext(os.path.basename(self.files[0]))[0]
        self.name_var = tk.StringVar(value=default_name)
        tk.Entry(name_row, textvariable=self.name_var, width=30).pack(side='left')
        tk.Label(name_row, text='.pdf').pack(side='left', padx=(4, 0))

        tk.Label(opts, text='Page size:').grid(row=1, column=0, sticky='w')
        self.size_var = tk.StringVar(value='A4')
        size_combo = ttk.Combobox(opts, textvariable=self.size_var, state='readonly',
                                   values=list(PAPER_SIZES_IN.keys()), width=22)
        size_combo.grid(row=1, column=1, padx=(6, 20), sticky='w')
        size_combo.bind('<<ComboboxSelected>>', self.on_size_change)

        self.orient_var = tk.StringVar(value='Portrait')
        self.orient_frame = tk.Frame(opts)
        self.orient_frame.grid(row=1, column=2, columnspan=2, sticky='w')
        tk.Label(self.orient_frame, text='Orientation:').pack(side='left')
        tk.Radiobutton(self.orient_frame, text='Portrait', variable=self.orient_var,
                        value='Portrait').pack(side='left')
        tk.Radiobutton(self.orient_frame, text='Landscape', variable=self.orient_var,
                        value='Landscape').pack(side='left')

        self.fit_var = tk.StringVar(value='Fit')
        fit_frame = tk.Frame(opts)
        fit_frame.grid(row=2, column=0, columnspan=4, sticky='w', pady=(8, 0))
        tk.Radiobutton(fit_frame, text='Fit (whole image, white borders)', variable=self.fit_var,
                        value='Fit').pack(side='left')
        tk.Radiobutton(fit_frame, text='Fill (crop to fill page)', variable=self.fit_var,
                        value='Fill').pack(side='left')

        status_text = f'{len(self.files)} image(s) loaded'
        if skipped:
            status_text += f' — skipped {len(skipped)} unreadable file(s)'
        self.status_var = tk.StringVar(value=status_text)
        tk.Label(outer, textvariable=self.status_var, anchor='w', wraplength=660,
                 justify='left').pack(fill='x')

        self.progress = ttk.Progressbar(outer, mode='determinate', maximum=100)

        btns = tk.Frame(outer)
        btns.pack(fill='x', pady=(10, 0))
        self.cancel_btn = tk.Button(btns, text='Cancel', width=12, command=root.destroy)
        self.cancel_btn.pack(side='right')
        self.convert_btn = tk.Button(btns, text='Convert to PDF', width=16,
                                      command=self.start_convert)
        self.convert_btn.pack(side='right', padx=(0, 8))

        self.on_size_change()

    def on_size_change(self, event=None):
        is_original = self.size_var.get() == 'Original Image Size'
        state = 'disabled' if is_original else 'normal'
        for child in self.orient_frame.winfo_children():
            if isinstance(child, tk.Radiobutton):
                child.configure(state=state)

    def build_card(self, path):
        card = tk.Frame(self.inner, width=CARD_W, height=CARD_H, bd=1,
                         relief='solid', bg='white')
        card.pack_propagate(False)

        top = tk.Frame(card, bg='white')
        top.pack(fill='x')
        number_label = tk.Label(top, bg='white', fg='#666', font=('Segoe UI', 8))
        number_label.pack(side='left', padx=4)
        rm = tk.Label(top, text='✕', bg='white', fg='#a00', cursor='hand2',
                      font=('Segoe UI', 9, 'bold'))
        rm.pack(side='right', padx=4)
        rm.bind('<Button-1>', lambda e, p=path: self.remove_file(p))

        img_label = tk.Label(card, image=self.thumb_cache[path], bg='white')
        img_label.pack(pady=(2, 2))

        name_label = tk.Label(card, text=os.path.basename(path), bg='white',
                               font=('Segoe UI', 8), wraplength=CARD_W - 10)
        name_label.pack()

        for widget in (card, top, img_label, name_label):
            widget.bind('<ButtonPress-1>', lambda e, p=path: self.drag_start(p, e))
            widget.bind('<B1-Motion>', self.drag_motion)
            widget.bind('<ButtonRelease-1>', self.drag_end)

        self.cards[path] = {'frame': card, 'number': number_label, 'image': img_label}

    def repack(self):
        for path in self.files:
            self.cards[path]['frame'].pack_forget()

        for idx, path in enumerate(self.files):
            self.cards[path]['number'].configure(text=str(idx + 1))
            self.cards[path]['frame'].pack(side='left', padx=4, pady=4)

        self.inner.update_idletasks()
        self.canvas.configure(scrollregion=self.canvas.bbox('all'))

        count_text = f'{len(self.files)} image(s)'
        if self.files:
            count_text += ' — drag thumbnails to reorder'
        else:
            count_text = 'No images left'
        self.status_var.set(count_text)
        self.convert_btn.configure(state='normal' if self.files else 'disabled')

    def remove_file(self, path):
        if self.converting or path not in self.files:
            return
        self.files.remove(path)
        self.cards.pop(path)['frame'].destroy()
        self.repack()

    def drag_start(self, path, event):
        if self.converting:
            return
        self.drag_path = path
        img_widget = self.cards[path]['image']
        self.drag_offset_x = event.x_root - img_widget.winfo_rootx()
        self.drag_offset_y = event.y_root - img_widget.winfo_rooty()

        ghost = tk.Toplevel(self.root)
        ghost.overrideredirect(True)
        try:
            ghost.attributes('-alpha', 0.65)
        except tk.TclError:
            pass
        ghost.attributes('-topmost', True)
        tk.Label(ghost, image=self.thumb_cache[path], bd=1, relief='solid').pack()
        ghost.geometry(f'+{event.x_root - self.drag_offset_x}+{event.y_root - self.drag_offset_y}')
        self.drag_ghost = ghost

    def drag_motion(self, event):
        if self.converting or self.drag_path is None:
            return
        self.drag_ghost.geometry(f'+{event.x_root - self.drag_offset_x}+{event.y_root - self.drag_offset_y}')

        rel_x = event.x_root - self.inner.winfo_rootx()
        target = int(min(max(rel_x // (CARD_W + 8), 0), len(self.files) - 1))
        cur_index = self.files.index(self.drag_path)
        if target != cur_index:
            self.files.insert(target, self.files.pop(cur_index))
            self.repack()

    def drag_end(self, event):
        if self.drag_path is None:
            return
        self.drag_path = None
        self.drag_ghost.destroy()
        self.drag_ghost = None

    def start_convert(self):
        if self.converting or not self.files:
            return
        self.converting = True
        self.convert_btn.configure(state='disabled')
        self.cancel_btn.configure(state='disabled')
        self.progress.pack(fill='x', pady=(8, 0))

        files = list(self.files)
        size_key = self.size_var.get()
        orientation = self.orient_var.get()
        fit_mode = self.fit_var.get().lower()
        out_name = self.name_var.get()

        t = threading.Thread(target=self._convert_worker,
                              args=(files, size_key, orientation, fit_mode, out_name), daemon=True)
        t.start()

    def _convert_worker(self, files, size_key, orientation, fit_mode, out_name):
        try:
            pages = build_pages(
                files, size_key, orientation, fit_mode,
                set_status=lambda m: self.root.after(0, self.status_var.set, m),
                set_progress=lambda v: self.root.after(0, lambda vv=v: self.progress.configure(value=vv))
            )
            base_dir = os.path.dirname(files[0])
            output_path = make_output_path(base_dir, out_name)
            resolution = PAGE_DPI if size_key != 'Original Image Size' else ORIGINAL_RESOLUTION

            self.root.after(0, self.status_var.set, 'Saving PDF…')
            pages[0].save(output_path, save_all=True, append_images=pages[1:],
                          resolution=resolution)

            self.root.after(0, lambda: self.progress.configure(value=100))
            self.root.after(0, self.status_var.set, f'Done — saved {os.path.basename(output_path)}')
            self.root.after(2000, self.root.destroy)
        except Exception as e:
            self.root.after(0, self.status_var.set, f'Error: {e}')
            self.root.after(4000, self.root.destroy)


def main():
    _enqueue(sys.argv[1:])

    if not _try_lock():
        return  # another instance already is (or will become) the master

    time.sleep(0.4)  # let sibling instances spawned by the same selection enqueue
    raw_files = _drain_all()
    files = [f for f in raw_files if os.path.isfile(f) and f.lower().endswith(IMAGE_EXTENSIONS)]
    if not files:
        _unlock()
        return

    skipped = []
    root = tk.Tk()
    App(root, files, skipped)

    root.update_idletasks()
    w, h = root.winfo_reqwidth(), root.winfo_reqheight()
    sx = (root.winfo_screenwidth() - w) // 2
    sy = (root.winfo_screenheight() - h) // 2
    root.geometry(f'{w}x{h}+{sx}+{sy}')

    root.protocol("WM_DELETE_WINDOW", lambda: (_unlock(), root.destroy()))
    root.mainloop()
    _unlock()


if __name__ == '__main__':
    main()
