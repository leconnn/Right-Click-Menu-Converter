import os
import sys
import ctypes
import subprocess
import tkinter as tk
from tkinter import simpledialog, ttk
import tempfile
import time
import threading

# Get paths to bundled executables
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

_NVENC_SPEED_ARGS = [
    '-preset', 'p1',   # fastest NVENC preset - less per-frame motion search/mode decision work
    '-tune', 'hq',     # keep quality-oriented rate control even at the fast preset
    # NOTE: intentionally NOT disabling B-frames or lookahead here anymore -
    # those saved some encode time but cost far more in compression
    # efficiency than expected (B-frames especially), leading to bigger
    # output files than the source despite downscaling. p1+hq alone is a
    # much safer speed/size trade-off.
]

def _quality_args(encoder, quality=23):
    """Constant-quality encode args, used when compressing by resolution
    rather than by a target file size (no bitrate math needed)."""
    if encoder == 'h264_nvenc':
        return _NVENC_SPEED_ARGS + ['-rc', 'vbr', '-cq', str(quality), '-b:v', '0']
    if encoder == 'h264_amf':
        return ['-rc', 'cqp', '-qp_i', str(quality), '-qp_p', str(quality), '-qp_b', str(quality)]
    if encoder == 'h264_qsv':
        return ['-global_quality', str(quality)]
    # libx264 fallback
    return ['-crf', str(quality), '-preset', 'medium']

# Add to this list if found more video file formats
video_extensions = (
    '.mkv', '.mov', '.avi', '.wmv', '.flv', '.webm', '.mpeg', '.mpg', '.m4v',
    '.3gp', '.3g2', '.ts', '.mts', '.m2ts', '.divx', '.vob', '.ogv', '.rm',
    '.rmvb', '.asf', '.f4v', '.dv', '.drc', '.mxf', '.roq', '.viv', '.amv',
    '.mp2', '.mpv', '.m2ts', '.mp4'
)

def _probe_source(file):
    """Return (duration_seconds, size_mb, width, height) for a video file, or None."""
    try:
        size_mb = os.path.getsize(file) / (1024 * 1024)
        _ensure_hydrated(file)
        result = subprocess.run(
            [ffprobe_exe, '-v', 'error', '-select_streams', 'v:0',
             '-show_entries', 'stream=width,height:format=duration',
             '-of', 'default=noprint_wrappers=1', file],
            capture_output=True, text=True, creationflags=_NO_WINDOW
        )
        info = {}
        for line in result.stdout.strip().splitlines():
            if '=' in line:
                k, v = line.split('=', 1)
                info[k] = v
        duration = float(info.get('duration', 0))
        width = int(info.get('width', 0))
        height = int(info.get('height', 0))
        if duration <= 0 or width <= 0 or height <= 0:
            return None
        return duration, size_mb, width, height
    except Exception:
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

def _build_encode_cmd(file, output_file, hw_encoder, target_height, target_size_mb,
                       duration, audio_bitrate, decode_mode):
    """decode_mode: 'gpu_full' (decode+scale+encode all stay on GPU memory,
    needs an ffmpeg build with the CUDA scale filter), 'gpu_decode' (decode
    on GPU, scale on CPU - what we had before), or 'cpu' (fully CPU decode,
    universal fallback)."""
    cmd = [ffmpeg_exe]
    if decode_mode == 'gpu_full':
        cmd += ['-hwaccel', 'cuda', '-hwaccel_output_format', 'cuda']
    elif decode_mode == 'gpu_decode':
        cmd += ['-hwaccel', 'cuda']
    cmd += ['-i', file]

    if target_height:
        # Scale down only; keeps width even and preserves aspect ratio.
        if decode_mode == 'gpu_full':
            cmd += ['-vf', f"scale_cuda=-2:'min({target_height},ih)'"]
        else:
            cmd += ['-vf', f"scale=-2:'min({target_height},ih)'"]

    if target_size_mb is not None:
        target_bitrate = max(int((target_size_mb * 8192) / duration - audio_bitrate), 64)
        cmd += ['-c:v', hw_encoder]
        if hw_encoder == 'h264_nvenc':
            cmd += _NVENC_SPEED_ARGS
        cmd += ['-b:v', f'{target_bitrate}k']
    else:
        cmd += ['-c:v', hw_encoder] + _quality_args(hw_encoder)

    cmd += [
        '-b:a', f'{audio_bitrate}k',
        '-progress', 'pipe:1', '-nostats',
        '-y', output_file
    ]
    return cmd

