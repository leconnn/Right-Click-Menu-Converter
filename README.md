# Right Click Converters

Simple right-click context menu tools for quick media conversions on Windows. No GUI needed—just right-click any file and convert it instantly.

MORE FEATURES WILL BE ADDED THROUGH UPDATES

## What It Does

This collection adds convenient conversion options to your Windows right-click context menu:

### 🎵 Audio Conversion
Right-click any audio file → **"Convert to mp3"**
- Converts audio files to MP3 format (192k bitrate)
- Works with: WAV, AAC, FLAC, OGG, WMA, M4A, AIFF, OPUS, ALAC, and more

### 🎬 Video Conversion and Compression
Right-click any video → **"Convert to..."** with organized submenu:
- **Compress video** - Compresses video file to desired file size in `MB` (MegaBytes).
- **To .mp4** - Convert video to MP4 format (copy video stream, fast)
- **To audio →**
  - **To .mp3** - Extract audio as MP3 (high quality)
  - **To .wav** - Extract audio as WAV (uncompressed, 44.1kHz)

### 📄 PDF Conversion
Right-click any PDF → **"Convert to png"**
- Converts each PDF page to separate PNG images
- Perfect for extracting pages as images

### 🖼️ Images to PDF
Select one or more images → right-click → **"Images to PDF"**
- Opens a window to drag-reorder pages, pick a paper size (A4, Letter, Legal, Long/Folio, A3, A5, Tabloid, or the original image size), choose Portrait/Landscape, and Fit/Fill
- Combines all selected images into a single PDF saved next to the first image

### Supported Formats
**Audio:** WAV, AAC, FLAC, OGG, WMA, M4A, AIFF, OPUS, ALAC, MP2, MP1, AMR, DSD, PCM, APE, AU, RA, TTA

**Video:** MKV, MOV, AVI, WMV, FLV, WebM, MPEG, MPG, M4V, 3GP, 3G2, TS, MTS, M2TS, DivX, VOB, OGV, RM, RMVB, ASF, F4V, DV, DRC, MXF, ROQ, VIV, AMV, MP4

**Images:** JPG, JPEG, PNG, BMP, GIF, TIFF, TIF, WEBP

**PDF:** PDF

## Requirements

None. FFmpeg, Poppler, and Python itself are all bundled inside the installer — nothing needs to be downloaded or installed separately.

## Installation

1. Run `RightClickConvert_Setup.exe`
2. On the **Select Components** page, check which converters you want (each video sub-feature — To .mp4, To .mp3, To .wav, Compress — can be toggled individually). Everything is checked by default.
3. Files are installed to `C:\Program Files\Right Click Converters\`
4. Context menu entries are created only for the components you selected
5. Done! Start right-clicking files to convert them

## How to Use

1. Right-click any supported file (or select multiple images for "Images to PDF")
2. Choose your conversion option from the menu
3. Converted file appears in the same folder
4. That's it!

**Example:**
- Right-click `vacation.mkv` → "Convert to..." → "To .mp4"
- Result: `vacation.mp4` created in the same folder

## Technical Details

- Each converter is a standalone `.exe` (built with PyInstaller) — no Python installation required on your machine
- Uses a bundled **FFmpeg** for audio/video conversion
- Uses a bundled **pdftoppm** (from Poppler) for PDF conversion
- Uses bundled **Pillow** for image-to-PDF conversion
- All scripts may or may not open a terminal window (`.pyw` files)
- Context menu only appears for supported file types
- Converted files keep the original filename with a new extension (or, for Images to PDF, are named after the first selected image)

## Uninstallation

1. Windows Settings → Apps → Installed apps
2. Find "Right Click Converters"
3. Click Uninstall
4. All files and registry entries are removed automatically

## License

Free to use and modify, but do credit me.
