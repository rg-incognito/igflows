"""
Google Drive Manager — uses regular OAuth (your personal Google account).
No service account needed. Works with any normal Google Drive folder.

Usage:
  python drive_manager.py auth              # one-time login (run on PC)
  python drive_manager.py download_state   # pull tracker + checkpoint
  python drive_manager.py upload_state     # push tracker + checkpoint
  python drive_manager.py download_videos  # download specific videos
  python drive_manager.py list_videos      # list all videos in Drive folder
  python drive_manager.py upload_videos    # one-time: upload all local videos to Drive
"""

import json
import os
import sys
from pathlib import Path

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

# ─── CONFIG ───────────────────────────────────────────────────────────────────
SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets",
]

DRIVE_TOKEN_FILE = Path("drive_token.json")
YT_CLIENT_FILE   = Path("yt_client_secret.json")

# Same Drive folder as the normal YT pipeline — shared video library
FOLDER_ID     = os.environ.get("GDRIVE_FOLDER_ID", "1CivbmOzwqkCmyOZmmC7UC2p6EGDitjCB")
LOCAL_REELS_DIR = Path("reels")
LOCAL_MUSIC_DIR = Path("music")

# IG pipeline does not use yt_token — only tracker, checkpoint, video index, sheet id
STATE_FILES = ["ig_tracker.json", "ig_checkpoint.json", "all_videos.json", "ig_sheet_id.json", "ig_session.json"]

# ─── AUTH ─────────────────────────────────────────────────────────────────────
def get_service():
    creds = None
    if DRIVE_TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(DRIVE_TOKEN_FILE), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            print("  Refreshing Drive token...")
            creds.refresh(Request())
            with open(DRIVE_TOKEN_FILE, "w") as f:
                f.write(creds.to_json())
        else:
            raise RuntimeError(
                "No valid Drive token found.\n"
                "Run on your PC first: python drive_manager.py auth"
            )
    return build("drive", "v3", credentials=creds, cache_discovery=False)

def auth():
    print("\nOpening browser for Google Drive login...")
    with open(YT_CLIENT_FILE) as f:
        raw = json.load(f)
    cred_type = "web" if "web" in raw else "installed"
    flow = InstalledAppFlow.from_client_secrets_file(str(YT_CLIENT_FILE), SCOPES)
    if cred_type == "web":
        creds = flow.run_local_server(port=8080, open_browser=True)
    else:
        creds = flow.run_local_server(port=0, open_browser=True)
    with open(DRIVE_TOKEN_FILE, "w") as f:
        f.write(creds.to_json())
    print(f"\n  drive_token.json saved!")
    print(f"\n  Add as GitHub secret:")
    print(f"  Secret name : GDRIVE_TOKEN_JSON")
    print(f"  Secret value: (contents below)\n")
    print(DRIVE_TOKEN_FILE.read_text())

# ─── HELPERS ──────────────────────────────────────────────────────────────────
def find_file(service, name, parent_id):
    q = f"name='{name}' and '{parent_id}' in parents and trashed=false"
    res = service.files().list(q=q, fields="files(id,name)").execute()
    files = res.get("files", [])
    return files[0]["id"] if files else None

def find_or_create_folder(service, name, parent_id):
    q = (f"name='{name}' and '{parent_id}' in parents "
         f"and mimeType='application/vnd.google-apps.folder' and trashed=false")
    res = service.files().list(q=q, fields="files(id)").execute()
    files = res.get("files", [])
    if files:
        return files[0]["id"]
    meta = {
        "name": name,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [parent_id]
    }
    f = service.files().create(body=meta, fields="id").execute()
    print(f"  Created subfolder: {name}")
    return f["id"]

def download_file(service, file_id, dest_path):
    dest_path = Path(dest_path)
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    request = service.files().get_media(fileId=file_id)
    with open(dest_path, "wb") as f:
        downloader = MediaIoBaseDownload(f, request, chunksize=4*1024*1024)
        done = False
        while not done:
            status, done = downloader.next_chunk()
            if status:
                pct = int(status.progress() * 100)
                print(f"\r  Downloading {dest_path.name}: {pct}%", end="", flush=True)
    print(f"\r  Downloaded: {dest_path.name}                    ")

