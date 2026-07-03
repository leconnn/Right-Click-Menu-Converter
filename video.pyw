import os
import sys
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

# --- Queue / lock helpers ---
# Each file gets its own job file → enqueue is a single atomic create,
# no read-modify-write race between concurrent instances.
_SCRIPT_ID = os.path.splitext(os.path.basename(__file__))[0]
_QUEUE_DIR  = os.path.join(tempfile.gettempdir(), f'rcc_{_SCRIPT_ID}_queue')
_LOCK_FILE  = os.path.join(tempfile.gettempdir(), f'rcc_{_SCRIPT_ID}.lock')
_LOG_FILE   = os.path.join(tempfile.gettempdir(), f'rcc_{_SCRIPT_ID}.log')

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
                with open(_LOCK_FILE, 'r') as _f:
                    content = _f.read().strip()
                if not content:
                    # PID not written yet — another process just created it; back off
                    time.sleep(0.05)
                    continue
                owner_pid = int(content)
                os.kill(owner_pid, 0)  # raises OSError if process is gone
                if not _is_python_process(owner_pid):
                    raise ProcessLookupError
                return False            # still alive — let it be the worker
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

def detect_hardware_encoder():
    try:
        result = subprocess.run(
            [ffmpeg_exe, '-encoders'],
            capture_output=True, text=True,
            creationflags=_NO_WINDOW
        )
        encoders = result.stdout
        if 'h264_nvenc' in encoders:
            return 'h264_nvenc'
        if 'h264_amf' in encoders:
            return 'h264_amf'
        if 'h264_qsv' in encoders:
            return 'h264_qsv'
        return 'libx264'
    except Exception:
        return 'libx264'

# Add to this list if found more video file formats
video_extensions = (
    '.mkv', '.mov', '.avi', '.wmv', '.flv', '.webm', '.mpeg', '.mpg', '.m4v',
    '.3gp', '.3g2', '.ts', '.mts', '.m2ts', '.divx', '.vob', '.ogv', '.rm',
    '.rmvb', '.asf', '.f4v', '.dv', '.drc', '.mxf', '.roq', '.viv', '.amv',
    '.mp2', '.mpv', '.m2ts'
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

def _run_ffmpeg(cmd, duration, set_progress):
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            creationflags=_NO_WINDOW)
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
    return proc.returncode, b''.join(stderr_buf)

def process_file(file, set_status=None, set_progress=None):
    if not (os.path.isfile(file) and file.lower().endswith(video_extensions)):
        return None  # skipped (not a recognised video file)
    base, ext = os.path.splitext(file)
    output_file = f'{base}.mp4'
    name = os.path.basename(file)

    if set_status:
        set_status(f'Converting: {name}')

    duration = _get_duration(file)

    # Try fast copy first (no re-encoding)
    rc, stderr = _run_ffmpeg(
        [ffmpeg_exe, '-y', '-i', base + ext, '-c:v', 'copy', '-c:a', 'aac',
         '-progress', 'pipe:1', '-nostats', output_file],
        duration, set_progress
    )

    # If copy failed, re-encode with hardware acceleration
    if rc != 0:
        hw_encoder = detect_hardware_encoder()
        if set_status:
            set_status(f'Re-encoding ({hw_encoder}): {name}')
        if set_progress:
            set_progress(0)
        rc, stderr = _run_ffmpeg([
            ffmpeg_exe, '-y', '-i', base + ext,
            '-c:v', hw_encoder, '-preset', 'fast',
            '-c:a', 'aac',
            '-progress', 'pipe:1', '-nostats', output_file
        ], duration, set_progress)

    if rc != 0:
        err = stderr.decode(errors='replace').strip().splitlines()
        msg = err[-1] if err else 'ffmpeg failed'
        _log(f'FAIL {name}: {msg}')
        return msg
    if set_progress:
        set_progress(100)
    _log(f'OK   {name} -> {os.path.basename(output_file)}')
    return True  # success

# --- Worker thread: drains the queue and updates the GUI ---
def _worker(set_status, set_progress, set_file_label, root):
    # Wait a bit longer so every racing instance finishes its _enqueue write
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
                # Give late-arriving instances a couple more chances before quitting
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
            elif res is not None:  # error string
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
    root.title('Video Converter')
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