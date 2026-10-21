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
- Matches TMDb metadata, with manual search, batch correction and episode fixing;
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

Prebuilt image: `ghcr.io/oz6d/cineflow:latest`, available for `linux/amd64` and `linux/arm64`.

### 1. Prepare

```bash
git clone https://github.com/Oz6d/Cineflow.git
cd cineflow
cp .env.example .env
```

Edit `.env` and at least change:

```dotenv
CINEFLOW_ADMIN_PASSWORD=change-me
# Optional: fixed encryption master key
# CINEFLOW_SECRET_KEY=...
# Host media folder
MEDIA_PATH=/path/to/your/media
```

### 2. Start

Pull the prebuilt image:

```bash
docker compose pull
docker compose up -d
```

Or build from the local source:

```bash
docker compose up -d --build
```

Open `http://localhost:8080` and log in with the admin password from `.env`.

> Change the default password in **Settings → Security** after the first login.
> Runtime data lives in `./data`; back it up regularly and keep `data/secret.key` private.
> If the GHCR package is not visible, set it to Public under **Packages → cineflow → Package settings** in the GitHub repository.

### 3. Configure

1. Add a local / WebDAV / SMB source under **Sources** and test the connection;
2. Add a TMDb API key or v4 Read Access Token under **Settings → TMDb**;
3. Create a library with a source, pending folder and target folder;
4. **Scan** → review under **Needs review** → **Organize preview** → **Organize**.

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
