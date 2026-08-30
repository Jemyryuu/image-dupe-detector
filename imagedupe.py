#!/usr/bin/env python3
import os
import sys
import time
import signal
import ctypes
import sqlite3
import argparse
import threading
from pathlib import Path
from typing import Dict, List, Tuple, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import numpy as np
from PIL import Image, ImageOps
from rich.console import Console
from rich.table import Table
from rich.progress import (
    Progress,
    SpinnerColumn,
    BarColumn,
    TextColumn,
    TimeRemainingColumn,
    MofNCompleteColumn,
)
from rich.panel import Panel
from rich.prompt import Confirm

IMAGE_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif",
    ".tiff", ".tif", ".jfif", ".avif", ".heic", ".ico"
}

console = Console(force_terminal=True)


class ProcessController:
    """Manages graceful cancellation via keyboard shortcuts ('q', 'Esc', 'Ctrl+C')."""

    def __init__(self):
        self.stop_requested = threading.Event()
        self._listener_thread: Optional[threading.Thread] = None
        signal.signal(signal.SIGINT, self._handle_sigint)

    def _handle_sigint(self, signum, frame):
        self.stop_requested.set()

    def start_key_listener(self):
        if os.name == "nt":
            try:
                import msvcrt

                def _listen():
                    while not self.stop_requested.is_set():
                        try:
                            if msvcrt.kbhit():
                                ch = msvcrt.getch()
                                if ch in (b"q", b"Q", b"\x1b", b"\x03"):
                                    self.stop_requested.set()
                                    break
                        except Exception:
                            pass
                        time.sleep(0.05)

                self._listener_thread = threading.Thread(target=_listen, daemon=True)
                self._listener_thread.start()
            except Exception:
                pass

    def stop_key_listener(self):
        self.stop_requested.set()

    def is_stopped(self) -> bool:
        return self.stop_requested.is_set()


class SHFILEOPSTRUCTW(ctypes.Structure):
    _fields_ = [
        ("hwnd", ctypes.c_void_p),
        ("wFunc", ctypes.c_uint),
        ("pFrom", ctypes.c_wchar_p),
        ("pTo", ctypes.c_wchar_p),
        ("fFlags", ctypes.c_ushort),
        ("fAnyOperationsAborted", ctypes.c_bool),
        ("hNameMappings", ctypes.c_void_p),
        ("lpszProgressTitle", ctypes.c_wchar_p),
    ]

FO_DELETE = 3
FOF_SILENT = 0x0004
FOF_NOCONFIRMATION = 0x0010
FOF_ALLOWUNDO = 0x0040
FOF_NOERRORUI = 0x0400


def send_to_recycle_bin(path: Path) -> bool:
    """Move file to the Windows Recycle Bin using shell32 API."""
    try:
        p_from = str(path.resolve()) + "\0\0"
        fileop = SHFILEOPSTRUCTW()
        fileop.hwnd = None
        fileop.wFunc = FO_DELETE
        fileop.pFrom = p_from
        fileop.pTo = None
        fileop.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI
        fileop.fAnyOperationsAborted = False
        fileop.hNameMappings = None
        fileop.lpszProgressTitle = None

        result = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(fileop))
        return result == 0 and not fileop.fAnyOperationsAborted
    except Exception:
        return False


def delete_file(path: Path, permanent: bool = False) -> Tuple[bool, str]:
    """Delete file, defaulting to Windows Recycle Bin unless permanent=True."""
    if not path.exists():
        return False, "File does not exist"

    if permanent or os.name != "nt":
        try:
            path.unlink()
            return True, "Permanently deleted"
        except Exception as e:
            return False, f"Failed to delete: {e}"
    else:
        success = send_to_recycle_bin(path)
        if success:
            return True, "Moved to Recycle Bin"
        else:
            try:
                path.unlink()
                return True, "Permanently deleted (Recycle Bin fallback)"
            except Exception as e:
                return False, f"Deletion error: {e}"


