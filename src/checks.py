import os
import subprocess
from typing import Dict, List


def check_mountpoint(path: str) -> Dict:
    """
    Verify path (or one of its parents) is an actual mount point.
    Walks up the directory tree since media paths are often subdirectories
    of the actual mount point rather than mount points themselves.
    """
    if not os.path.exists(path):
        return {"pass": False, "detail": f"Path does not exist: {path}"}

    # Walk up the tree to find the nearest mount point
    check = path
    while True:
        try:
            result = subprocess.run(
                ["mountpoint", "-q", check],
                capture_output=True, timeout=5
            )
            if result.returncode == 0:
                if check == path:
                    return {"pass": True, "detail": f"Mounted: {path}"}
                else:
                    return {"pass": True, "detail": f"Path accessible via mount at {check}"}
        except FileNotFoundError:
            # mountpoint binary unavailable — just check path exists and is non-empty
            try:
                if os.path.exists(path) and os.listdir(path):
                    return {"pass": True, "detail": f"Path accessible: {path}"}
                elif os.path.exists(path):
                    return {"pass": False, "detail": f"Path exists but is empty: {path}"}
                return {"pass": False, "detail": f"Path does not exist: {path}"}
            except Exception as e:
                return {"pass": False, "detail": f"Path check error: {e}"}
        except Exception as e:
            return {"pass": False, "detail": f"Mount check error: {e}"}

        parent = os.path.dirname(check)
        if parent == check:
            # Reached filesystem root — path is accessible, just not a named mount
            return {"pass": True, "detail": f"Path accessible: {path}"}
        check = parent


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


def check_file_threshold(path: str, min_threshold: float, plex_count: int) -> Dict:
    """
    Validate file count on disk using ratio check only.
    disk_count / plex_count must be >= min_threshold.
    If plex_count is 0 or unavailable, just verify path is non-empty.
    """
    disk_count = count_files(path)

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

    # Plex count unavailable — just verify path has at least 1 file
    if disk_count == 0:
        return {
            "pass":       False,
            "disk_count": 0,
            "plex_count": 0,
            "detail":     "No files found on disk (path may be empty or unmounted)"
        }
    return {
        "pass":       True,
        "disk_count": disk_count,
        "plex_count": 0,
        "detail":     f"{disk_count} files on disk (Plex count unavailable)"
    }