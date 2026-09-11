import os
import sys
import ctypes
import subprocess
import tempfile
import time
import threading
import tkinter as tk
from tkinter import ttk

# Get paths to bundled ffmpeg / ffprobe
if getattr(sys, 'frozen', False):
    script_dir = os.path.dirname(sys.executable)
else:
    script_dir = os.path.dirname(os.path.abspath(__file__))
ffmpeg_exe = 'ffmpeg'
ffprobe_exe = 'ffprobe'
for _bin_dir in ('bin', 'bin_standalone'):
    _ff = os.path.join(script_dir, _bin_dir, 'ffmpeg.exe')
    _fp = os.path.join(script_dir, _bin_dir, 'ffprobe.exe')
    if os.path.exists(_ff):
        ffmpeg_exe = _ff
    if os.path.exists(_fp):
        ffprobe_exe = _fp
    if ffmpeg_exe != 'ffmpeg':
        break

_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0

# --- Cloud placeholder (OneDrive Files On-Demand, etc.) handling ---
# A cloud-only file still passes os.path.isfile(), but ffmpeg/ffprobe doing
# random-access reads on it can fail or hang if the cloud filter driver
# doesn't hydrate on-demand for external processes. Force a full download
# by reading the whole file ourselves before handing the path to ffmpeg.
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
_QUEUE_DIR  = os.path.join(tempfile.gettempdir(), f'rcc_{_SCRIPT_ID}_queue')
_LOCK_FILE  = os.path.join(tempfile.gettempdir(), f'rcc_{_SCRIPT_ID}.lock')

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

# Add to this list if found more audio file formats
audio_extensions = (
    '.wav', '.aac', '.flac', '.ogg', '.wma', '.m4a', '.aiff', '.opus', '.alac',
    '.mp2', '.mp1', '.mid', '.midi', '.amr', '.dsd', '.pcm', '.ape', '.au',
    '.ra', '.tta'
)

def _get_duration(file):
    result = subprocess.run(
        [ffprobe_exe, '-v', 'error', '-show_entries', 'format=duration',
         '-of', 'default=noprint_wrappers=1:nokey=1', file],
        capture_output=True, text=True, creationflags=_NO_WINDOW
    )
    try:
        return float(result.stdout.strip())
    except (ValueError, AttributeError):
        return None

def process_file(file, set_status=None, set_progress=None):
    if not (os.path.isfile(file) and file.lower().endswith(audio_extensions)):
        return None
    base, ext = os.path.splitext(file)
    name = os.path.basename(file)
    if set_status:
        set_status(f'Converting: {name}')

    if not _ensure_hydrated(file, set_status):
        return 'could not download file from cloud storage'

    duration = _get_duration(file)

    proc = subprocess.Popen(
        [ffmpeg_exe, '-y', '-i', base + ext, '-b:a', '192k',
         '-progress', 'pipe:1', '-nostats', base + '.mp3'],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        creationflags=_NO_WINDOW
    )

    stderr_buf = []
    def _drain():
        for line in proc.stderr:
            stderr_buf.append(line)
    threading.Thread(target=_drain, daemon=True).start()

    for raw in proc.stdout:
        line = raw.decode(errors='replace').strip()
        if line.startswith('out_time_ms=') and duration and set_progress:
            try:
                us = int(line.split('=')[1])
                set_progress(min(us / (duration * 1_000_000) * 100, 99))
            except (ValueError, ZeroDivisionError):
                pass

    proc.wait()
    if proc.returncode != 0:
        err = b''.join(stderr_buf).decode(errors='replace').strip().splitlines()
        return err[-1] if err else 'ffmpeg failed'
    if set_progress:
        set_progress(100)
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
    except Exception as e:
        set_status(f'Error: {e}')
        time.sleep(5)
        _unlock()
        root.after(0, root.destroy)
        return

    _unlock()
    if errors:
        msg = f'Done — {processed} converted, {len(errors)} failed: {errors[0]}'
        set_status(msg)
        time.sleep(4)
    root.after(0, root.destroy)

# --- Main: enqueue this batch, then become the worker if no one else is ---
_enqueue(sys.argv[1:])

if _try_lock():
    root = tk.Tk()
    root.title('Audio Converter')
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

    # Centre the window
    root.update_idletasks()
    w, h = 400, 110
    sx = (root.winfo_screenwidth()  - w) // 2
    sy = (root.winfo_screenheight() - h) // 2
    root.geometry(f'{w}x{h}+{sx}+{sy}')

    t = threading.Thread(
        target=_worker,
        args=(lambda msg: root.after(0, status_var.set, msg),
              lambda val: root.after(0, lambda v=val: bar.configure(value=v)),
              lambda msg: root.after(0, file_var.set, msg),
              root),
        daemon=True
    )
    t.start()

    root.protocol("WM_DELETE_WINDOW", lambda: (_unlock(), root.destroy()))
    root.mainloop()