def compute_dhash(image_path: Path, hash_size: int = 8) -> Optional[Tuple[int, int, int]]:
    """Compute difference hash (dHash) and image resolution (64-bit integer, width, height)."""
    try:
        with Image.open(image_path) as img:
            try:
                img = ImageOps.exif_transpose(img)
            except Exception:
                pass

            width, height = img.size
            resized = img.convert("L").resize((hash_size + 1, hash_size), Image.Resampling.BILINEAR)
            pixels = np.array(resized, dtype=np.int32)
            diff = pixels[:, 1:] > pixels[:, :-1]

            decimal_hash = 0
            for bit in diff.flatten():
                decimal_hash = (decimal_hash << 1) | int(bit)

            return decimal_hash, width, height
    except Exception:
        return None


def hamming_distance(hash1: int, hash2: int) -> int:
    """Calculate the Hamming distance between two 64-bit hashes."""
    return (hash1 ^ hash2).bit_count()


class HashCache:
    """Persists computed dHash and file metadata in SQLite to skip redundant processing."""

    def __init__(self, cache_db_path: Path):
        self.db_path = cache_db_path
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS image_cache (
                    filepath TEXT PRIMARY KEY,
                    mtime REAL,
                    size INTEGER,
                    dhash TEXT,
                    width INTEGER,
                    height INTEGER
                )
            """)
            conn.commit()

    def get(self, path: Path) -> Optional[Tuple[int, int, int]]:
        try:
            stat = path.stat()
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT mtime, size, dhash, width, height FROM image_cache WHERE filepath = ?",
                    (str(path.resolve()),)
                )
                row = cursor.fetchone()
                if row:
                    cached_mtime, cached_size, cached_dhash, width, height = row
                    if abs(cached_mtime - stat.st_mtime) < 0.001 and cached_size == stat.st_size:
                        return int(cached_dhash), width, height
        except Exception:
            pass
        return None

    def set(self, path: Path, dhash: int, width: int, height: int):
        try:
            stat = path.stat()
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO image_cache (filepath, mtime, size, dhash, width, height)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (str(path.resolve()), stat.st_mtime, stat.st_size, str(dhash), width, height)
                )
                conn.commit()
        except Exception:
            pass


class ImageInfo:
    def __init__(self, path: Path, dhash: int, width: int, height: int, size_bytes: int, mtime: float):
        self.path = path
        self.dhash = dhash
        self.width = width
        self.height = height
        self.size_bytes = size_bytes
        self.mtime = mtime
        self.pixels_count = width * height


class DisjointSet:
    def __init__(self, elements: List[ImageInfo]):
        self.parent = {elem: elem for elem in elements}

    def find(self, item: ImageInfo) -> ImageInfo:
        if self.parent[item] != item:
            self.parent[item] = self.find(self.parent[item])
        return self.parent[item]

    def union(self, item1: ImageInfo, item2: ImageInfo):
        root1 = self.find(item1)
        root2 = self.find(item2)
        if root1 != root2:
            self.parent[root2] = root1


def cluster_duplicates(images: List[ImageInfo], threshold: int) -> List[List[ImageInfo]]:
    """Groups images into duplicate clusters where pairwise Hamming distance <= threshold."""
    if len(images) < 2:
        return []

    dsu = DisjointSet(images)
    n = len(images)

    for i in range(n):
        h1 = images[i].dhash
        for j in range(i + 1, n):
            dist = hamming_distance(h1, images[j].dhash)
            if dist <= threshold:
                dsu.union(images[i], images[j])

    clusters: Dict[ImageInfo, List[ImageInfo]] = {}
    for img in images:
        root = dsu.find(img)
        clusters.setdefault(root, []).append(img)

    return [group for group in clusters.values() if len(group) > 1]


def select_best_image(group: List[ImageInfo], strategy: str = "highest-res") -> Tuple[ImageInfo, List[ImageInfo]]:
    """Selects the keeper image based on chosen strategy and returns (keeper, list_of_duplicates)."""
    if strategy == "highest-res":
        sorted_group = sorted(
            group,
            key=lambda x: (x.pixels_count, x.size_bytes, -x.mtime),
            reverse=True
        )
    elif strategy == "largest-file":
        sorted_group = sorted(group, key=lambda x: (x.size_bytes, x.pixels_count), reverse=True)
    elif strategy == "oldest":
        sorted_group = sorted(group, key=lambda x: x.mtime)
    elif strategy == "newest":
        sorted_group = sorted(group, key=lambda x: x.mtime, reverse=True)
    else:
        sorted_group = group

    keeper = sorted_group[0]
    duplicates_to_delete = sorted_group[1:]
    return keeper, duplicates_to_delete


def format_size(size_bytes: int) -> str:
    """Format bytes to human-readable string."""
    for unit in ["B", "KB", "MB", "GB"]:
        if size_bytes < 1024.0:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.2f} TB"


def process_image(path: Path, cache: HashCache, stop_event: Optional[threading.Event] = None) -> Optional[ImageInfo]:
    if stop_event and stop_event.is_set():
        return None

    try:
        stat = path.stat()
        cached = cache.get(path)
        if cached is not None:
            dhash, width, height = cached
        else:
            if stop_event and stop_event.is_set():
                return None
            result = compute_dhash(path)
            if result is None:
                return None
            dhash, width, height = result
            cache.set(path, dhash, width, height)

        return ImageInfo(
            path=path,
            dhash=dhash,
            width=width,
            height=height,
            size_bytes=stat.st_size,
            mtime=stat.st_mtime
        )
    except Exception:
        return None


def run_scanner(
    target_dir: Path,
    recursive: bool = False,
    threshold: int = 2,
    keep_strategy: str = "highest-res",
    dry_run: bool = False,
    permanent: bool = False,
    auto_confirm: bool = False,
    threads: int = 8
):
    target_dir = target_dir.resolve()
    if not target_dir.is_dir():
        console.print(f"[bold red]Error:[/bold red] '{target_dir}' is not a valid directory.")
        sys.exit(1)

    controller = ProcessController()
    controller.start_key_listener()

    action_text = (
        "[bold yellow]DRY RUN (Preview Only)[/bold yellow]"
        if dry_run
        else ("[bold red]Permanent Delete[/bold red]" if permanent else "[bold green]Move to Recycle Bin[/bold green]")
    )

    console.print(Panel(
        f"[bold cyan]Image Duplicate Detector (Perceptual dHash)[/bold cyan]\n"
        f"[white]Target Directory:[/white] [green]{target_dir}[/green]\n"
        f"[white]Recursive:[/white] {recursive}  |  "
        f"[white]Threshold (Hamming Dist):[/white] [yellow]{threshold}[/yellow]  |  "
        f"[white]Keep Strategy:[/white] [magenta]{keep_strategy}[/magenta]\n"
        f"[white]Action:[/white] {action_text}\n"
        f"[dim]Stop Shortcut: Press [bold yellow]'q'[/bold yellow], [bold yellow]Esc[/bold yellow], or [bold yellow]Ctrl+C[/bold yellow] at any time to stop safely.[/dim]",
        title="[bold blue]Configuration[/bold blue]",
        border_style="blue"
    ))

    pattern = "**/*" if recursive else "*"
    all_files = [
        p for p in target_dir.glob(pattern)
        if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS
    ]

    if not all_files:
        console.print("[yellow]No supported images found in target directory.[/yellow]")
        return

    console.print(f"Found [bold cyan]{len(all_files)}[/bold cyan] image files. Computing perceptual hashes...")

    cache_file = target_dir / ".imagedupe_cache.db"
    cache = HashCache(cache_file)

    image_infos: List[ImageInfo] = []
    failed_count = 0
    was_cancelled = False

    try:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TimeRemainingColumn(),
            console=console
        ) as progress:
            task = progress.add_task("[cyan]Hashing images (Press 'q'/Esc/Ctrl+C to stop)...", total=len(all_files))

            with ThreadPoolExecutor(max_workers=threads) as executor:
                future_to_file = {
                    executor.submit(process_image, p, cache, controller.stop_requested): p
                    for p in all_files
                }

                for future in as_completed(future_to_file):
                    if controller.is_stopped():
                        was_cancelled = True
                        executor.shutdown(wait=False, cancel_futures=True)
                        break

                    try:
                        info = future.result()
                        if info is not None:
                            image_infos.append(info)
                        else:
                            failed_count += 1
                    except Exception:
                        failed_count += 1

                    progress.advance(task)
    except KeyboardInterrupt:
        was_cancelled = True
        controller.stop_requested.set()

    if was_cancelled or controller.is_stopped():
        console.print(f"\n[bold yellow][!] Process stopped by user ('q' / Esc / Ctrl+C).[/bold yellow]")
        console.print(f"[green][OK] Saved {len(image_infos)} image hashes to cache database. Progress was not lost.[/green]")
        return

    if failed_count > 0:
        console.print(f"[yellow]Warning: Failed to process {failed_count} unreadable/corrupted files.[/yellow]")

    console.print(f"Successfully processed [bold green]{len(image_infos)}[/bold green] images.")

    console.print("[cyan]Comparing hashes and clustering duplicates...[/cyan]")
    clusters = cluster_duplicates(image_infos, threshold=threshold)

    if not clusters:
        console.print("\n[bold green][OK] No duplicate images found. Your folder is clean.[/bold green]")
        return

    total_dupes_count = 0
    total_reclaimable_bytes = 0
    plan_to_delete: List[Tuple[ImageInfo, ImageInfo]] = []

    table = Table(title="[bold yellow]Duplicate Groups Detected[/bold yellow]", show_lines=True, expand=True)
    table.add_column("Group", style="cyan", justify="center", width=7)
    table.add_column("Action", style="bold", width=9)
    table.add_column("Image File", style="white", ratio=3)
    table.add_column("Resolution", style="green", width=12)
    table.add_column("File Size", style="magenta", width=11)
    table.add_column("dHash Dist", style="yellow", justify="center", width=12)

    for group_idx, group in enumerate(clusters, start=1):
        keeper, dupes = select_best_image(group, strategy=keep_strategy)

        rel_keeper_dir = keeper.path.parent.name
        table.add_row(
            str(group_idx),
            "[green]KEEP[/green]",
            f"[bold]{keeper.path.name}[/bold]\n[dim]{rel_keeper_dir}[/dim]",
            f"{keeper.width}x{keeper.height}",
            format_size(keeper.size_bytes),
            "-"
        )

        for dupe in dupes:
            dist = hamming_distance(keeper.dhash, dupe.dhash)
            total_dupes_count += 1
            total_reclaimable_bytes += dupe.size_bytes
            plan_to_delete.append((dupe, keeper))

            rel_dupe_dir = dupe.path.parent.name
            table.add_row(
                "",
                "[red]DELETE[/red]",
                f"{dupe.path.name}\n[dim]{rel_dupe_dir}[/dim]",
                f"{dupe.width}x{dupe.height}",
                format_size(dupe.size_bytes),
                f"Dist: {dist}"
            )

    console.print(table)
    console.print(
        f"\n[bold yellow]Summary:[/bold yellow] Found [bold red]{total_dupes_count}[/bold red] duplicate images across "
        f"[bold cyan]{len(clusters)}[/bold cyan] groups."
    )
    console.print(f"[bold green]Reclaimable Disk Space:[/bold green] [bold magenta]{format_size(total_reclaimable_bytes)}[/bold magenta]\n")

    if dry_run:
        console.print("[bold yellow][INFO] Dry-run mode enabled. No files were deleted.[/bold yellow]")
        console.print("Run without [cyan]--dry-run[/cyan] to perform the deletion.")
        return

    if not auto_confirm:
        dest_str = "permanently deleted" if permanent else "moved to the Recycle Bin"
        confirmed = Confirm.ask(
            f"[bold red]Are you sure you want to delete {total_dupes_count} duplicate files ({format_size(total_reclaimable_bytes)})? They will be {dest_str}.[/bold red]",
            default=False
        )
        if not confirmed:
            console.print("[yellow]Operation cancelled by user. No files were deleted.[/yellow]")
            return

    deleted_count = 0
    failed_delete = 0
    deleted_bytes = 0

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        console=console
    ) as progress:
        del_task = progress.add_task("[red]Deleting duplicates...", total=len(plan_to_delete))

        for dupe, _ in plan_to_delete:
            if controller.is_stopped():
                console.print("\n[bold yellow][!] Deletion stopped by user.[/bold yellow]")
                break

            success, msg = delete_file(dupe.path, permanent=permanent)
            if success:
                deleted_count += 1
                deleted_bytes += dupe.size_bytes
            else:
                failed_delete += 1
                console.print(f"[red]Error deleting {dupe.path.name}: {msg}[/red]")
            progress.advance(del_task)

    console.print(
        f"\n[bold green][OK] Completed![/bold green] Deleted [bold cyan]{deleted_count}[/bold cyan] files. "
        f"Freed [bold magenta]{format_size(deleted_bytes)}[/bold magenta] of space."
    )
    if failed_delete > 0:
        console.print(f"[bold red]Failed to delete {failed_delete} files.[/bold red]")


def resolve_directory_path(raw_path: str) -> Path:
    """Expands ~, %USERPROFILE%, and resolves library shortcuts ('Pictures', 'Downloads', etc.)."""
    clean_str = raw_path.strip('"\'')
    expanded = os.path.expandvars(os.path.expanduser(clean_str))
    path = Path(expanded)

    if path.exists():
        return path

    shortcut_map = {
        "pictures": Path.home() / "Pictures",
        "downloads": Path.home() / "Downloads",
        "desktop": Path.home() / "Desktop",
        "documents": Path.home() / "Documents",
        "videos": Path.home() / "Videos",
    }
    lowered = clean_str.strip('/\\').lower()
    if lowered in shortcut_map and shortcut_map[lowered].exists():
        return shortcut_map[lowered]

    return path


def main():
    parser = argparse.ArgumentParser(
        description="Perceptual Image Duplicate Detector using dHash.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python imagedupe.py Pictures -r --dry-run
  python imagedupe.py "C:\\Users\\YourName\\Pictures" -r
  python imagedupe.py ~/Pictures -r -y
        """
    )

    parser.add_argument(
        "directory",
        type=str,
        nargs="?",
        default=".",
        help="Target directory path or shortcut ('Pictures', 'Downloads', '~/Pictures', default: current directory)"
    )
    parser.add_argument(
        "-r", "--recursive",
        action="store_true",
        help="Scan subdirectories recursively"
    )
    parser.add_argument(
        "-t", "--threshold",
        type=int,
        default=2,
        help="Hamming distance tolerance (0 = exact visual match, 1-4 = near match, default: 2)"
    )
    parser.add_argument(
        "--keep",
        choices=["highest-res", "largest-file", "oldest", "newest"],
        default="highest-res",
        help="Strategy for which image to preserve in each duplicate group (default: highest-res)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview duplicates and space savings without deleting any files"
    )
    parser.add_argument(
        "--permanent",
        action="store_true",
        help="Permanently delete files instead of sending them to the Windows Recycle Bin"
    )
    parser.add_argument(
        "-y", "--yes",
        action="store_true",
        help="Bypass interactive confirmation prompt before deletion"
    )
    parser.add_argument(
        "--threads",
        type=int,
        default=8,
        help="Number of worker threads for parallel image hashing (default: 8)"
    )

    args = parser.parse_args()
    target_path = resolve_directory_path(args.directory)

    run_scanner(
        target_dir=target_path,
        recursive=args.recursive,
        threshold=args.threshold,
        keep_strategy=args.keep,
        dry_run=args.dry_run,
        permanent=args.permanent,
        auto_confirm=args.yes,
        threads=args.threads
    )


if __name__ == "__main__":
    main()
