import os
import sys
import subprocess
import tkinter as tk
from tkinter import simpledialog

# Get paths to bundled executables
script_dir = os.path.dirname(os.path.abspath(__file__))
bin_dir = os.path.join(script_dir, 'bin')
ffmpeg_exe = os.path.join(bin_dir, 'ffmpeg.exe')
ffprobe_exe = os.path.join(bin_dir, 'ffprobe.exe')

# Fall back to system PATH if bundled executables don't exist
if not os.path.exists(ffmpeg_exe):
    ffmpeg_exe = 'ffmpeg'
if not os.path.exists(ffprobe_exe):
    ffprobe_exe = 'ffprobe'

selected_files = sys.argv[1:]
# print("Selected files:", selected_files)

# Add to this list if found more video file formats
video_extensions = (
    '.mkv', '.mov', '.avi', '.wmv', '.flv', '.webm', '.mpeg', '.mpg', '.m4v', 
    '.3gp', '.3g2', '.ts', '.mts', '.m2ts', '.divx', '.vob', '.ogv', '.rm', 
    '.rmvb', '.asf', '.f4v', '.dv', '.drc', '.mxf', '.roq', '.viv', '.amv', 
    '.mp2', '.mpv', '.m2ts', '.mp4'
)

# Create a hidden tkinter window to get user input
root = tk.Tk()
root.withdraw()

# Ask user for target size in megabytes
target_size_mb = simpledialog.askstring(
    "Target Size",
    "Enter target file size in megabytes (e.g., 10):"
)

if target_size_mb is None or target_size_mb.strip() == "":
    sys.exit(0)

try:
    target_size_mb = float(target_size_mb)
except ValueError:
    sys.exit(0)

for file in selected_files:
    if os.path.isfile(file) and file.lower().endswith(video_extensions):
        print(file)
        base, ext = os.path.splitext(file)
        
        # Get video duration
        duration_cmd = [
            ffprobe_exe, '-v', 'error', '-show_entries', 'format=duration',
            '-of', 'default=noprint_wrappers=1:nokey=1', file
        ]
        result = subprocess.run(duration_cmd, capture_output=True, text=True)
        duration = float(result.stdout.strip())
        
        # Calculate target bitrate (in kbps)
        # Formula: (target_size_mb * 8192) / duration - audio_bitrate
        audio_bitrate = 128  # kbps
        target_bitrate = int((target_size_mb * 8192) / duration - audio_bitrate)
        
        # Ensure minimum bitrate
        if target_bitrate < 64:
            target_bitrate = 64
        
        output_file = f'{base}_COMPRESSED.mp4'
        
        subprocess.run([
            ffmpeg_exe, '-i', file,
            '-b:v', f'{target_bitrate}k',
            '-b:a', f'{audio_bitrate}k',
            '-y', output_file
        ])
