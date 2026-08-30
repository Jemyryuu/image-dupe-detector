# Image Duplicate Detector (Perceptual dHash CLI)

A lightweight, high-performance command-line tool to scan, identify, and clean duplicate or near-duplicate images using **Difference Hash (`dHash`)**.

---

## Key Features

- **Perceptual Difference Hashing (`dHash`)**: Resilient against resizing, JPEG compression, and brightness alterations.
- **Lightning Fast**: Multi-threaded image processing with progress bars and $O(1)$ 64-bit bitwise Hamming distance comparisons.
- **Smart Quality Retention**: Automatically keeps the **highest resolution / best quality** image in each duplicate group and marks lower-quality duplicates for removal.
- **Safety First**:
  - **Recycle Bin Integration**: On Windows, deleted files are moved to the Recycle Bin by default, making deletions undoable.
  - **Instant Cancellation Shortcuts**: Press **`q`**, **`Esc`**, or **`Ctrl+C`** at any time to safely stop the process without losing cached progress.
  - **Dry-Run Preview (`--dry-run`)**: Preview duplicates, file sizes, and reclaimable disk space before touching files.
  - **Interactive Confirmation**: Prompts for confirmation before mass deletion (unless `-y` is passed).
- **Persistent Hash Caching**: Hashes and file metadata are saved in a local SQLite cache (`.imagedupe_cache.db`), so rescanning large folders only processes new or modified files.

---

## Installation

Ensure Python 3.10+ is installed, then install the dependencies:

```bash
pip install -r requirements.txt
```

---

## Usage Guide

### 1. Preview Duplicates (Dry Run - Recommended First Step)
Scan a directory and preview duplicate groups and reclaimable disk space without deleting anything:
```bash
python imagedupe.py "C:\path\to\your\images" --dry-run
```

### 2. Scan and Clean (Interactive Prompt)
Scan a folder and prompt for confirmation before moving duplicates to the Windows Recycle Bin:
```bash
python imagedupe.py "C:\path\to\your\images"
```

### 3. Recursive Subfolder Scanning
Scan all subfolders recursively using `-r`:
```bash
python imagedupe.py "C:\path\to\your\images" -r
```

### 4. Auto-Confirm Deletion
Bypass the confirmation prompt for automated / script workflows:
```bash
python imagedupe.py "C:\path\to\your\images" -y
```

### 5. Adjust Similarity Tolerance (`--threshold`)
The Hamming distance determines how strictly images must match (default is `2`):
- `0`: Exact visual match (identical pixels or exact resize).
- `1` – `2`: Near-identical (recompressed JPEG, resized, minor noise). *(Default)*
- `3` – `5`: Looser match (minor crops, subtle filters, small watermarks).

```bash
# Stricter match
python imagedupe.py "C:\path\to\your\images" --threshold 1

# Looser match
python imagedupe.py "C:\path\to\your\images" --threshold 4
```

### 6. Retention Strategy (`--keep`)
Choose which image to preserve in each duplicate group:
- `highest-res`: Keep highest resolution image *(Default)*
- `largest-file`: Keep largest file size in bytes
- `oldest`: Keep oldest file created/modified
- `newest`: Keep newest file created/modified

```bash
python imagedupe.py "C:\path\to\your\images" --keep largest-file
```

### 7. Permanent Deletion (`--permanent`)
Permanently deletes duplicates instead of moving them to the Windows Recycle Bin:
```bash
python imagedupe.py "C:\path\to\your\images" --permanent
```

---

## Supported Image Formats

`.jpg`, `.jpeg`, `.png`, `.webp`, `.bmp`, `.gif`, `.tiff`, `.jfif`, `.avif`, `.heic`, `.ico`
