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

### 2. Using Directory Shortcuts
You don't need to type full paths for common user folders. You can pass shortcuts like `Pictures`, `Downloads`, `Desktop`, or `~/Pictures`:
```bash
python imagedupe.py Pictures -r --dry-run
```

### 3. Scan and Clean (Interactive Prompt)
Scan a folder and prompt for confirmation before moving duplicates to the Windows Recycle Bin:
```bash
python imagedupe.py "C:\path\to\your\images"
```

### 4. Recursive Subfolder Scanning (`-r`)
Scan all subfolders recursively using `-r`:
```bash
python imagedupe.py Pictures -r
```

### 5. Multi-Threaded Acceleration (`--threads`)
Specify the number of worker threads for parallel image hashing (default: `8`). Increase this on multi-core CPUs and fast SSDs for large photo libraries (e.g. 10,000+ photos):
```bash
python imagedupe.py Pictures -r --threads 16
```

### 6. Auto-Confirm Deletion (`-y` / `--yes`)
Bypass the interactive confirmation prompt for automated or script workflows:
```bash
python imagedupe.py Pictures -r -y
```

### 7. Adjust Similarity Tolerance (`-t` / `--threshold`)
The Hamming distance determines how strictly images must match (default is `2`):
- `0`: Exact visual match (identical pixels or exact resize).
- `1` – `2`: Near-identical (recompressed JPEG, resized, minor noise). *(Default)*
- `3` – `5`: Looser match (minor crops, subtle filters, small watermarks).

```bash
# Stricter match
python imagedupe.py Pictures -r --threshold 1

# Looser match
python imagedupe.py Pictures -r --threshold 4
```

### 8. Retention Strategy (`--keep`)
Choose which image to preserve in each duplicate group:
- `highest-res`: Keep highest resolution image *(Default)*
- `largest-file`: Keep largest file size in bytes
- `oldest`: Keep oldest file created/modified
- `newest`: Keep newest file created/modified

```bash
python imagedupe.py Pictures -r --keep largest-file
```

### 9. Permanent Deletion (`--permanent`)
Permanently deletes duplicates instead of moving them to the Windows Recycle Bin:
```bash
python imagedupe.py Pictures -r --permanent -y
```

---

## All CLI Flags & Options Reference

| Flag | Short | Type | Default | Description |
| :--- | :--- | :--- | :--- | :--- |
| `directory` | | Positional | `.` (current dir) | Target directory path or shortcut (`Pictures`, `Downloads`, `Desktop`, `Documents`, `~/Pictures`) |
| `--recursive` | `-r` | Flag | `False` | Scan subdirectories recursively |
| `--threshold` | `-t` | Integer | `2` | Hamming distance tolerance (`0` = exact match, `1-4` = near match) |
| `--keep` | | Choice | `highest-res` | Image preservation strategy: `highest-res`, `largest-file`, `oldest`, `newest` |
| `--threads` | | Integer | `8` | Number of parallel worker threads for image hashing |
| `--dry-run` | | Flag | `False` | Preview duplicates and reclaimable space without modifying or deleting files |
| `--permanent` | | Flag | `False` | Permanently delete duplicates instead of sending to Windows Recycle Bin |
| `--yes` | `-y` | Flag | `False` | Bypass confirmation prompt before deleting files |

---

## Combining Flags & Shortcuts

You can combine any flags and bundle single-letter options:

```bash
# Scan Pictures recursively, auto-confirm delete, keep largest file, with 16 threads:
python imagedupe.py Pictures -ry --keep largest-file --threads 16

# Dry-run scan with loose threshold on Downloads:
python imagedupe.py Downloads -r --dry-run -t 4
```

---

## Stopping a Scan Mid-Process

You can safely interrupt a scan at any time by pressing:
- **`q`** (or **`Q`**)
- **`Esc`**
- **`Ctrl + C`**

All hashes computed up to that moment are safely preserved in `.imagedupe_cache.db`, so subsequent runs will resume instantly without re-hashing previously processed files.

---

## Supported Image Formats

`.jpg`, `.jpeg`, `.png`, `.webp`, `.bmp`, `.gif`, `.tiff`, `.jfif`, `.avif`, `.heic`, `.ico`
