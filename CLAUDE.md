# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Project Does

Automated Instagram Reels pipeline that runs 6×/day via GitHub Actions. Each run:
1. Selects 3 random unused bike MP4s from Google Drive
2. Downloads a random MP3 from the Drive `music/` subfolder
3. Normalizes videos to 1080×1920 with ffmpeg, concatenates them, mixes audio
4. Posts the final reel (max 59 s) to Instagram via instagrapi
5. Logs the post to a Google Sheets tracker

## Running the Pipeline

**CI (automated):** GitHub Actions cron runs at 7:30, 10:30, 13:30, 16:30, 19:30, 22:30 IST. Manual dispatch supports `force_run=true` to bypass the daily post-count guard.

**Locally:**
```bash
pip install -r requirements_ci.txt
# Required env vars:
export GDRIVE_FOLDER_ID="1CivbmOzwqkCmyOZmmC7UC2p6EGDitjCB"
export IG_USERNAME="..."
export IG_PASSWORD="..."
export IG_SESSION_JSON=$(cat ig_session.json)
export GDRIVE_TOKEN_JSON=$(cat drive_token.json)
python pipeline_ci.py
```

**One-time auth setup:**
```bash
python ig_login.py          # generates ig_session.json
python drive_manager.py auth  # generates drive_token.json
```

**Drive utilities:**
```bash
python drive_manager.py list_videos
python drive_manager.py download_state   # pull tracker from Drive
python drive_manager.py upload_state     # push tracker to Drive
```

## Architecture

### Core Files

| File | Role |
|------|------|
| `pipeline_ci.py` | Main orchestrator — video selection, ffmpeg encoding, Instagram upload |
| `drive_manager.py` | Google Drive OAuth + download/upload for videos and state files |
| `checkpoint.py` | Checkpoint manager; step order: `idle → videos_selected → downloaded → normalized → merged → uploaded` |
| `cleanup.py` | Disk management — keeps last 12 reels, wipes `output/temp/`, rolls back if files missing |
| `sheets_manager.py` | Creates and writes to a Google Sheets post tracker |
| `ig_login.py` | One-time helper to generate `ig_session.json` |

### State Files (not committed)

| File | Purpose |
|------|---------|
| `ig_tracker.json` | Which videos/music have been used; daily post count |
| `ig_checkpoint.json` | Mid-run state for crash recovery |
| `all_videos.json` | Index of all MP4s in the Drive folder |
| `ig_session.json` | Instagram session cookie (reused across runs to avoid 2FA) |
| `drive_token.json` | Google OAuth2 token |

State files live in Google Drive between runs. The CI workflow downloads them at start and uploads them on success.

### Checkpoint / Resume

If any pipeline step fails, the next run resumes from the last saved checkpoint rather than starting over. `checkpoint.py` stores the current step and associated data (selected videos, downloaded paths, etc.).

### ffmpeg Encoding

Videos are normalized in pipeline_ci.py using subprocess ffmpeg calls — no MoviePy for encoding. MoviePy is imported only because instagrapi pulls it in; the actual video work is all ffmpeg. Target format: 1080×1920, 30 fps, CRF 24, AAC 192 k, max 59 s.

## Required GitHub Secrets

`IG_USERNAME`, `IG_PASSWORD`, `IG_SESSION_JSON`, `GDRIVE_TOKEN_JSON`, `GDRIVE_FOLDER_ID` (hardcoded fallback: `1CivbmOzwqkCmyOZmmC7UC2p6EGDitjCB`), `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` (optional notifications).

## Key Constraints

- **Daily cap:** `POSTS_PER_DAY = 6`, enforced via `ig_tracker.json`, resets at midnight UTC.
- **Video source:** Must be MP4s in the root of the Drive folder; music must be MP3s in a `music/` subfolder.
- **Session reuse:** `ig_session.json` is persisted and uploaded back to Drive after each run to avoid triggering Instagram 2FA on every execution.
- **moviepy pinned to 1.0.3** (`requirements_ci.txt`) because instagrapi imports `moviepy.editor`, which was removed in later versions.
