# emptyarr

Safely empties Plex library trash by validating mount health before acting.
Supports multiple Plex instances, mixed physical/debrid libraries,
per-library cron schedules, dry runs, and Discord notifications.

## How it works

Each library run:
1. Checks Plex is reachable
2. Per path: mountpoint → symlink resolution (debrid/usenet) → file threshold
3. Optionally pings debrid provider APIs (Real-Debrid, AllDebrid, Torbox, Debrid-Link)
4. If all checks pass: snapshots trash → calls emptyTrash → records removed titles
5. If any check fails: skips, optionally sends Discord alert

## Library types

| Type | Mountpoint | Symlinks | File threshold | Provider API |
|------|-----------|----------|---------------|-------------|
| `physical` | ✅ | ❌ | ✅ | ❌ |
| `debrid` | ✅ | ✅ | ✅ | optional |
| `usenet` | ✅ | ✅ | ✅ | optional |
| `mixed` | per path | per path | per path | per path |

---

## Configuration — two tiers

### Tier 1: config.yml (structure)

Controls everything about your Plex instances and libraries:
- Plex instance names and URLs
- Library names, paths, types
- min_files, min_threshold per path
- Cron schedules per library
- Notify preferences

Copy the example and fill in your values:
```bash
cp data/config.yml.example data/config.yml
```

### Tier 2: Environment variables (secrets)

Tokens and API keys set in the Unraid Docker UI or `.env` file.
These **override** any matching values in config.yml.

| Variable | Description |
|----------|-------------|
| `PLEX_TOKEN_{NAME}` | Token per instance — e.g. `PLEX_TOKEN_MY_PLEX` |
| `PLEX_URL_{NAME}` | URL per instance (optional override) |
| `RD_API_KEY` | Real-Debrid API key |
| `AD_API_KEY` | AllDebrid API key |
| `TB_API_KEY` | Torbox API key |
| `DL_API_KEY` | Debrid-Link API key |
| `DISCORD_WEBHOOK` | Discord webhook URL |
| `TZ` | Timezone e.g. `America/Denver` |
| `LOG_LEVEL` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |

The instance name in the variable matches the `name` field in config.yml,
uppercased with spaces and hyphens replaced by underscores:
- `"My Plex"` → `PLEX_TOKEN_MY_PLEX`
- `"Plex-4K"` → `PLEX_TOKEN_PLEX_4K`
- `"HomeServer"` → `PLEX_TOKEN_HOMESERVER`

---

## Setup on Unraid

### Step 1 — Create the config file

The config file must exist **before** starting the container. If it doesn't
exist when Docker mounts it, Docker creates a directory instead of a file
and the container won't start correctly.

```bash
# SSH into Unraid or use the terminal
mkdir -p /mnt/cache/appdata/emptyarr
cp /path/to/emptyarr/data/config.yml.example /mnt/cache/appdata/emptyarr/config.yml
nano /mnt/cache/appdata/emptyarr/config.yml
```

Fill in your Plex instance URLs, library names, and paths.
Leave tokens blank in config.yml — set them as env vars in the Docker UI instead.

### Step 2 — Get your Plex section IDs (optional)

emptyarr auto-discovers section IDs by library name, but you can hardcode
them in config.yml to skip the lookup on each run:

```
# First Plex instance
http://192.168.1.100:32400/library/sections?X-Plex-Token=YOUR_TOKEN

# Second Plex instance
http://192.168.1.100:32410/library/sections?X-Plex-Token=YOUR_TOKEN
```

Look for `key="1"` on each `<Directory>` element.

### Step 3 — Get your min_files values

Run these on Unraid to count your actual file counts:
```bash
find /mnt/symlink_media/symlinks/movie -type f | wc -l
find /mnt/symlink_media/symlinks/tv -type f | wc -l
find /mnt/user/media/usenet/movies -type f | wc -l
find /mnt/user/media/usenet/tv -type f | wc -l
```
Set `min_files` to ~10% of these numbers as a safe floor.

