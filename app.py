import logging
import os
import secrets
import threading
import yaml
from flask import Flask, jsonify, render_template, request, redirect, url_for, session

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from src.config import load_config, AppConfig, PlexInstanceConfig, LibraryConfig
from src.plex_client import PlexClient
from src.auth import require_auth, auth_enabled, check_credentials, is_authenticated, hash_password
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

plex_clients: dict[str, PlexClient] = {
    inst.name: PlexClient(inst.url, inst.token)
    for inst in config.instances
}

app            = Flask(__name__)
app.secret_key = os.environ.get("EMPTYARR_SECRET_KEY", secrets.token_hex(32))
scheduler      = BackgroundScheduler()
_next_runs: dict = {}


# ── Scheduler ─────────────────────────────────────────────────────────────────

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
    if job:
        # APScheduler 3.x uses next_fire_time
        nft = getattr(job, 'next_fire_time', None) or getattr(job, 'next_run_time', None)
        if nft:
            _next_runs[key] = nft.isoformat()


def _setup_scheduler():
    for inst in config.instances:
        for lib in inst.libraries:
            parts = lib.cron.split()
            if len(parts) != 5:
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
    for inst in config.instances:
        for lib in inst.libraries:
            _update_next(inst.name, lib.name)


_setup_scheduler()
scheduler.start()


# ── Template context ──────────────────────────────────────────────────────────

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

@app.route("/login", methods=["GET", "POST"])
def login():
    if not auth_enabled(config):
        return redirect(url_for("index"))
    if is_authenticated():
        return redirect(url_for("index"))
    error = None
    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        if check_credentials(username, password, config):
            session["authenticated"] = True
            return redirect(url_for("index"))
        error = "Invalid username or password"
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@require_auth
def index():
    return render_template("index.html",
        instances=_build_ui_instances(),
        config_missing=config.config_missing,
        auth_enabled=auth_enabled(config),
        config=config,
    )


@app.route("/api/status")
@require_auth
def api_status():
    return jsonify({
        "instances":          _build_ui_instances(),
        "next_runs":          _next_runs,
        "global_checks":      runner.get_last_global_checks(),
        "history_count":      len(runner.get_history()),
        "scheduling_enabled": get_scheduling_enabled(),
        "config_missing":     config.config_missing,
        "auth_enabled":       auth_enabled(),
    })


@app.route("/api/history")
@require_auth
def api_history():
    return jsonify(runner.get_history())


@app.route("/api/checks")
@require_auth
def api_checks():
    results = {}
    for inst in config.instances:
        plex = plex_clients[inst.name]
        results[inst.name] = runner.run_instance_checks(inst, plex)
    return jsonify(results)


@app.route("/api/scheduling", methods=["POST"])
@require_auth
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
                           plex_checks=plex_checks, dry_run=dry_run, manual=True)
    threading.Thread(target=_run, daemon=True).start()
    return True


@app.route("/api/run/<instance_name>/<library_name>", methods=["POST"])
@require_auth
def api_run_library(instance_name: str, library_name: str):
    if _trigger(instance_name, library_name):
        return jsonify({"status": "triggered"})
    return jsonify({"error": "not found"}), 404


@app.route("/api/dryrun/<instance_name>/<library_name>", methods=["POST"])
@require_auth
def api_dryrun_library(instance_name: str, library_name: str):
    if _trigger(instance_name, library_name, dry_run=True):
        return jsonify({"status": "dry_run_triggered"})
    return jsonify({"error": "not found"}), 404


@app.route("/api/run/all", methods=["POST"])
@require_auth
def api_run_all():
    def _run():
        for inst in config.instances:
            plex = plex_clients[inst.name]
            plex_checks = runner.run_instance_checks(inst, plex)
            for lib in inst.libraries:
                runner.run_library(inst, lib, config, plex,
                                   plex_checks=plex_checks, manual=True)
    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"status": "triggered"})


@app.route("/api/dryrun/all", methods=["POST"])
@require_auth
def api_dryrun_all():
    def _run():
        for inst in config.instances:
            plex = plex_clients[inst.name]
            plex_checks = runner.run_instance_checks(inst, plex)
            for lib in inst.libraries:
                runner.run_library(inst, lib, config, plex,
                                   plex_checks=plex_checks, dry_run=True, manual=True)
    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"status": "dry_run_triggered"})


# ── Wizard / Config endpoints ─────────────────────────────────────────────────

