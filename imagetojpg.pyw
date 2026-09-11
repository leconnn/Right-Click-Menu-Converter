import os
import sys
import ctypes
import subprocess
import tempfile
import time
import threading
import tkinter as tk
from tkinter import ttk
from PIL import Image, ImageOps

_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0

# --- Cloud placeholder (OneDrive Files On-Demand, etc.) handling ---
# A cloud-only file still passes os.path.isfile(), but Pillow doing
# random-access reads on it can fail if the cloud filter driver doesn't
# hydrate on-demand for external processes. Force a full download by
# reading the whole file ourselves before handing it to Image.open.
_FILE_ATTRIBUTE_OFFLINE = 0x1000
_FILE_ATTRIBUTE_RECALL_ON_OPEN = 0x40000
_FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS = 0x400000
_INVALID_FILE_ATTRIBUTES = 0xFFFFFFFF

def _is_cloud_placeholder(path):
    if sys.platform != 'win32':
        return False
    attrs = ctypes.windll.kernel32.GetFileAttributesW(path)
    if attrs == _INVALID_FILE_ATTRIBUTES:
        return False
    return bool(attrs & (_FILE_ATTRIBUTE_OFFLINE | _FILE_ATTRIBUTE_RECALL_ON_OPEN
                          | _FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS))

def _ensure_hydrated(file, set_status=None):
    if not _is_cloud_placeholder(file):
        return True
    if set_status:
        set_status(f'Downloading from cloud: {os.path.basename(file)}')
    try:
        with open(file, 'rb') as f:
            while f.read(4 * 1024 * 1024):
                pass
        return True
    except OSError:
        return False

# --- Queue / lock helpers ---
_SCRIPT_ID = os.path.splitext(os.path.basename(__file__))[0]
_QUEUE_DIR = os.path.join(tempfile.gettempdir(), f'rcc_{_SCRIPT_ID}_queue')
_LOCK_FILE = os.path.join(tempfile.gettempdir(), f'rcc_{_SCRIPT_ID}.lock')
_LOG_FILE = os.path.join(tempfile.gettempdir(), f'rcc_{_SCRIPT_ID}.log')


def _log(msg):
    try:
        with open(_LOG_FILE, 'a', encoding='utf-8') as _f:
            _f.write(f'[{time.strftime("%H:%M:%S")}] {msg}\n')
    except OSError:
        pass


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
                with open(_LOCK_FILE, 'r', encoding='utf-8') as _f:
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
                _log(f'removing stale lock (dead pid {owner_pid})')
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


def _dequeue():
    os.makedirs(_QUEUE_DIR, exist_ok=True)
    for job in sorted(os.listdir(_QUEUE_DIR)):
        job_path = os.path.join(_QUEUE_DIR, job)
        try:
            with open(job_path, 'r', encoding='utf-8') as f:
                path = f.read().strip()
            os.remove(job_path)
            return path
        except OSError:
            continue
    return None


IMAGE_EXTENSIONS = (
    '.jpg', '.jpeg', '.png', '.bmp', '.gif', '.tiff', '.tif', '.webp',
    '.jfif', '.heic', '.heif', '.avif'
)
_INVALID_NAME_CHARS = '<>:"/\\|?*'


def sanitize_filename(name):
    name = ''.join(c for c in name if c not in _INVALID_NAME_CHARS).strip().rstrip('.')
    return name or 'image'


def make_output_path(source_path):
    source_dir = os.path.dirname(source_path)
    source_name = os.path.splitext(os.path.basename(source_path))[0]
    base_name = sanitize_filename(source_name)
    candidate = os.path.join(source_dir, f'{base_name}.jpg')
    n = 1
    while os.path.exists(candidate):
        candidate = os.path.join(source_dir, f'{base_name} ({n}).jpg')
        n += 1
    return candidate


