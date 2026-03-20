# emptyarr

**Plex trash watchdog** — validates mount health before emptying library trash.

emptyarr runs on a cron schedule, checks that your media paths are healthy, then safely empties Plex library trash. It supports multiple Plex instances, physical and debrid libraries, per-library schedules, Discord notifications, and a web UI.

![emptyarr dashboard](https://raw.githubusercontent.com/jjbobzin/emptyarr/main/static/icon-192.png)

---

## Features

- **Health checks before every empty** — mountpoint validation, symlink resolution, file count ratio
- **Multiple Plex instances** — manage Streamstead, Streamstead-Unlimited, or any number of servers
- **Library types** — `physical`, `debrid`, `usenet`, `mixed` (combined threshold check)
- **Per-library cron schedules** — each library runs on its own schedule
- **Dry run mode** — see what would be removed without taking action
- **Discord notifications** — on success, failure, or skip
- **Web UI** — dashboard, run history with expandable check details, settings editor
- **Optional auth** — set username/password via the Settings UI
- **Light/dark theme** — toggle in the nav bar

---

## Quick Start (Unraid)

### 1. Build the image

```bash
cd /mnt/cache/appdata/emptyarr
git clone https://github.com/jjbobzin/emptyarr.git .
docker build -t emptyarr:latest .
```

### 2. Add the container in Unraid Docker UI

| Field | Value |
|---|---|
| Name | `emptyarr` |
| Repository | `emptyarr:latest` |
| Network | `arr_net` (or your arr network) |
| Port | `8222` → `8222` |

**Path mappings:**

| Host | Container | Mode |
|---|---|---|
| `/mnt/cache/appdata/emptyarr/data` | `/app/data` | Read/Write |
| `/mnt/symlink_media` | `/symlink_media` | Read Only |
| `/mnt/user/media` | `/mnt/user/media` | Read Only |

> **Note on symlink paths:** The container path for symlink media must match what your symlinks actually point to. Check with `ls -la /mnt/symlink_media/symlinks/radarr/ | head -3` — if the targets start with `/symlink_media/` (no `/mnt`), use `/symlink_media` as the container path.

**Environment variables:**

| Variable | Description |
|---|---|
| `PUID` | `99` (Unraid nobody) |
| `PGID` | `100` (Unraid users) |
| `TZ` | e.g. `America/Denver` |
| `PLEX_TOKEN_<NAME>` | Plex token per instance (optional — can set via UI instead) |

Tokens are named by instance: `PLEX_TOKEN_STREAMSTEAD`, `PLEX_TOKEN_STREAMSTEAD_UNLIMITED`, etc. (uppercase, spaces/hyphens replaced with underscores).

### 3. Open the UI and run the setup wizard

Navigate to `http://YOUR_UNRAID_IP:8222` and click **Open Setup Wizard**.

---

## Configuration

Config is stored at `/app/data/config.yml` (mapped to your host's data directory). You can edit it via the **Settings** page in the UI, or directly in the file.

### Example config.yml

```yaml
discord_webhook: https://discord.com/api/webhooks/...
notify:
  on_success: true
  on_failure: true
  on_skip: true

plex_instances:
  - name: Streamstead
    url: http://10.10.10.5:32400
    token: ''   # use PLEX_TOKEN_STREAMSTEAD env var
    libraries:
      - name: Movies
        type: physical
        cron: "0 * * * *"
        paths:
          - path: /mnt/user/media/usenet/movies
            type: physical
            min_threshold: 90

      - name: TV Shows
        type: physical
        cron: "0 * * * *"
        paths:
          - path: /mnt/user/media/usenet/tv
            type: physical
            min_threshold: 90

  - name: Streamstead-Unlimited
    url: http://10.10.10.5:32410
    token: ''
    libraries:
      - name: Movies
        type: mixed
        cron: "0 * * * *"
        paths:
          - path: /mnt/user/media/usenet/movies
            type: physical
            min_threshold: 90
          - path: /symlink_media/symlinks/radarr
            type: debrid
            min_threshold: 90

      - name: TV Shows
        type: debrid
        cron: "0 * * * *"
        paths:
          - path: /symlink_media/symlinks/sonarr
            type: debrid
            min_threshold: 90
```

### Library types

| Type | Description | Checks run |
|---|---|---|
| `physical` | Standard files on disk | Mount, Files ratio |
| `debrid` | Symlinked debrid content | Mount, Symlinks, Files ratio |
| `usenet` | Usenet downloads | Mount, Symlinks, Files ratio |
| `mixed` | Mix of physical + debrid | Mount + Symlinks per path, combined Files ratio |

### Thresholds

`min_threshold` is the minimum percentage of your Plex library count that must exist on disk before emptyarr will empty trash. Default is `90` (90%).

For `mixed` libraries, emptyarr combines the file count across **all paths** and compares the total to the Plex count — so each individual path doesn't need to hold the full library.

---

## Health Checks

Before emptying trash, emptyarr runs these checks for each configured path:

1. **Mount** — walks up the directory tree to find the nearest mount point. Fails if the path doesn't exist.
2. **Symlinks** — for debrid/usenet paths, samples symlinks and verifies they resolve to real files. Catches dead debrid mounts.
3. **Files (ratio)** — counts files on disk and compares to Plex library count. Fails if ratio drops below `min_threshold`.
4. **Files (combined)** — for `mixed` libraries, checks the sum of all paths vs Plex count.

If any check fails, trash is **not** emptied and a notification is sent (if configured).

---

## Authentication

Go to **Settings → Security** to set a username and password. Auth takes effect immediately — no container restart needed. Passwords are stored as SHA-256 hashes in `config.yml`.

You can also set `EMPTYARR_USERNAME` and `EMPTYARR_PASSWORD` env vars instead (env vars take priority).

---

## Updating

```bash
cd /mnt/cache/appdata/emptyarr
git pull
docker build -t emptyarr:latest .
# Restart container in Unraid UI
```

---

## Privacy

emptyarr makes network requests **only** to services you configure: your Plex server, debrid provider APIs (if you set an API key), and your Discord webhook. No telemetry. No analytics. No phone-home. See [PRIVACY.md](PRIVACY.md).

---

## License

MIT