@app.route("/api/wizard/test-plex", methods=["POST"])
@require_auth
def api_test_plex():
    """Test a Plex connection and return available libraries."""
    data  = request.get_json(silent=True) or {}
    url   = data.get("url", "").rstrip("/")
    token = data.get("token", "")
    if not url or not token:
        return jsonify({"ok": False, "error": "URL and token are required"}), 400
    try:
        plex = PlexClient(url, token)
        reachable = plex.check_reachable()
        if not reachable["pass"]:
            return jsonify({"ok": False, "error": reachable["detail"]})
        sections = plex.get_sections()
        return jsonify({"ok": True, "libraries": sections, "detail": reachable["detail"]})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/wizard/browse", methods=["POST"])
@require_auth
def api_browse():
    """Browse filesystem directories for path selection."""
    data = request.get_json(silent=True) or {}
    path = data.get("path", "/")
    try:
        if not os.path.exists(path):
            return jsonify({"ok": False, "error": f"Path does not exist: {path}"}), 400
        entries = []
        for entry in sorted(os.scandir(path), key=lambda e: e.name):
            if entry.is_dir(follow_symlinks=False):
                entries.append({
                    "name":    entry.name,
                    "path":    entry.path,
                    "is_link": entry.is_symlink(),
                })
        parent = str(os.path.dirname(path)) if path != "/" else None
        return jsonify({"ok": True, "path": path, "parent": parent, "entries": entries})
    except PermissionError:
        return jsonify({"ok": False, "error": f"Permission denied: {path}"}), 403
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/wizard/save", methods=["POST"])
@require_auth
def api_wizard_save():
    """
    Receive wizard form data and write config.yml.
    Expects JSON matching the config structure.
    If store_tokens=True, writes tokens directly to config (less secure but simpler).
    If store_tokens=False, leaves tokens blank and returns the env var names needed.
    """
    data         = request.get_json(silent=True) or {}
    store_tokens = bool(data.get("store_tokens", False))

    # Build config dict from wizard data
    cfg = {
        "discord_webhook": data.get("discord_webhook", ""),
        "notify": {
            "on_success": data.get("notify_success", False),
            "on_failure": data.get("notify_failure", True),
            "on_skip":    data.get("notify_skip",    True),
        },
        "plex_instances": []
    }

    # Write auth block if provided
    wiz_user = data.get("auth_username", "").strip()
    wiz_pass = data.get("auth_password", "").strip()
    if wiz_user and wiz_pass:
        cfg["auth"] = {
            "username":      wiz_user,
            "password_hash": hash_password(wiz_pass),
        }

    env_vars_needed = []  # list of {name, description} for the summary screen

    for inst in data.get("instances", []):
        inst_name = inst.get("name", "")
        token     = inst.get("token", "")
        safe_name = inst_name.upper().replace(" ", "_").replace("-", "_")
        env_var   = f"PLEX_TOKEN_{safe_name}"

        instance_cfg = {
            "name":      inst_name,
            "url":       inst.get("url", ""),
            "token":     token if store_tokens else "",
            "libraries": []
        }

        if not store_tokens:
            env_vars_needed.append({
                "name":        env_var,
                "description": f"Plex token for '{inst_name}'",
                "value":       token,  # send back so UI can show it pre-filled
            })

        for lib in inst.get("libraries", []):
            lib_cfg = {
                "name": lib.get("name", ""),
                "type": lib.get("type", "physical"),
                "cron": lib.get("cron", "0 * * * *"),
                "paths": []
            }
            for p in lib.get("paths", []):
                path_cfg = {
                    "path":          p.get("path", ""),
                    "type":          p.get("type", "physical"),
                    "min_threshold": int(p.get("min_threshold", 90)),
                }
                pcs = p.get("provider_checks", [])
                if pcs:
                    path_cfg["provider_checks"] = [
                        {"type": pc.get("type", ""), "api_key": ""}
                        for pc in pcs
                    ]
                    # Add env var hints for provider API keys
                    for pc in pcs:
                        ptype    = pc.get("type", "")
                        env_map  = {
                            "realdebrid": "RD_API_KEY",
                            "alldebrid":  "AD_API_KEY",
                            "torbox":     "TB_API_KEY",
                            "debridlink": "DL_API_KEY",
                        }
                        env_name = env_map.get(ptype)
                        if env_name and not any(e["name"] == env_name for e in env_vars_needed):
                            env_vars_needed.append({
                                "name":        env_name,
                                "description": f"{ptype.capitalize()} API key (optional — for provider health checks)",
                                "value":       "",
                            })
                lib_cfg["paths"].append(path_cfg)
            instance_cfg["libraries"].append(lib_cfg)
        cfg["plex_instances"].append(instance_cfg)

    try:
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        with open(CONFIG_PATH, "w") as f:
            yaml.dump(cfg, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
        return jsonify({
            "ok":              True,
            "store_tokens":    store_tokens,
            "env_vars_needed": env_vars_needed,
            "message":         "Config saved. Restart the container to apply.",
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/config/load")
@require_auth
def api_config_load():
    """Return current config.yml contents for the settings editor."""
    try:
        with open(CONFIG_PATH, "r") as f:
            import yaml as _yaml
            raw = _yaml.safe_load(f) or {}
        return jsonify({"ok": True, "config": raw})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/debug/trash/<instance_name>/<section_id>")
@require_auth
def api_debug_trash(instance_name: str, section_id: str):
    """Debug endpoint — shows raw trash item counts per type level."""
    import requests as _requests
    inst = next((i for i in config.instances if i.name == instance_name), None)
    if not inst:
        return jsonify({"error": "instance not found"}), 404
    plex   = plex_clients[inst.name]
    result = {}
    for type_id in [2, 3, 4]:
        try:
            # Test with token as query param (curl-style)
            r = _requests.get(
                f"{plex.url}/library/sections/{section_id}/all",
                params={"checkFiles": 1, "type": type_id, "X-Plex-Token": plex.token},
                headers={"Accept": "application/json"},
                timeout=120,
            )
            items   = r.json().get("MediaContainer", {}).get("Metadata", [])
            deleted = [i for i in items if i.get("deletedAt")]
            result[f"type_{type_id}"] = {
                "total_returned": len(items),
                "deleted_count":  len(deleted),
                "titles":         [i.get("title") for i in deleted[:10]],
            }
        except Exception as e:
            result[f"type_{type_id}"] = {"error": str(e)}
    return jsonify(result)
    return jsonify(result)


@app.route("/api/providers/status")
@require_auth
def api_providers_status():
    """Return account status for all configured providers."""
    from src.providers import get_account_status, _ENV_KEYS, get_api_key
    result = {}
    for provider in _ENV_KEYS:
        key = get_api_key(provider, config=config)
        if key:
            result[provider] = get_account_status(provider, key)
        else:
            result[provider] = {"ok": False, "error": "no_key"}
    return jsonify(result)


@app.route("/api/providers/save", methods=["POST"])
@require_auth
def api_providers_save():
    """Save provider API keys to config.yml providers block."""
    global config
    data = request.get_json(silent=True) or {}
    try:
        with open(CONFIG_PATH, "r") as f:
            raw = yaml.safe_load(f) or {}
        providers = raw.get("providers", {})
        for provider, key in data.items():
            key = key.strip()
            if key:
                providers[provider] = {"api_key": key}
            else:
                providers.pop(provider, None)
        if providers:
            raw["providers"] = providers
        else:
            raw.pop("providers", None)
        with open(CONFIG_PATH, "w") as f:
            yaml.dump(raw, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
        config = load_config(CONFIG_PATH)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/auth/save", methods=["POST"])
@require_auth
def api_auth_save():
    """Save or clear username/password in config.yml."""
    global config
    data     = request.get_json(silent=True) or {}
    username = data.get("username", "").strip()
    password = data.get("password", "").strip()
    clear    = data.get("clear", False)

    try:
        with open(CONFIG_PATH, "r") as f:
            raw = yaml.safe_load(f) or {}

        if clear or (not username and not password):
            raw.pop("auth", None)
        else:
            if not username:
                return jsonify({"ok": False, "error": "Username required"}), 400
            if not password:
                return jsonify({"ok": False, "error": "Password required"}), 400
            raw["auth"] = {
                "username":      username,
                "password_hash": hash_password(password),
            }

        with open(CONFIG_PATH, "w") as f:
            yaml.dump(raw, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

        # Hot-reload config so auth takes effect without restart
        config = load_config(CONFIG_PATH)

        action = "cleared" if (clear or not username) else f"set for '{username}'"
        return jsonify({"ok": True, "message": f"Auth {action} — takes effect immediately."})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8222, debug=False)