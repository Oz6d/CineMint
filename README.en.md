# CineFlow

> A Chinese-first, self-hosted media library organizer: scan files → parse filenames → match TMDb → fix mistakes → move / rename with templates → generate NFO and artwork.

**English** | [中文](README.md)

![License](https://img.shields.io/badge/license-MIT-green)
![Python](https://img.shields.io/badge/python-3.12-blue)
![React](https://img.shields.io/badge/react-18-61dafb)
![Docker](https://img.shields.io/badge/docker-ready-2496ed)

---

## What it is

CineFlow works only with media files you already have:

- Local folders, WebDAV (OpenList / Alist, Nextcloud, Synology, etc.) and SMB / Samba;
- Detects movies / TV shows, seasons and episodes, resolution, HDR, audio, release group;
- Matches TMDb metadata, with manual search, batch correction and episode fixing; manual matches and season / episode edits are kept across rescans, and rematch returns to automatic matching;
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

### Other
- Task center with queue, progress, logs and cancel.
- Editable custom tag dictionary for editions, release notes and groups.
- Single admin password, scrypt hashing, signed session cookies and Fernet-encrypted source secrets.
- Responsive UI for desktop and mobile.

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

## Image publishing and mirrors

### Image addresses

- GHCR: `ghcr.io/oz6d/cineflow:latest` (`latest` / `main` / `0.1.2` / `0.1`)
- Docker Hub: `docker.io/oz6d/cineflow:latest` (`latest` / `main` / `0.1.2` / `0.1`)

Docker Hub is published at `oz6d/cineflow` and is publicly pullable. GitHub Actions pushes to both GHCR and Docker Hub for `main` and `v*` tags:

- `DOCKERHUB_USERNAME`: your Docker Hub username;
- `DOCKERHUB_TOKEN`: a Docker Hub access token with Read & Write permission; not your login password.

If the two secrets are not set, the workflow skips Docker Hub and publishes only to GHCR without failing. The Docker Hub repository is public.

> The old `tiawjielv/cineflow` namespace is no longer updated; use `oz6d/cineflow`.

### 1ms and other mirrors

Sites such as 1ms are **pull-through mirrors / reverse proxies**, not registries. You cannot push to them, and the project cannot "sync" to them directly. Publish to Docker Hub or GHCR first, then pull through the mirror:

```bash
# Docker Hub
docker pull docker.1ms.run/oz6d/cineflow:latest

# GHCR
docker pull ghcr.1ms.run/oz6d/cineflow:latest
```

The first pull through a mirror may need to fetch from upstream and can be slower; later pulls use its cache. Other Docker mirrors work the same way: pull-only. With compose, you can also change `image` to `docker.io/oz6d/cineflow:latest` or `docker.1ms.run/oz6d/cineflow:latest`.

---

## Local development

### Backend

```bash
cd backend
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

CINEFLOW_DATA_DIR=../data CINEFLOW_DEMO=true \
  uvicorn app.main:app --reload --port 8080
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

Vite runs at `http://localhost:5173` and proxies `/api` to `http://127.0.0.1:8080`.

### Tests

```bash
cd backend
.venv/bin/python ../tests/test_parser.py
.venv/bin/python ../tests/test_naming.py
.venv/bin/python ../tests/test_attachments.py
.venv/bin/python ../tests/test_attachment_subdirs.py
.venv/bin/python ../tests/test_scraper.py
.venv/bin/python ../tests/test_webdav.py
.venv/bin/python ../tests/test_files_api.py
.venv/bin/python ../tests/test_rename.py
.venv/bin/python ../tests/test_presence_cache.py
.venv/bin/python ../tests/test_ai_usage.py
.venv/bin/python ../tests/test_ai_pricing.py
.venv/bin/python ../tests/test_ai_holidays.py
```

Frontend checks:

```bash
cd frontend
npx tsc --noEmit
npm run build
```

---

## Project layout

```text
backend/app/
├── api/                  # auth / sources / libraries / items / tasks / settings
├── core/
│   ├── parser.py         # filename, season / episode and tag parsing
│   ├── tag_dictionary.py # editable tag dictionary
│   ├── matcher.py        # TMDb candidates and scoring
│   ├── naming.py         # naming templates and collisions
│   ├── attachments.py    # attachment classification / matching
│   ├── attachment_links.py
│   ├── organizer.py      # moves, attachments, collisions
│   ├── renamer.py        # rename only
│   ├── runner.py         # scan / organize / rename tasks
│   ├── scraper.py        # NFO and artwork
│   ├── metadata/         # TMDb, AI, placeholders
│   └── providers/        # local / WebDAV / SMB
├── db.py                 # SQLite schema
├── security.py
└── main.py

frontend/src/
├── pages/                # overview / libraries / tasks / sources / settings
├── components/           # Layout, FileBrowser, AttachmentPanel, etc.
├── lib/                  # API, hooks, formatting, toast
└── types.ts

tests/                    # regression tests
docker/                   # container entrypoint
```

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

---

## Disclaimer

- This project only renames, moves and organizes metadata. It does not provide, download or play any media.
- Only organize media you have the legal right to access, and follow your local copyright laws.
- This product uses the TMDb API but is not endorsed or certified by TMDb.
- Inspired by Filmix, but independently implemented; it contains no private code or interfaces from that app.

## License

[MIT](LICENSE)
