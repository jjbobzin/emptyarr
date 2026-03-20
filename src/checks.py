import os
import subprocess
from typing import Dict, List


def check_mountpoint(path: str) -> Dict:
    """Verify path is an actual mount point, not an empty local dir."""
    try:
        result = subprocess.run(
            ["mountpoint", "-q", path],
            capture_output=True, timeout=5
        )
        if result.returncode == 0:
            return {"pass": True, "detail": f"Mounted: {path}"}
        return {"pass": False, "detail": f"Not a mount point: {path}"}
    except FileNotFoundError:
        # mountpoint binary unavailable — fall back to existence + non-empty check
        try:
            entries = os.listdir(path)
            if entries:
                return {"pass": True, "detail": f"Path accessible: {path}"}
            return {"pass": False, "detail": f"Path exists but is empty: {path}"}
        except Exception as e:
            return {"pass": False, "detail": f"Path check error: {e}"}
    except Exception as e:
        return {"pass": False, "detail": f"Mountpoint check error: {e}"}


def check_symlinks(path: str, sample_size: int = 50) -> Dict:
    """
    Sample up to sample_size symlinks under path, verify targets resolve.
    Fails if >10% of sampled symlinks are broken.
    Checks both file symlinks and directory symlinks (e.g. movie folders).
    """
    if not os.path.exists(path):
        return {"pass": False, "detail": f"Path does not exist: {path}"}

    symlinks_checked = 0
    symlinks_broken  = 0
    broken_examples  = []

    try:
        for root, dirs, files in os.walk(path, followlinks=False):
            # Check file symlinks
            for fname in files:
                full = os.path.join(root, fname)
                if os.path.islink(full):
                    symlinks_checked += 1
                    if not os.path.exists(full):
                        symlinks_broken += 1
                        if len(broken_examples) < 3:
                            broken_examples.append(os.path.relpath(full, path))
                if symlinks_checked >= sample_size:
                    break
            # Check directory symlinks (e.g. entire movie folders as symlinks)
            for d in dirs:
                full = os.path.join(root, d)
                if os.path.islink(full):
                    symlinks_checked += 1
                    if not os.path.exists(full):
                        symlinks_broken += 1
                        if len(broken_examples) < 3:
                            broken_examples.append(os.path.relpath(full, path))
                if symlinks_checked >= sample_size:
                    break
            if symlinks_checked >= sample_size:
                break
    except PermissionError as e:
        return {"pass": False, "detail": f"Permission error: {e}"}

    if symlinks_checked == 0:
        return {"pass": True, "detail": f"No symlinks found in {path} — skipped"}

    broken_pct = symlinks_broken / symlinks_checked
    if broken_pct > 0.10:
        examples = ", ".join(broken_examples)
        return {
            "pass": False,
            "detail": (f"{symlinks_broken}/{symlinks_checked} sampled symlinks broken "
                       f"({broken_pct*100:.0f}%) — e.g. {examples}")
        }
    return {
        "pass": True,
        "detail": (f"Symlinks OK: {symlinks_broken}/{symlinks_checked} broken in sample "
                   f"({broken_pct*100:.0f}%)")
    }


def count_files(path: str) -> int:
    """
    Count symlinks and files under path without following symlinks.
    For debrid/symlink libraries the symlinks themselves are the media items
    so we count them directly rather than following into their targets.
    """
    total = 0
    if not os.path.exists(path):
        return 0
    for root, dirs, files in os.walk(path, followlinks=False):
        # Count all files (includes symlinks reported as files)
        total += len(files)
        # Count directory symlinks (movie folders that are themselves symlinks)
        total += sum(1 for d in dirs
                     if os.path.islink(os.path.join(root, d)))
    return total


def check_file_threshold(path: str, min_files: int,
                          min_threshold: float, plex_count: int) -> Dict:
    """
    1. disk count must be >= min_files (absolute floor)
    2. disk count must be >= min_threshold * plex_count (ratio)
    """
    disk_count = count_files(path)

    if disk_count < min_files:
        return {
            "pass":       False,
            "disk_count": disk_count,
            "plex_count": plex_count,
            "detail":     (f"Only {disk_count} files on disk, "
                           f"minimum floor is {min_files}")
        }

    if plex_count > 0:
        ratio = disk_count / plex_count
        if ratio < min_threshold:
            return {
                "pass":       False,
                "disk_count": disk_count,
                "plex_count": plex_count,
                "detail":     (f"Ratio {ratio*100:.1f}% below threshold "
                               f"{min_threshold*100:.0f}% "
                               f"({disk_count} on disk / {plex_count} in Plex)")
            }
        return {
            "pass":       True,
            "disk_count": disk_count,
            "plex_count": plex_count,
            "detail":     (f"OK: {ratio*100:.1f}% "
                           f"({disk_count} on disk / {plex_count} in Plex)")
        }

    return {
        "pass":       True,
        "disk_count": disk_count,
        "plex_count": 0,
        "detail":     f"{disk_count} files on disk (Plex count unavailable)"
    }