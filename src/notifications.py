import requests
from typing import List, Dict


def _post(webhook_url: str, payload: dict):
    if not webhook_url:
        return
    # Validate it's actually a Discord webhook URL to prevent SSRF
    if not webhook_url.startswith("https://discord.com/api/webhooks/") and \
       not webhook_url.startswith("https://discordapp.com/api/webhooks/"):
        return
    try:
        requests.post(webhook_url, json=payload, timeout=10)
    except Exception:
        pass


def _check_fields(checks: Dict) -> list:
    return [
        {
            "name":   name,
            "value":  ("✅ " if c["pass"] else "❌ ") + c["detail"],
            "inline": False,
        }
        for name, c in checks.items()
    ]


def notify_emptied(webhook_url: str, instance_name: str, library_name: str,
                   removed_items: List[Dict], checks: Dict, breakdown: str = ""):
    """Fired when trash was actually emptied (items removed)."""
    if not webhook_url:
        return
    count    = len(removed_items)
    titles   = "\n".join(
        f"• {i['title']}" + (f" ({i['year']})" if i.get("year") else "")
        for i in removed_items[:15]
        if i.get("type") == "episode" or count <= 15
    )
    overflow    = f"\n_…and {count - 15} more_" if count > 15 else ""
    description = f"Emptied **{breakdown or f'{count} item(s)'}** from trash."
    if titles:
        description += f"\n\n{titles}{overflow}"
    _post(webhook_url, {"embeds": [{
        "title":       f"✅ emptyarr — {instance_name} / {library_name}",
        "description": description,
        "color":       0x3ecf8e,
        "fields":      _check_fields(checks),
    }]})


def notify_clean(webhook_url: str, instance_name: str, library_name: str,
                 checks: Dict):
    """Fired when run succeeded but trash was already empty."""
    if not webhook_url:
        return
    _post(webhook_url, {"embeds": [{
        "title":       f"✅ emptyarr — {instance_name} / {library_name}",
        "description": "Trash was already empty — nothing to remove.",
        "color":       0x3ecf8e,
        "fields":      _check_fields(checks),
    }]})


def notify_health_fail(webhook_url: str, instance_name: str, library_name: str,
                       failed_checks: Dict, all_checks: Dict):
    """Fired when health checks failed — trash empty was skipped."""
    if not webhook_url:
        return
    failed_list = "\n".join(
        f"• **{n}**: {c['detail']}" for n, c in failed_checks.items()
    )
    _post(webhook_url, {"embeds": [{
        "title":       f"⚠️ emptyarr — {instance_name} / {library_name}",
        "description": f"Health checks failed — trash empty skipped.\n\n**Failed:**\n{failed_list}",
        "color":       0xf06565,
        "fields":      _check_fields(all_checks),
    }]})


def notify_error(webhook_url: str, instance_name: str, library_name: str,
                 error: str, checks: Dict):
    """Fired when emptyTrash API call failed."""
    if not webhook_url:
        return
    _post(webhook_url, {"embeds": [{
        "title":       f"🔴 emptyarr — {instance_name} / {library_name} error",
        "description": f"emptyTrash failed:\n```{error}```",
        "color":       0xe74c3c,
        "fields":      _check_fields(checks),
    }]})


def notify_skip(webhook_url: str, instance_name: str,
                library_name: str, reason: str):
    """Fired when run was skipped (scheduling paused, config error, etc)."""
    if not webhook_url:
        return
    _post(webhook_url, {"embeds": [{
        "title":       f"⏭️ emptyarr — {instance_name} / {library_name} skipped",
        "description": f"**Reason:** {reason}",
        "color":       0xe8a045,
    }]})


# ── Legacy aliases for any existing callers ───────────────────────────────────
def notify_success(webhook_url, instance_name, library_name, removed_items, checks):
    if removed_items:
        notify_emptied(webhook_url, instance_name, library_name, removed_items, checks)
    else:
        notify_clean(webhook_url, instance_name, library_name, checks)

def notify_failure(webhook_url, instance_name, library_name, failed_checks, all_checks):
    notify_health_fail(webhook_url, instance_name, library_name, failed_checks, all_checks)