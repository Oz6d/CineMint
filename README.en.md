# CineFlow

> A Chinese-first, self-hosted media library organizer: scan files → parse filenames → match TMDb → fix mistakes → move / rename with templates → generate NFO and artwork.

**English** | [中文](README.md)

![License](https://img.shields.io/badge/license-MIT-green)
![Python](https://img.shields.io/badge/python-3.12-blue)
![React](https://img.shields.io/badge/react-18-61dafb)
![Version](https://img.shields.io/badge/version-0.2.2-2dd4bf)
![Docker](https://img.shields.io/badge/docker-ready-2496ed)

---

## What it is

CineFlow works only with media files you already have:

- Local folders, WebDAV (OpenList / Alist, Nextcloud, Synology, etc.) and SMB / Samba;
- Detects movies / TV shows, seasons and episodes, resolution, HDR, audio, release group;
- Matches TMDb metadata, with manual search (paged results), batch correction and episode fixing; manual matches and season / episode edits are kept across rescans, and rematch returns to automatic matching;
- Poster wall and the file view verify the real source directory; entries deleted outside CineFlow (or whose folder no longer exists) are marked missing and removed from the work count, while transient network errors are not treated as deletion;
- Moves files into a target library with subtitles, audio tracks, NFO and artwork;
- Generates NFO and images compatible with Emby / Jellyfin / Plex / Kodi / Infuse;
- Includes **rename only** mode: rename files / folders in place without moving or scraping.

**It does not provide media sources, downloads, playback or DRM circumvention.**

---

## Features

### Files and scanning
- Local / WebDAV / SMB sources with connection tests and folder creation.
- Recursive scanning that skips hidden folders, recycle-bin folders (such as `Filmix.Trash`) and symlink folders.
- Two views under **All Files**:
  - **Videos only**: browse pending media in its real folder tree;
  - **All files**: browse videos, subtitles, audio, NFO, artwork and other files; sort by headers, move / delete in bulk, preview and delete by plain text or regex.
- Item and manual-attachment records stay in sync after moves / deletes.

### Recognition and matching
- Chinese / English titles, years, `S01E02`, `第01集`, `1x02`, multi-episode files, resolution, HDR, audio and release groups.
- TMDb matching, candidate scoring, manual search, batch fixes, whole-show rematch and episode offsets.
- Optional AI filename fallback: only the filename is sent, and the result is cross-checked with TMDb.
- AI token / cost tracking: cache-hit / cache-miss / output pricing, peak / off-peak multipliers, Chinese public holidays, provider-reported cost, and OpenRouter / LiteLLM price sync.

### Organizing and scraping
- Four template layers: movie / show / season folder / file name, with category folders, season folder styles, tag order and separators.
- Optional **clean up empty folders**: after videos and attachments are moved, empty source folders are removed deepest-first, including subtitle folders such as `Subs`; the source root, target folder, non-empty folders and symlink folders are never removed.
- Movie NFO uses the final video name; episode NFO matches the episode video; `tvshow.nfo` / `season.nfo` keep fixed names.
- Subtitles and audio tracks follow from the same folder and common `Subs` / `Sub` / `Subtitles` subfolders; manual linking can target the current video or another episode in the same folder.
- Name collisions get `(1)` instead of overwriting; organizing is retry-safe and idempotent.

### Subscriptions and tracking
- **Quark subscriptions**: add a Quark share link; scheduled checks detect updates and transfer files to a staging folder, then reuse the library scan and organize pipeline. Supports keyword / regex filtering, V2 episode replacement (same TMDb + season + episode auto-replaces the old version), and scheduling (fixed interval / standard cron).
- **Share-folder URLs**: paste a Quark share URL containing `#/list/share/<folder-id>` and the folder is detected automatically; you can also open the share browser from the button next to the link, and selecting a folder updates the URL. Only files inside that folder (including subfolders) are transferred.
- **Subscription task parameters**: export or copy a complete task parameter JSON (including the passcode) from any subscription, or create a new subscription from it. Pasting task parameters also works; after recognition you complete the library, staging folder and schedule in the form.
- **RSS subscriptions**: add RSS feed URLs; new items are downloaded via 115 cloud-add and organized through the same pipeline. Supports Mikan RSS and any standard RSS / Atom feed; 115 supports web cookies and Open Platform `refresh_token` with auto-renewal.
- **Mikan calendar and follow list**: Mikan Project weekly broadcast calendar (default) + Bangumi calendar (optional) + Mikan RSS tracking progress; can link directly to RSS / 115 subscriptions for automatic downloads. Supports following / paused / completed status management.
- **Notification channels**: 23 push channels (Webhook, Bark, Telegram, WeCom, Feishu, DingTalk, etc.). Each subscription run is merged into one push: the title is just the task name; the body starts with transferred / failed / organized / unmatched / review counts, then categorized details, and ends with the time. Event filters and delivery logs are available.
- See **Subscriptions and tracking** below for detailed steps, task parameters and the notification format.

### Other
- Task center with queue, progress, logs and cancel.
- Editable custom tag dictionary for editions, release notes and groups.
- Single admin password, scrypt hashing, signed session cookies and Fernet-encrypted source secrets.
- Responsive UI for desktop and mobile; sidebar includes Overview, Libraries, Subscriptions, Bangumi Calendar, Notifications, Tasks, Sources, and Settings.

---

## Quick start (Docker)

Prebuilt images: `ghcr.io/oz6d/cineflow:latest` and `docker.io/oz6d/cineflow:latest`, available for `linux/amd64` and `linux/arm64`. GHCR package: [GHCR](https://github.com/Oz6d/Cineflow/pkgs/container/cineflow).

Choose one of the two methods below; do not run both against the same data directory. Using the prebuilt image does not require cloning the whole repository.

### Option 1: docker compose (recommended)

Create a deployment directory:

```bash
mkdir -p cineflow
cd cineflow
```

Save these two files in it.

#### `docker-compose.yml`

```yaml
services:
  cineflow:
    image: ghcr.io/oz6d/cineflow:latest
    container_name: cineflow
    restart: unless-stopped
    ports:
      - "${CINEFLOW_HTTP_PORT:-8080}:8080"
    environment:
      TZ: ${TZ:-Asia/Shanghai}
      CINEFLOW_DATA_DIR: /data
      CINEFLOW_ADMIN_PASSWORD: ${CINEFLOW_ADMIN_PASSWORD:-change-me}
      CINEFLOW_SECRET_KEY: ${CINEFLOW_SECRET_KEY:-}
      CINEFLOW_COOKIE_SECURE: ${CINEFLOW_COOKIE_SECURE:-false}
      CINEFLOW_SESSION_DAYS: ${CINEFLOW_SESSION_DAYS:-14}
      CINEFLOW_DEMO: ${CINEFLOW_DEMO:-false}
      CINEFLOW_TMDB_API_BASE: ${CINEFLOW_TMDB_API_BASE:-https://api.themoviedb.org/3}
      CINEFLOW_TMDB_IMAGE_BASE: ${CINEFLOW_TMDB_IMAGE_BASE:-https://image.tmdb.org/t/p}
      PUID: ${PUID:-}
      PGID: ${PGID:-}
    volumes:
      - ./data:/data
      - ${MEDIA_PATH:-./media}:/media
    healthcheck:
      test:
        [
          "CMD",
          "python",
          "-c",
          "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/health', timeout=4)",
        ]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 20s
```

#### `.env`

```dotenv
# Replace this with a unique strong password; change-me is only an example
CINEFLOW_ADMIN_PASSWORD=change-me

# Host media folder, mounted at /media inside the container
MEDIA_PATH=/path/to/your/media

# Public port
CINEFLOW_HTTP_PORT=8080

# Time zone
TZ=Asia/Shanghai

# Optional: use an alternate TMDb API endpoint if api.themoviedb.org is unreachable
# CINEFLOW_TMDB_API_BASE=https://api.tmdb.org/3
# CINEFLOW_TMDB_IMAGE_BASE=https://image.tmdb.org/t/p
```

#### Start

You only need to change **two values** for a normal deployment:

- `CINEFLOW_ADMIN_PASSWORD`: set your own unique strong password;
- `MEDIA_PATH`: point to the host media folder.

Everything else can keep its default. `CINEFLOW_DATA_DIR` must remain `/data` because it matches the `./data:/data` volume; leave `CINEFLOW_SECRET_KEY` empty to generate it automatically; set `CINEFLOW_COOKIE_SECURE` to `true` only behind an HTTPS reverse proxy; fill `PUID` / `PGID` only if you hit file-permission issues on Linux.

> If the server cannot reach `api.themoviedb.org` (for example, `TMDb request failed: [Errno 101] Network is unreachable`), uncomment `CINEFLOW_TMDB_API_BASE=https://api.tmdb.org/3` in `.env` and run `docker compose up -d`. Images still use `image.tmdb.org` by default.

```bash
docker compose pull
docker compose up -d
```

Upgrade:

```bash
docker compose pull
docker compose up -d
```

Logs:

```bash
docker compose logs -f cineflow
```

To build from local source instead of using the prebuilt image:

```bash
git clone https://github.com/Oz6d/Cineflow.git
cd Cineflow
docker compose -f docker-compose.yml -f docker-compose.build.yml up -d --build
```

### Option 2: docker run (without compose)

Useful when you do not want to create a compose file and just want to run the image.

```bash
mkdir -p data
docker pull ghcr.io/oz6d/cineflow:latest

docker run -d \
  --name cineflow \
  --restart unless-stopped \
  -p 8080:8080 \
  -e TZ=Asia/Shanghai \
  -e CINEFLOW_DATA_DIR=/data \
  -e CINEFLOW_ADMIN_PASSWORD='change-me' \
  -v "$PWD/data:/data" \
  -v "/path/to/your/media:/media" \
  ghcr.io/oz6d/cineflow:latest
```

Notes:

- Replace `change-me` with your own strong password; do not use the example value;
- `-v "$PWD/data:/data"` persists the database and keys; **do not delete this folder**;
- `-v "/path/to/your/media:/media"` is the media folder; omit it if you only use WebDAV / SMB;
- Logs: `docker logs -f cineflow`;
- Upgrade: `docker pull ghcr.io/oz6d/cineflow:latest`, remove the old container, then run the same `docker run` command again (keep `./data`).

### Configure after startup

Open `http://localhost:8080` and log in with your admin password, then:

1. Add a local / WebDAV / SMB source under **Sources** and test the connection;
2. Add a TMDb API key or v4 Read Access Token under **Settings → TMDb**;
3. Create a library with a source, pending folder and target folder;
4. **Scan** → review under **Needs review** → **Organize preview** → **Organize**.

> Change the password in **Settings → Security** after the first login.
> Runtime data lives in `./data`; back it up regularly and keep `data/secret.key` private.
> If the GHCR package is not visible, set it to Public under **Packages → cineflow → Package settings** in the GitHub repository.

---

## Subscriptions and tracking

v0.2.0 adds a complete subscription workflow: new items from Quark share links or RSS feeds are transferred / downloaded into the library pending folder, then reuse the regular scan, TMDb match, organize, scrape and notification pipeline. Open **Subscriptions** in the sidebar; the broadcast calendar lives under **Calendar**.

### Prerequisites

- Create a library first; every subscription must choose the library it organizes into.
- Quark subscriptions need a Quark cookie under **Settings → Quark Drive**; make sure **Test connection** succeeds.
- RSS subscriptions download through 115 Cloud; configure a web cookie or an Open Platform `refresh_token` under **Settings → 115** (refresh tokens renew automatically).
- To receive pushes, add channels such as Bark, Telegram or Webhook under **Notification channels**; each subscription can select its own channels.

### Quark subscriptions

1. **Subscriptions → Quark subscription**, enter a name and choose the library.
2. Paste the Quark share link; add the passcode if required.
3. **Share folder** (optional):
   - Open the target folder in Quark, copy the URL containing `#/list/share/<folder-id>` and paste it into the share link. The folder is detected automatically; no second selection is needed.
   - You can also click the folder button next to the link to browse the share; selecting a folder updates the link.
   - Empty means the whole share; after selecting a folder only files inside it (including subfolders) are transferred.
4. **Transfer folder**: empty saves to the library root; use **Browse cloud drive** to pick a subfolder inside the library. Folders outside the library are rejected.
5. **Filters**: choose keyword or regex. Keywords are comma-separated and must all match; regex matches the filename; empty disables filtering. You can also limit items per run, set an interval or cron, a daily time window and a deadline.
6. **Auto organize / V2 replacement**:
   - With auto organize enabled, matched files are renamed, scraped and moved to the target folder. When disabled they remain in the pending folder for manual review.
   - V2 replacement requires auto organize: for the same TMDb + season + episode, a newer version replaces the old one; the old file goes to the recycle folder and no `(1)` duplicate is created.
7. **Notification channels**: no selection means all enabled channels; otherwise only the selected channels receive pushes.
8. A newly saved, enabled subscription queues one check immediately, then follows the schedule. Run records show per-item status and errors, and **Run now** is always available.

Terminology:

- **Transferred**: new files transferred from the share during this run.
- **Reused**: an identical file already existed in the staging folder; the transfer is skipped and the existing file is organized.
- **Duplicate**: the episode already has another version or was already organized, so it is skipped.
- **Failed**: transfer or organize failure; check the run record for the exact error and retry after fixing it.

### Subscription task parameters

The **Parameters** button on a subscription card exports CineFlow's own complete task parameter JSON (name, type, full config, schedule, deadline, auto organize / V2 replacement, notification channels and passcode). Copy it, or use **Create from these parameters** to create an identical subscription.

The **Paste task parameters** entry accepts CineFlow task parameters for quickly creating a similar task. After recognition you still complete the library, transfer folder and schedule in the form before saving. The parameters contain the passcode, so store them carefully.

### RSS subscriptions

**Subscriptions → RSS subscription**: enter the RSS / Atom URL, library, keyword or regex, items per run, staging folder and schedule. An empty staging folder also means the library root. Mikan RSS and any standard RSS / Atom feed are supported.

### Calendar and follow list

**Calendar** provides the Mikan Project weekly schedule (default) and an optional Bangumi schedule. Selecting a show loads the Bangumi entry, subgroup and Mikan RSS, with an RSS preview. Adding a follow also creates an RSS subscription that checks, downloads and organizes on schedule. Following / paused / completed states are supported; deleting a follow also deletes its RSS subscription (deletion is refused while it is running; downloaded / organized files are unaffected).

### Notifications

- 23 push channels: Webhook, Bark, Telegram, WeCom, Feishu, DingTalk, etc.
- **Each subscription run is merged into one push**, so three unmatched files do not produce three messages.
- The title is only the task name; the first body line gives transferred / failed / organized / unmatched / review counts, followed by categorized file names and guidance, and the time at the end.
- Scheduled checks with no new content send nothing; a failed run sends a separate failure notification.
- Event filters under **Notification channels** decide what arrives; no selection means everything. Delivery logs are available.

### Notes

- An empty transfer / staging folder means the library root; CineFlow does not create `_subscriptions/<name>` automatically.
- OpenList mount names are not hard-coded: detection removes the first segment of the library root path. If you rename the mount, update the library pending / target folders accordingly.
- OpenList + Quark WebDAV moves can occasionally return HTTP 500 (a transient OpenList directory-cache / driver issue). The file usually remains in the pending folder; click **Run now** to retry, and check OpenList logs if it keeps failing.
- Share-folder ID resolution checks at most 8 levels / about 20,000 entries; very deep or huge shares may fail, in which case select the folder with the button next to the share link.
- Manual matching remembers the TMDb show for a title, so later episodes with romanized names and no season number are recognized and organized automatically; with auto organize enabled the subscription organizes them directly.

---

## Configuration

### TMDb

Add a v3 API key or v4 Read Access Token under **Settings → TMDb**. Images are proxied and cached by the backend; clients do not connect directly to TMDb image hosts.

The default TMDb API endpoint is `https://api.themoviedb.org/3`. If the server cannot reach that host, use the alternate endpoint:

```dotenv
CINEFLOW_TMDB_API_BASE=https://api.tmdb.org/3
```

`api.tmdb.org` and `api.themoviedb.org` return the same TMDb API responses. If the image host is also unreachable, set `CINEFLOW_TMDB_IMAGE_BASE` separately.

### Optional AI filename fallback

Works with any OpenAI-compatible API (OpenAI, DeepSeek, Ollama, SiliconFlow, etc.):

- Only the filename is sent, not file contents;
- Results are cross-checked with TMDb;
- Configurable cache-hit / cache-miss / output prices, peak / off-peak windows and Chinese public holidays;
- Can prefer provider-reported `usage.cost`, or sync model pricing from OpenRouter / LiteLLM;
- Costs are estimates; the provider invoice is authoritative.

### Sources

- **WebDAV**: the URL usually needs to end with `/dav`; verify it in a browser for OpenList / Alist.
- **SMB**: provide host, share, username, password and optional subfolder.
- All file operations are restricted to the library pending root.

### Quark Drive / 115

- **Quark Drive**: paste a cookie under **Settings → Quark Drive** and click **Test connection**; it should return the nickname and folder list.
- **115**: choose a web cookie or an Open Platform `refresh_token` under **Settings → 115**. Open Platform tokens are renewed and rotated automatically, so you do not need to replace the cookie often.
- Notifications require a channel under **Notification channels** first; channel secrets / webhook URLs are encrypted with Fernet.

---

## Security and privacy

- Single admin password, hashed with scrypt.
- HMAC-signed session cookies; optional Secure cookies behind HTTPS.
- WebDAV / SMB passwords, TMDb keys and AI keys are Fernet-encrypted in SQLite.
- The data directory (database, keys, demo media) is excluded from Git by default.
- Media files are never uploaded; the AI fallback only sends filenames.

---

## Known limitations

- No media sources, downloads, playback or DRM circumvention.
- Deleting in **All files** has no recycle bin and cannot be undone; recursive rules skip hidden, recycle-bin and symlink folders.
- Large WebDAV moves can be slow; if the server only completes a copy, the source is kept and the move is reported as failed.
- Attachments are auto-discovered only in the same folder and common one-level subtitle subfolders (`Subs` / `Sub` / `Subtitles` / etc.).
- Poster-wall existence checks are cached for about 20 seconds; external renames / deletes may take that long to appear.
- AI cost tracking depends on provider usage / cost fields; historical calls cannot be reconstructed.
- Chinese public holiday sync depends on an external data source; on failure the previous dates are kept.
- Subscription task parameters include the passcode; share-folder ID resolution checks up to 8 levels / about 20,000 entries, so very deep or huge shares may require picking the folder manually.
- OpenList + Quark WebDAV moves can occasionally return HTTP 500 (a transient directory-cache / driver issue); the file usually stays in the pending folder, so click **Run now** to retry.

---

## Disclaimer

- This project only renames, moves and organizes metadata. It does not provide, download or play any media.
- Only organize media you have the legal right to access, and follow your local copyright laws.
- This product uses the TMDb API but is not endorsed or certified by TMDb.
- Inspired by Filmix, but independently implemented; it contains no private code or interfaces from that app.

## License

[MIT](LICENSE)