def upload_file(service, local_path, parent_id, mime="application/octet-stream"):
    local_path = Path(local_path)
    existing_id = find_file(service, local_path.name, parent_id)
    media = MediaFileUpload(str(local_path), mimetype=mime, resumable=True)
    if existing_id:
        service.files().update(fileId=existing_id, media_body=media).execute()
    else:
        meta = {"name": local_path.name, "parents": [parent_id]}
        service.files().create(body=meta, media_body=media, fields="id").execute()
    print(f"  Uploaded: {local_path.name}")

def list_folder(service, folder_id, name_filter=None):
    results = []
    page_token = None
    while True:
        q = f"'{folder_id}' in parents and trashed=false"
        if name_filter:
            q += f" and name contains '{name_filter}'"
        res = service.files().list(
            q=q, fields="nextPageToken,files(id,name)",
            pageSize=1000, pageToken=page_token
        ).execute()
        results.extend(res.get("files", []))
        page_token = res.get("nextPageToken")
        if not page_token:
            break
    return results

# ─── OPERATIONS ───────────────────────────────────────────────────────────────
def download_state():
    print("Downloading state files from Google Drive...")
    service = get_service()
    for fname in STATE_FILES:
        fid = find_file(service, fname, FOLDER_ID)
        if fid:
            download_file(service, fid, fname)
        else:
            print(f"  Not in Drive yet: {fname}")
    print("State download done.")

def upload_state():
    print("Uploading state files to Google Drive...")
    service = get_service()
    for fname in STATE_FILES:
        if Path(fname).exists():
            upload_file(service, fname, FOLDER_ID, mime="application/json")
    if DRIVE_TOKEN_FILE.exists():
        upload_file(service, DRIVE_TOKEN_FILE, FOLDER_ID, mime="application/json")
    print("State upload done.")

def download_videos(video_names):
    print(f"Downloading {len(video_names)} video(s) from Drive...")
    service = get_service()
    all_files = list_folder(service, FOLDER_ID, name_filter=".mp4")
    name_to_id = {f["name"]: f["id"] for f in all_files}
    LOCAL_REELS_DIR.mkdir(exist_ok=True)
    for name in video_names:
        dest = LOCAL_REELS_DIR / name
        if dest.exists():
            print(f"  Cached: {name}")
            continue
        fid = name_to_id.get(name)
        if not fid:
            raise FileNotFoundError(f"Video not found in Drive: {name}")
        download_file(service, fid, dest)

def list_videos():
    service = get_service()
    files = list_folder(service, FOLDER_ID, name_filter=".mp4")
    for f in sorted(files, key=lambda x: x["name"]):
        print(f["name"])
    print(f"\nTotal: {len(files)} videos")

def upload_videos_local():
    service = get_service()
    existing = {f["name"] for f in list_folder(service, FOLDER_ID)}
    all_local = sorted(LOCAL_REELS_DIR.glob("*.mp4"))
    to_upload = [f for f in all_local if f.name not in existing]
    print(f"Already in Drive : {len(existing)}")
    print(f"To upload        : {len(to_upload)}")
    for i, video in enumerate(to_upload, 1):
        size_mb = video.stat().st_size / (1024*1024)
        print(f"[{i}/{len(to_upload)}] {video.name} ({size_mb:.1f} MB)")
        upload_file(service, video, FOLDER_ID, mime="video/mp4")
    print(f"\nDone! {len(to_upload)} videos uploaded.")

# ─── CLI ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "auth":
        auth()
    elif cmd == "download_state":
        download_state()
    elif cmd == "upload_state":
        upload_state()
    elif cmd == "download_videos":
        download_videos(sys.argv[2:])
    elif cmd == "list_videos":
        list_videos()
    elif cmd == "upload_videos":
        upload_videos_local()
    else:
        print("Commands: auth | download_state | upload_state | list_videos | upload_videos")
