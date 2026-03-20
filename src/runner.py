import json
import logging
import os
import threading
from datetime import datetime
from typing import Dict, List, Optional

from src.config import AppConfig, LibraryConfig, PathConfig, PlexInstanceConfig
from src.plex_client import PlexClient
from src.checks import check_mountpoint, check_symlinks, check_file_threshold
from src.providers import check_provider
from src import notifications

logger = logging.getLogger("emptyarr")

MAX_HISTORY      = 100
_history: List[Dict]          = []
_instance_status: Dict        = {}   # instance_name -> {library_name -> status}
_last_global_checks: Dict     = {}   # instance_name -> {check_name -> result}
_scheduling_enabled: bool     = True
_lock = threading.Lock()

_STATE_FILE = os.environ.get("STATE_FILE", "data/state.json")


def _load_state():
    global _scheduling_enabled
    try:
        if os.path.exists(_STATE_FILE):
            with open(_STATE_FILE) as f:
                state = json.load(f)
                _scheduling_enabled = state.get("scheduling_enabled", True)
    except Exception:
        pass


def _save_state():
    try:
        os.makedirs(os.path.dirname(_STATE_FILE), exist_ok=True)
        with open(_STATE_FILE, "w") as f:
            json.dump({"scheduling_enabled": _scheduling_enabled}, f)
    except Exception:
        pass


_load_state()


# ── State accessors ───────────────────────────────────────────────────────────

def get_history() -> List[Dict]:
    with _lock:
        return list(_history)

def get_instance_status() -> Dict:
    with _lock:
        return dict(_instance_status)

def get_last_global_checks() -> Dict:
    with _lock:
        return dict(_last_global_checks)

def get_scheduling_enabled() -> bool:
    with _lock:
        return _scheduling_enabled

def set_scheduling_enabled(enabled: bool):
    global _scheduling_enabled
    with _lock:
        _scheduling_enabled = enabled
    _save_state()
    logger.info(f"Scheduling {'enabled' if enabled else 'paused'}")


# ── History recording ─────────────────────────────────────────────────────────

def _record(instance_name: str, library_name: str, status: str,
            checks: Dict, message: str, removed_items: List[Dict] = None):
    record = {
        "timestamp":     datetime.now().isoformat(),
        "instance":      instance_name,
        "library":       library_name,
        "status":        status,   # success | skipped | error | dry_run
        "checks":        checks,
        "message":       message,
        "removed_items": removed_items or [],
        "removed_count": len(removed_items or []),
    }
    with _lock:
        _history.insert(0, record)
        if len(_history) > MAX_HISTORY:
            _history.pop()
        if instance_name not in _instance_status:
            _instance_status[instance_name] = {}
        _instance_status[instance_name][library_name] = {
            "last_run":      record["timestamp"],
            "last_status":   status,
            "last_message":  message,
            "removed_count": record["removed_count"],
        }
    return record


# ── Per-path checks ───────────────────────────────────────────────────────────

def _run_path_checks(path_cfg: PathConfig, plex_count: int) -> Dict:
    """
    Run all checks appropriate for a single path based on its type.
    Returns dict of check_name -> result.
    """
    results = {}
    label = path_cfg.path.split("/")[-1] or path_cfg.path  # short label

    # 1. Mountpoint — always
    results[f"Mount ({label})"] = check_mountpoint(path_cfg.path)

    # 2. Symlink resolution — debrid and usenet only
    if path_cfg.type in ("debrid", "usenet"):
        results[f"Symlinks ({label})"] = check_symlinks(path_cfg.path)

    # 3. File threshold — always
    results[f"Files ({label})"] = check_file_threshold(
        path_cfg.path, path_cfg.min_threshold, plex_count
    )

    # 4. Provider API checks — optional, per provider configured on this path
    for pc in path_cfg.provider_checks:
        check_name = f"{pc.type.capitalize()} API ({label})"
        results[check_name] = check_provider(pc.type, pc.api_key)

    return results