def process_file(file, set_status=None, set_progress=None):
    if not (os.path.isfile(file) and file.lower().endswith(IMAGE_EXTENSIONS)):
        return None

    name = os.path.basename(file)
    ext = os.path.splitext(file)[1].lower()
    if ext in {'.jpg', '.jpeg'}:
        if set_status:
            set_status(f'Skipping: {name}')
        return None

    if set_status:
        set_status(f'Converting: {name}')

    if not _ensure_hydrated(file, set_status):
        _log(f'FAIL {name}: could not download file from cloud storage')
        return 'could not download file from cloud storage'

    try:
        with Image.open(file) as img:
            img = ImageOps.exif_transpose(img)
            if img.mode in ('RGBA', 'LA', 'P'):
                bg = Image.new('RGB', img.size, (255, 255, 255))
                if 'transparency' in img.info:
                    img = img.convert('RGBA')
                    bg.paste(img, mask=img.split()[-1])
                else:
                    img = img.convert('RGB')
                rgb_img = bg
            else:
                rgb_img = img.convert('RGB')

            out_path = make_output_path(file)
            rgb_img.save(out_path, 'JPEG', quality=95)
    except Exception as exc:
        _log(f'FAIL {name}: {exc}')
        return str(exc)

    if set_progress:
        set_progress(100)
    _log(f'OK   {name} -> {os.path.basename(out_path)}')
    return True


# --- Worker thread: drains the queue and updates the GUI ---
def _worker(set_status, set_progress, set_file_label, root):
    time.sleep(0.3)
    processed = 0
    idle_checks = 0
    errors = []
    try:
        total = len([f for f in os.listdir(_QUEUE_DIR) if f.endswith('.job')])
    except OSError:
        total = 0
    done = 0
    try:
        while True:
            file = _dequeue()
            if file is None:
                idle_checks += 1
                if idle_checks >= 3:
                    break
                time.sleep(0.15)
                continue
            idle_checks = 0
            done += 1
            set_file_label(f'File {done} / {total}')
            set_progress(0)
            res = process_file(file, set_status, set_progress)
            if res is True:
                processed += 1
            elif res is not None:
                errors.append(f'{os.path.basename(file)}: {res}')
    except Exception as exc:
        set_status(f'Error: {exc}')
        time.sleep(5)
        _unlock()
        root.after(0, root.destroy)
        return

    _unlock()
    if errors:
        msg = f'Done — {processed} converted, {len(errors)} failed: {errors[0]}'
        _log(msg)
        set_status(msg)
        time.sleep(4)
    else:
        _log(f'Done — {processed} file{"s" if processed != 1 else ""} converted.')
    root.after(0, root.destroy)


# --- Main: enqueue this batch, then become the worker if no one else is ---
_log(f'START pid={os.getpid()} argv={sys.argv[1:]}')
_enqueue(sys.argv[1:])

if _try_lock():
    root = tk.Tk()
    root.title('Image to JPG Converter')
    root.resizable(False, False)

    frame = tk.Frame(root, padx=20, pady=16)
    frame.pack(fill='both', expand=True)

    status_var = tk.StringVar(value='Waiting for queue…')
    tk.Label(frame, textvariable=status_var, wraplength=360,
             justify='left', anchor='w').pack(fill='x')

    file_var = tk.StringVar(value='')
    tk.Label(frame, textvariable=file_var, anchor='w').pack(fill='x')

    bar = ttk.Progressbar(frame, mode='determinate', length=360, maximum=100)
    bar.pack(pady=(6, 0))

    root.update_idletasks()
    w, h = 400, 110
    sx = (root.winfo_screenwidth() - w) // 2
    sy = (root.winfo_screenheight() - h) // 2
    root.geometry(f'{w}x{h}+{sx}+{sy}')

    t = threading.Thread(
        target=_worker,
        args=(
            lambda msg: root.after(0, status_var.set, msg),
            lambda val: root.after(0, lambda v=val: bar.configure(value=v)),
            lambda msg: root.after(0, file_var.set, msg),
            root,
        ),
        daemon=True,
    )
    t.start()

    root.protocol('WM_DELETE_WINDOW', lambda: (_unlock(), root.destroy()))
    root.mainloop()