def process_file(file, target_size_mb, target_height=None, set_status=None, set_progress=None):
    if not (os.path.isfile(file) and file.lower().endswith(video_extensions)):
        return None
    base, ext = os.path.splitext(file)
    name = os.path.basename(file)
    if set_status:
        set_status(f'Compressing: {name}')

    if not _ensure_hydrated(file, set_status):
        return 'could not download file from cloud storage'

    # Get video duration
    result = subprocess.run(
        [ffprobe_exe, '-v', 'error', '-show_entries', 'format=duration',
         '-of', 'default=noprint_wrappers=1:nokey=1', file],
        capture_output=True, text=True, creationflags=_NO_WINDOW
    )
    try:
        duration = float(result.stdout.strip())
    except ValueError:
        return f'could not read duration ({result.stderr.strip()[:80]})'

    audio_bitrate = 128  # kbps
    output_file = f'{base}_COMPRESSED.mp4'

    hw_encoder = detect_hardware_encoder()
    if set_status:
        set_status(f'Encoding ({hw_encoder}): {name}')

    use_hwaccel = (hw_encoder == 'h264_nvenc')
    # If we're scaling AND on NVENC, try keeping the whole pipeline
    # (decode -> scale -> encode) on the GPU first - avoids the CPU
    # round-trip that plain '-hwaccel cuda' still has for the scale step.
    decode_mode = 'gpu_full' if (use_hwaccel and target_height) else \
                  'gpu_decode' if use_hwaccel else 'cpu'

    cmd = _build_encode_cmd(file, output_file, hw_encoder, target_height,
                             target_size_mb, duration, audio_bitrate, decode_mode)
    rc, stderr = _run_ffmpeg(cmd, duration, set_progress)

    if rc != 0 and decode_mode == 'gpu_full':
        # This ffmpeg build likely lacks the CUDA scale filter - fall back
        # to GPU decode with CPU-side scaling.
        if set_status:
            set_status(f'Retrying (GPU decode, CPU scale): {name}')
        if set_progress:
            set_progress(0)
        decode_mode = 'gpu_decode'
        cmd = _build_encode_cmd(file, output_file, hw_encoder, target_height,
                                 target_size_mb, duration, audio_bitrate, decode_mode)
        rc, stderr = _run_ffmpeg(cmd, duration, set_progress)

    if rc != 0 and decode_mode == 'gpu_decode':
        # Source codec probably isn't NVDEC-decodable (rare but happens
        # with some older/odd formats) - fall back to full CPU decode.
        if set_status:
            set_status(f'Retrying (CPU decode): {name}')
        if set_progress:
            set_progress(0)
        cmd = _build_encode_cmd(file, output_file, hw_encoder, target_height,
                                 target_size_mb, duration, audio_bitrate, 'cpu')
        rc, stderr = _run_ffmpeg(cmd, duration, set_progress)

    if rc != 0:
        err = stderr.decode(errors='replace').strip().splitlines()
        return err[-1] if err else 'ffmpeg failed'
    if set_progress:
        set_progress(100)
    return True

# --- Worker thread: drains the queue and updates the GUI ---
def _worker(set_status, set_progress, set_file_label, root, target_size_mb, target_height=None):
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
            res = process_file(file, target_size_mb, target_height, set_status, set_progress)
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
        msg = f'Done — {processed} compressed, {len(errors)} failed: {errors[0]}'
        set_status(msg)
        time.sleep(4)
    root.after(0, root.destroy)

_RESOLUTION_PRESETS = (('1080p', 1080), ('720p', 720), ('480p', 480), ('360p', 360))

def _estimate_output_mb(source_info, target_height):
    """Rough estimate: for constant-quality (CRF) encodes, bitrate scales
    roughly with pixel count. Returns estimated MB, or None if unknown."""
    if source_info is None:
        return None
    duration, size_mb, width, height = source_info
    orig_bitrate_kbps = size_mb * 8192 / duration
    audio_kbps = 128
    video_kbps = max(orig_bitrate_kbps - audio_kbps, 64)

    new_height = min(target_height, height) if target_height else height
    ratio = (new_height / height) ** 2
    new_video_kbps = video_kbps * ratio
    return (new_video_kbps + audio_kbps) * duration / 8192