### Step 4 — Add the container

**Option A: Community Applications (easiest)**

Search for `emptyarr` in the CA app store. Fill in the fields — secrets
go in the masked variable fields, the config.yml path mapping points to
where you created the file in Step 1.

**Option B: Manual via Docker UI**

Unraid → Docker → Add Container:
- Repository: `ghcr.io/jjbobzin/emptyarr:latest`
- Name: `emptyarr`
- Port: `8222` → `8222`

Add path mappings:

| Host path | Container path | Mode |
|-----------|---------------|------|
| `/mnt/cache/appdata/emptyarr/config.yml` | `/app/data/config.yml` | ro |
| `/mnt/symlink_media/symlinks/movie` | `/media/symlinks/movie` | ro |
| `/mnt/symlink_media/symlinks/tv` | `/media/symlinks/tv` | ro |
| `/mnt/user/media/usenet/movies` | `/media/physical/movies` | ro |
| `/mnt/user/media/usenet/tv` | `/media/physical/tv` | ro |
| `/mnt/symlink_media/decypharr/mount` | `/mnt/symlink_media/decypharr/mount` | ro |

Add environment variables (use the masked/password type for tokens):
- `PLEX_TOKEN_MY_PLEX` (rename to match your instance name)
- `PLEX_TOKEN_MY_OTHER_PLEX` (if you have a second instance)
- `RD_API_KEY`
- `DISCORD_WEBHOOK`
- `TZ` = `America/New_York`

**Option C: docker compose (local/dev)**

```bash
cp .env.example .env
# Fill in .env with your tokens
docker compose up -d --build
```

### Step 5 — First run

Open `http://YOUR_UNRAID_IP:8222`

1. Click **check** — pings Plex on all instances, no library action
2. Click **◎ dry run** — runs all checks, shows what would be removed, no action
3. Expand history rows to verify the right items show up in the trash snapshot
4. Click **▶ run all** for the first real run

---

## Volume mount — symlink resolution

The most important thing to get right: **symlink targets must resolve inside the container.**

Your symlinks in `/media/symlinks/movie` point to files somewhere under the
Decypharr mount. That mount must be visible inside the container **at the
exact path your symlinks reference**.

Example: if a symlink at `/media/symlinks/movie/Film (2020)/film.mkv`
points to `/mnt/symlink_media/decypharr/mount/__all__/film.mkv`, then
you need:
```yaml
- /mnt/symlink_media/decypharr/mount:/mnt/symlink_media/decypharr/mount:ro
```

If the paths don't match, every symlink will appear broken even when the
mount is healthy, and emptyarr will never empty trash.

---

## Updating

```bash
# On Unraid — pull latest and rebuild
cd /mnt/cache/appdata/emptyarr
git pull
docker compose up -d --build
```

---

## API

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Web UI |
| `/api/status` | GET | Full status, next runs, scheduling state |
| `/api/history` | GET | Run history (last 100) |
| `/api/checks` | GET | Plex reachability checks only, no action |
| `/api/scheduling` | POST | `{"enabled": true/false}` pause/resume cron |
| `/api/run/all` | POST | Trigger all libraries |
| `/api/dryrun/all` | POST | Dry run all libraries |
| `/api/run/{instance}/{library}` | POST | Trigger one library |
| `/api/dryrun/{instance}/{library}` | POST | Dry run one library |

---

## Cron reference

| Expression | Meaning |
|------------|---------|
| `*/30 * * * *` | Every 30 minutes |
| `0 * * * *` | Every hour |
| `0 */2 * * *` | Every 2 hours |
| `0 2 * * *` | Daily at 2am |

---

## min_files / min_threshold guidance

**min_files** — absolute floor. If your mount dies, file count drops from
thousands to ~0. Set this to ~10% of your library size so a catastrophic
mount failure is caught immediately.

**min_threshold** — ratio of disk files to Plex library count. `90` means
there must be at least 90% as many files on disk as items in Plex. Catches
partial mount failures while tolerating normal fluctuation from upgrades.