# ── Plex instance global checks ───────────────────────────────────────────────

def run_instance_checks(instance: PlexInstanceConfig,
                        plex: PlexClient) -> Dict:
    """Run Plex reachability check for an instance. Store result."""
    checks = {
        f"Plex ({instance.name})": plex.check_reachable(),
    }
    with _lock:
        _last_global_checks[instance.name] = checks
    return checks


# ── Library runner ────────────────────────────────────────────────────────────

def run_library(instance: PlexInstanceConfig, library: LibraryConfig,
                config: AppConfig, plex: PlexClient,
                plex_checks: Optional[Dict] = None,
                dry_run: bool = False):
    """
    Full run for one library:
    1. Plex reachability
    2. Per-path checks (mount, symlinks, file threshold, provider APIs)
    3. If all pass: snapshot trash → (unless dry_run) emptyTrash
    4. Notify
    """
    mode = "DRY RUN" if dry_run else "run"
    logger.info(f"[{instance.name} / {library.name}] Starting {mode}")

    # Scheduling gate — cron runs only, not manual/dry
    if not dry_run and not get_scheduling_enabled():
        logger.info(f"[{instance.name} / {library.name}] Scheduling paused — skipping")
        _record(instance.name, library.name, "skipped", {},
                "Scheduling is paused")
        return

    # Resolve section ID
    section_id = library.section_id or plex.find_section_id(library.name)
    if not section_id:
        msg = f"Could not find Plex section for '{library.name}'"
        logger.warning(f"[{instance.name} / {library.name}] {msg}")
        _record(instance.name, library.name, "error", {}, msg)
        if config.notify.on_skip and config.discord_webhook:
            notifications.notify_skip(config.discord_webhook,
                                      instance.name, library.name, msg)
        return

    # Plex reachability
    all_checks = dict(plex_checks or run_instance_checks(instance, plex))

    # Per-path checks
    plex_count = plex.get_library_item_count(section_id)
    for path_cfg in library.paths:
        path_checks = _run_path_checks(path_cfg, plex_count)
        all_checks.update(path_checks)

    # Evaluate
    failed = {n: c for n, c in all_checks.items() if not c["pass"]}

    if failed:
        failed_names = ", ".join(failed.keys())
        msg = f"Checks failed ({failed_names}) — trash empty skipped"
        logger.warning(f"[{instance.name} / {library.name}] {msg}")
        _record(instance.name, library.name, "skipped", all_checks, msg)
        if config.notify.on_failure and config.discord_webhook:
            notifications.notify_failure(config.discord_webhook,
                                         instance.name, library.name,
                                         failed, all_checks)
        return

    # Snapshot trash
    trash_items = plex.get_trash_items(section_id)
    trash_count = len(trash_items)

    if dry_run:
        msg = (f"[DRY RUN] Would remove {trash_count} item(s) from trash — no action taken"
               if trash_count > 0 else "[DRY RUN] Trash is already empty")
        logger.info(f"[{instance.name} / {library.name}] {msg}")
        _record(instance.name, library.name, "dry_run", all_checks, msg, trash_items)
        return

    logger.info(f"[{instance.name} / {library.name}] "
                f"{trash_count} item(s) in trash, emptying…")
    result = plex.empty_trash(section_id)

    if not result["ok"]:
        msg = f"emptyTrash failed: {result.get('error', result.get('http'))}"
        logger.error(f"[{instance.name} / {library.name}] {msg}")
        _record(instance.name, library.name, "error", all_checks, msg, trash_items)
        return

    msg = (f"Emptied {trash_count} item(s) from trash"
           if trash_count > 0 else "Trash was already empty")
    logger.info(f"[{instance.name} / {library.name}] {msg}")
    _record(instance.name, library.name, "success", all_checks, msg, trash_items)

    if config.notify.on_success and config.discord_webhook:
        notifications.notify_success(config.discord_webhook,
                                     instance.name, library.name,
                                     trash_items, all_checks)