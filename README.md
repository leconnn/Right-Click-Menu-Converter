# Right Click Converters

Simple right-click context menu tools for quick media conversions on Windows. No GUI needed—just right-click any file and convert it instantly.

## What It Does

This collection adds convenient conversion options to your Windows right-click context menu:

### 🎵 Audio Conversion
Right-click any audio file → **"Convert to mp3"**
- Converts audio files to MP3 format (192k bitrate)
- Works with: WAV, AAC, FLAC, OGG, WMA, M4A, AIFF, OPUS, ALAC, and more

### 🎬 Video Conversion
Right-click any video → **"Convert to..."** with organized submenu:
- **To .mp4** - Convert video to MP4 format (copy video stream, fast)
- **To audio →**
  - **To .mp3** - Extract audio as MP3 (high quality)
  - **To .wav** - Extract audio as WAV (uncompressed, 44.1kHz)

### 📄 PDF Conversion
Right-click any PDF → **"Convert to png"**
- Converts each PDF page to separate PNG images
- Perfect for extracting pages as images

### Supported Formats
**Audio:** WAV, AAC, FLAC, OGG, WMA, M4A, AIFF, OPUS, ALAC, MP2, MP1, AMR, DSD, PCM, APE, AU, RA, TTA

**Video:** MKV, MOV, AVI, WMV, FLV, WebM, MPEG, MPG, M4V, 3GP, 3G2, TS, MTS, M2TS, DivX, VOB, OGV, RM, RMVB, ASF, F4V, DV, DRC, MXF, ROQ, VIV, AMV, MP4

**PDF:** PDF

## Requirements

Before installing, you need these programs:

1. **Python 3.x** - [Download here](https://www.python.org/downloads/)
   - ✅ Check "Add Python to PATH" during installation

2. **FFmpeg** - [Download here](https://ffmpeg.org/download.html)
   - **Easy install with pip:**
     ```bash
     pip install ffmpeg-python
     ```
   - **Or download manually:** Extract and add to system PATH

3. **Poppler** - [Download here](https://github.com/oschwartz10612/poppler-windows/releases/)
   - **Easy install with conda:**
     ```bash
     conda install -c conda-forge poppler
     ```
   - **Or download manually:** Extract and add `bin` folder to system PATH

## Installation

1. Run `RightClickConvert_Setup.exe`
2. The installer checks for required dependencies
3. Files are installed to `C:\Program Files\Right Click Converters\`
4. Context menu entries are automatically created
5. Done! Start right-clicking files to convert them

## How to Use

1. Right-click any supported file
2. Choose your conversion option from the menu
3. Converted file appears in the same folder
4. That's it!

**Example:**
- Right-click `vacation.mkv` → "Convert to..." → "To .mp4"
- Result: `vacation.mp4` created in the same folder

## Technical Details

- Uses **FFmpeg** for audio/video conversion
- Uses **pdftoppm** (from Poppler) for PDF conversion
- All scripts may or may not open a terminal window (`.pyw` files)
- Context menu only appears for supported file types
- Converted files keep the original filename with new extension

## Uninstallation

1. Windows Settings → Apps → Installed apps
2. Find "Right Click Converters"
3. Click Uninstall
4. All files and registry entries are removed automatically

## License

Free to use and modify, but do credit me.
