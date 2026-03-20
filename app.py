import logging
import os
import threading
from datetime import datetime, timedelta

from flask import Flask, jsonify, render_template, request
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from src.config import load_config, AppConfig, PlexInstanceConfig, LibraryConfig
from src.plex_client import PlexClient
from src import runner
from src.runner import get_scheduling_enabled, set_scheduling_enabled

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("emptyarr")

# ── Bootstrap ─────────────────────────────────────────────────────────────────
CONFIG_PATH = os.environ.get("CONFIG_PATH", "data/config.yml")
config: AppConfig = load_config(CONFIG_PATH)
logging.getLogger().setLevel(config.log_level.upper())

# One PlexClient per instance
plex_clients: dict[str, PlexClient] = {
    inst.name: PlexClient(inst.url, inst.token)
    for inst in config.instances
}

app       = Flask(__name__)
scheduler = BackgroundScheduler()
_next_runs: dict = {}   # f"{instance_name}::{library_name}" -> ISO string


# ── Scheduler setup ───────────────────────────────────────────────────────────

def _job_key(instance_name: str, library_name: str) -> str:
    return f"{instance_name}::{library_name}"


def make_job(inst: PlexInstanceConfig, lib: LibraryConfig):
    def job():
        plex = plex_clients[inst.name]
        plex_checks = runner.run_instance_checks(inst, plex)
        runner.run_library(inst, lib, config, plex, plex_checks=plex_checks)
        _update_next(inst.name, lib.name)
    return job


def _update_next(instance_name: str, library_name: str):
    key = _job_key(instance_name, library_name)
    job = scheduler.get_job(key)
    if job and job.next_run_time:
        _next_runs[key] = job.next_run_time.isoformat()


for inst in config.instances:
    for lib in inst.libraries:
        parts = lib.cron.split()
        if len(parts) != 5:
            logger.warning(f"Invalid cron '{lib.cron}' for "
                           f"{inst.name}/{lib.name} — defaulting to hourly")
            parts = ["0", "*", "*", "*", "*"]

        key = _job_key(inst.name, lib.name)
        scheduler.add_job(
            make_job(inst, lib),
            CronTrigger(
                minute=parts[0], hour=parts[1],
                day=parts[2], month=parts[3], day_of_week=parts[4],
            ),
            id=key,
            name=f"{inst.name} / {lib.name}",
            replace_existing=True,
        )

scheduler.start()
for inst in config.instances:
    for lib in inst.libraries:
        _update_next(inst.name, lib.name)


# ── Template context helpers ──────────────────────────────────────────────────

def _build_ui_instances():
    inst_status = runner.get_instance_status()
    result = []
    for inst in config.instances:
        libs = []
        for lib in inst.libraries:
            key = _job_key(inst.name, lib.name)
            libs.append({
                "name":     lib.name,
                "type":     lib.type,
                "paths":    [{"path": p.path, "type": p.type} for p in lib.paths],
                "cron":     lib.cron,
                "next_run": _next_runs.get(key, "—"),
                "status":   inst_status.get(inst.name, {}).get(lib.name, {}),
            })
        result.append({
            "name":      inst.name,
            "url":       inst.url,
            "libraries": libs,
        })
    return result


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html",
        instances=_build_ui_instances(),
        config_missing=config.config_missing,
    )


@app.route("/api/status")
def api_status():
    return jsonify({
        "instances":          _build_ui_instances(),
        "next_runs":          _next_runs,
        "global_checks":      runner.get_last_global_checks(),
        "history_count":      len(runner.get_history()),
        "scheduling_enabled": get_scheduling_enabled(),
        "config_missing":     config.config_missing,
    })


@app.route("/api/history")
def api_history():
    return jsonify(runner.get_history())


@app.route("/api/checks")
def api_checks():
    """Run Plex reachability checks for all instances. No library action."""
    results = {}
    for inst in config.instances:
        plex = plex_clients[inst.name]
        results[inst.name] = runner.run_instance_checks(inst, plex)
    return jsonify(results)


@app.route("/api/scheduling", methods=["POST"])
def api_scheduling():
    data    = request.get_json(silent=True) or {}
    enabled = bool(data.get("enabled", True))
    set_scheduling_enabled(enabled)
    return jsonify({"scheduling_enabled": enabled})


def _trigger(instance_name: str, library_name: str, dry_run: bool = False):
    inst = next((i for i in config.instances if i.name == instance_name), None)
    lib  = next((l for l in inst.libraries if l.name == library_name), None) if inst else None
    if not inst or not lib:
        return False
    plex = plex_clients[inst.name]
    def _run():
        plex_checks = runner.run_instance_checks(inst, plex)
        runner.run_library(inst, lib, config, plex,
                           plex_checks=plex_checks, dry_run=dry_run)
    threading.Thread(target=_run, daemon=True).start()
    return True


@app.route("/api/run/<instance_name>/<library_name>", methods=["POST"])
def api_run_library(instance_name: str, library_name: str):
    if _trigger(instance_name, library_name, dry_run=False):
        return jsonify({"status": "triggered",
                        "instance": instance_name, "library": library_name})
    return jsonify({"error": "not found"}), 404


@app.route("/api/dryrun/<instance_name>/<library_name>", methods=["POST"])
def api_dryrun_library(instance_name: str, library_name: str):
    if _trigger(instance_name, library_name, dry_run=True):
        return jsonify({"status": "dry_run_triggered",
                        "instance": instance_name, "library": library_name})
    return jsonify({"error": "not found"}), 404


@app.route("/api/run/all", methods=["POST"])
def api_run_all():
    def _run():
        for inst in config.instances:
            plex = plex_clients[inst.name]
            plex_checks = runner.run_instance_checks(inst, plex)
            for lib in inst.libraries:
                runner.run_library(inst, lib, config, plex,
                                   plex_checks=plex_checks)
    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"status": "triggered"})


@app.route("/api/dryrun/all", methods=["POST"])
def api_dryrun_all():
    def _run():
        for inst in config.instances:
            plex = plex_clients[inst.name]
            plex_checks = runner.run_instance_checks(inst, plex)
            for lib in inst.libraries:
                runner.run_library(inst, lib, config, plex,
                                   plex_checks=plex_checks, dry_run=True)
    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"status": "dry_run_triggered"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8222, debug=False)