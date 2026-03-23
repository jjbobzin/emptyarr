# emptyarr

Plex doesn't automatically clean up its library trash when you're using symlinked debrid or usenet media. When a file gets replaced or removed, Plex marks it unavailable — but unless you have "empty trash automatically after every scan" turned on (which you probably don't, because that's risky), those entries just pile up.

emptyarr runs on a schedule, checks that your mounts are actually healthy, and then calls Plex's emptyTrash API. If anything looks wrong — mount missing, symlinks broken, file count dropped — it skips the empty and optionally pings you on Discord.

---

## How it works

Before emptying trash on any library, emptyarr runs:

1. **Mount check** — walks up the path tree to find the nearest mount point and verifies it's accessible
2. **Symlink check** — for debrid/usenet paths, samples a random set of symlinks and verifies they resolve to real files
3. **File threshold** — compares the count of files on disk to your Plex library count. If the ratio drops below your configured threshold (default 90%), something's wrong and it bails
4. **Combined check** — for mixed libraries (physical + debrid), sums all paths and checks the combined ratio

All checks pass → trash gets emptied. Any check fails → skip, log it, notify if configured.

---

## Setup (Unraid)

### Build

```bash
mkdir -p /mnt/cache/appdata/emptyarr/data
cd /mnt/cache/appdata/emptyarr
git clone https://github.com/jjbobz/emptyarr.git .
docker build -t emptyarr:latest .
```

### Container settings

**Network:** your arr network (e.g. `arr_net`)  
**Port:** `8222`

**Path mappings:**

| Host | Container | Mode |
|---|---|---|
| `/mnt/cache/appdata/emptyarr/data` | `/app/data` | Read/Write |
| `/mnt/symlink_media` | `/symlink_media` | Read Only |
| `/mnt/user/media` | `/mnt/user/media` | Read Only |

> The container path for symlink media needs to match what your symlinks actually point to. Check with `ls -la /mnt/symlink_media/symlinks/radarr/ | head -3` and look at the symlink targets. If they start with `/symlink_media/` (no `/mnt`), use that as the container path.

**Environment variables:**

| Variable | Description |
|---|---|
| `PUID` | `99` (Unraid nobody) |
| `PGID` | `100` (Unraid users) |
| `TZ` | Your timezone, e.g. `America/Denver` |
| `PLEX_TOKEN_<NAME>` | Optional — set per instance, or paste in the UI instead |

Token env var names match your instance name uppercased with spaces/hyphens as underscores: `PLEX_TOKEN_STREAMSTEAD`, `PLEX_TOKEN_STREAMSTEAD_UNLIMITED`, etc.

### First run

Open `http://YOUR_IP:8222` and run through the setup wizard. Takes a few minutes.

---

## Configuration

Config lives at `/app/data/config.yml` (your host's data directory). The Settings page in the UI can edit everything — you shouldn't need to touch the file directly.

### Library types

- **physical** — standard files on disk
- **debrid** — symlinked content (Real-Debrid, AllDebrid, etc.)
- **usenet** — usenet downloads with symlinks
- **mixed** — combination of physical and debrid in the same Plex library

For mixed libraries the file threshold check combines all paths before comparing to your Plex count, so individual paths don't need to hold the full library.

### Threshold

`min_threshold` is the percentage of your Plex library count that must exist on disk. Default is 90. If you have 1000 movies in Plex and only 850 files on disk, that's 85% — below 90%, so the empty gets skipped.

### Cron schedules

Per-library. Standard cron syntax. `0 * * * *` runs every hour on the hour, `*/30 * * * *` every 30 minutes.

### Example config

```yaml
discord_webhook: https://discord.com/api/webhooks/...
notify:
  on_emptied: true
  on_health_fail: true
  on_error: true
  on_clean: false
  on_skip: false

plex_instances:
  - name: Streamstead
    url: http://192.168.1.100:32400
    token: ''
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
    url: http://192.168.1.100:32410
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

---

## Auth

Settings → Security. Enter username and password, save. Takes effect immediately, no restart needed. Stored as a SHA-256 hash in config.yml — never plaintext.

You can also set `EMPTYARR_USERNAME` and `EMPTYARR_PASSWORD` env vars instead (these take priority).

---

## Notifications

Five separate Discord notification events you can toggle independently:

- **Trash emptied** — something was actually removed
- **Health check failed** — checks didn't pass, empty was skipped
- **Error** — the emptyTrash API call failed
- **Already clean** — ran fine, nothing to remove (off by default — gets noisy)
- **Skipped** — scheduling paused, config error, section not found (off by default)

---

## Updating

```bash
cd /mnt/cache/appdata/emptyarr
git pull
docker build -t emptyarr:latest .
# restart in Unraid UI
```

---

## Privacy

emptyarr only talks to: your Plex server, debrid provider APIs if you configure an API key, and your Discord webhook. That's it. No telemetry, no analytics, no external calls. See [PRIVACY.md](PRIVACY.md).

---

## License

MIT