def _ask_target_size_and_resolution(sample_file=None):
    """Modal dialog: target size entry + resolution preset buttons below it,
    with a live estimated-output-size readout.
    Returns (size_str_or_None, target_height_or_None)."""
    result = {'size': None, 'height': None}
    buttons = {}
    source_info = _probe_source(sample_file) if sample_file else None

    dlg = tk.Tk()
    dlg.title('Target Size')
    dlg.resizable(False, False)

    frame = tk.Frame(dlg, padx=20, pady=16)
    frame.pack(fill='both', expand=True)

    tk.Label(frame, text='Target file size in MB (optional):',
             anchor='w', justify='left').pack(fill='x')
    entry = tk.Entry(frame, width=32)
    entry.pack(fill='x', pady=(4, 14))
    entry.focus_set()

    tk.Label(frame, text='...or just pick a resolution to downscale to:',
             anchor='w', justify='left').pack(fill='x')
    preset_frame = tk.Frame(frame)
    preset_frame.pack(fill='x', pady=(4, 6))

    _default_bg = tk.Button(preset_frame).cget('bg')

    estimate_var = tk.StringVar(value='')
    tk.Label(frame, textvariable=estimate_var, anchor='w', justify='left',
             fg='#444444', wraplength=340).pack(fill='x', pady=(0, 14))

    _auto_size = {'value': None}  # tracks what we auto-filled, so we don't clobber manual edits

    def _refresh_estimate():
        typed = entry.get().strip()
        if not typed:
            estimate_var.set('')
            return
        auto_note = ''
        if result['height'] is not None and typed == _auto_size['value']:
            preset_label = next(l for l, h in _RESOLUTION_PRESETS if h == result['height'])
            auto_note = f'  (auto-estimated for {preset_label}, edit to override)'
        source_note = f'  — source: {source_info[1]:.1f} MB' if source_info else ''
        estimate_var.set(f'Target size: {typed} MB{auto_note}{source_note}')

    def _toggle_resolution(height):
        was_selected = result['height'] == height
        result['height'] = None if was_selected else height
        for h, b in buttons.items():
            if h == result['height']:
                b.configure(relief='sunken', bg='#cfe3ff')
            else:
                b.configure(relief='raised', bg=_default_bg)

        current = entry.get().strip()
        if result['height'] is not None:
            # Selecting a preset: fill the size field with the estimate for
            # this resolution, so compressing actually bitrate-targets that
            # size instead of leaving NVENC's CQ mode to spend whatever it
            # wants (which can badly overshoot on high-fps/high-motion
            # source, as CQ mode has no ceiling).
            est = _estimate_output_mb(source_info, result['height'])
            if est is not None and (not current or current == _auto_size['value']):
                est_str = f'{est:.0f}'
                entry.delete(0, tk.END)
                entry.insert(0, est_str)
                _auto_size['value'] = est_str
        else:
            # Deselecting: clear the field only if it's still our untouched
            # auto-fill (leave any manual edit alone).
            if current == _auto_size['value']:
                entry.delete(0, tk.END)
                _auto_size['value'] = None
        _refresh_estimate()

    for label, height in _RESOLUTION_PRESETS:
        b = tk.Button(preset_frame, text=label, width=8,
                       command=lambda h=height: _toggle_resolution(h))
        b.pack(side='left', padx=(0, 6))
        buttons[height] = b

    entry.bind('<KeyRelease>', lambda e: _refresh_estimate())

    def _on_ok(event=None):
        result['size'] = entry.get()
        dlg.destroy()

    def _on_cancel(event=None):
        result['size'] = None
        result['height'] = None
        dlg.destroy()

    action_frame = tk.Frame(frame)
    action_frame.pack(fill='x', pady=(6, 0))
    tk.Button(action_frame, text='Compress', width=10, command=_on_ok).pack(side='right')
    tk.Button(action_frame, text='Cancel', width=10, command=_on_cancel).pack(
        side='right', padx=(0, 6))

    dlg.bind('<Return>', _on_ok)
    dlg.bind('<Escape>', _on_cancel)

    dlg.update_idletasks()
    w, h = dlg.winfo_reqwidth(), dlg.winfo_reqheight()
    sx = (dlg.winfo_screenwidth() - w) // 2
    sy = (dlg.winfo_screenheight() - h) // 2
    dlg.geometry(f'+{sx}+{sy}')

    dlg.grab_set()
    dlg.mainloop()

    return result['size'], result['height']

# --- Main: enqueue this batch, then become the worker if no one else is ---
_enqueue(sys.argv[1:])

if _try_lock():
    # Ask for target size and/or a resolution preset first
    _sample = next((f for f in sys.argv[1:]
                     if os.path.isfile(f) and f.lower().endswith(video_extensions)), None)
    answer, target_height = _ask_target_size_and_resolution(_sample)

    target_size_mb = None
    if answer is not None and answer.strip():
        try:
            target_size_mb = float(answer)
        except ValueError:
            pass

    if target_size_mb is not None or target_height is not None:
        root = tk.Tk()
        root.title('Video Compressor')
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
                  root, target_size_mb, target_height),
            daemon=True
        )
        t.start()

        root.protocol("WM_DELETE_WINDOW", lambda: (_unlock(), root.destroy()))
        root.mainloop()
    else:
        # Drain queue and release lock without processing
        while _dequeue():
            pass
        _unlock()
