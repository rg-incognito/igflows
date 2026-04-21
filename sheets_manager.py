"""
Google Sheets tracker — logs every posted Instagram Reel.

Sheet columns:
  A: #  B: Date (IST)  C: Video 1  D: Video 2  E: Video 3
  F: Music Source  G: IG URL  H: Status
"""

import os
import json
from pathlib import Path
from datetime import datetime, timedelta

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets",
]

DRIVE_TOKEN_FILE = Path("drive_token.json")
SHEET_ID_FILE    = Path("ig_sheet_id.json")
SHEET_TITLE      = "Bike IG Reels Tracker"

HEADERS = [
    "#", "Date (IST)", "Video 1", "Video 2", "Video 3",
    "Music Source", "IG URL", "Status"
]

# ─── AUTH ─────────────────────────────────────────────────────────────────────
def get_sheets_service():
    creds = Credentials.from_authorized_user_file(str(DRIVE_TOKEN_FILE), SCOPES)
    if not creds.valid and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(DRIVE_TOKEN_FILE, "w") as f:
            f.write(creds.to_json())
    return build("sheets", "v4", credentials=creds, cache_discovery=False)

def get_drive_service():
    creds = Credentials.from_authorized_user_file(str(DRIVE_TOKEN_FILE), SCOPES)
    if not creds.valid and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(DRIVE_TOKEN_FILE, "w") as f:
            f.write(creds.to_json())
    return build("drive", "v3", credentials=creds, cache_discovery=False)

# ─── SHEET MANAGEMENT ─────────────────────────────────────────────────────────
def get_or_create_sheet():
    if SHEET_ID_FILE.exists():
        with open(SHEET_ID_FILE) as f:
            data = json.load(f)
        sheet_id = data.get("sheet_id")
        if sheet_id:
            return sheet_id, f"https://docs.google.com/spreadsheets/d/{sheet_id}"

    drive_svc = get_drive_service()
    q = f"name='{SHEET_TITLE}' and mimeType='application/vnd.google-apps.spreadsheet' and trashed=false"
    res = drive_svc.files().list(q=q, fields="files(id,name)").execute()
    files = res.get("files", [])

    if files:
        sheet_id = files[0]["id"]
    else:
        sheets_svc = get_sheets_service()
        sheet = sheets_svc.spreadsheets().create(body={
            "properties": {"title": SHEET_TITLE},
            "sheets": [{"properties": {"title": "Posts"}}]
        }, fields="spreadsheetId").execute()
        sheet_id = sheet["spreadsheetId"]
        print(f"  Created new sheet: {sheet_id}")
        _setup_sheet(sheets_svc, sheet_id)

    with open(SHEET_ID_FILE, "w") as f:
        json.dump({"sheet_id": sheet_id}, f)

    return sheet_id, f"https://docs.google.com/spreadsheets/d/{sheet_id}"

def _setup_sheet(sheets_svc, sheet_id):
    meta = sheets_svc.spreadsheets().get(spreadsheetId=sheet_id).execute()
    tab_id = meta["sheets"][0]["properties"]["sheetId"]
    requests = [
        {
            "updateCells": {
                "rows": [{"values": [
                    {"userEnteredValue": {"stringValue": h},
                     "userEnteredFormat": {
                         "backgroundColor": {"red": 0.13, "green": 0.55, "blue": 0.13},
                         "textFormat": {"bold": True, "foregroundColor": {"red": 1, "green": 1, "blue": 1}}
                     }}
                    for h in HEADERS
                ]}],
                "fields": "userEnteredValue,userEnteredFormat",
                "start": {"sheetId": tab_id, "rowIndex": 0, "columnIndex": 0}
            }
        },
        {
            "updateSheetProperties": {
                "properties": {"sheetId": tab_id, "gridProperties": {"frozenRowCount": 1}},
                "fields": "gridProperties.frozenRowCount"
            }
        },
        {
            "updateDimensionProperties": {
                "range": {"sheetId": tab_id, "dimension": "COLUMNS", "startIndex": 0, "endIndex": 8},
                "properties": {"pixelSize": 170},
                "fields": "pixelSize"
            }
        }
    ]
    sheets_svc.spreadsheets().batchUpdate(
        spreadsheetId=sheet_id, body={"requests": requests}
    ).execute()

# ─── LOGGING ──────────────────────────────────────────────────────────────────
def log_post(selected_videos, track_info, ig_url, post_number):
    try:
        sheet_id, sheet_url = get_or_create_sheet()
        sheets_svc = get_sheets_service()

        now_ist = datetime.utcnow() + timedelta(hours=5, minutes=30)
        date_str = now_ist.strftime("%Y-%m-%d %H:%M IST")

        vids = list(selected_videos) + ["", ""]
        music_source = f"{track_info.get('title', '')[:40]} (YT Short)"
        row = [
            post_number,
            date_str,
            vids[0], vids[1], vids[2],
            music_source,
            ig_url or "",
            "Posted"
        ]

        sheets_svc.spreadsheets().values().append(
            spreadsheetId=sheet_id,
            range="Posts!A:H",
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body={"values": [row]}
        ).execute()

        print(f"  Sheet updated: row {post_number} logged")
        return sheet_url

    except Exception as e:
        print(f"  Sheet update failed (non-fatal): {e}")
        return None

def get_sheet_url():
    if SHEET_ID_FILE.exists():
        with open(SHEET_ID_FILE) as f:
            data = json.load(f)
        sid = data.get("sheet_id")
        if sid:
            return f"https://docs.google.com/spreadsheets/d/{sid}"
    try:
        _, url = get_or_create_sheet()
        return url
    except Exception:
        return None

if __name__ == "__main__":
    print("Creating/finding IG tracker sheet...")
    sid, url = get_or_create_sheet()
    print(f"Sheet ID  : {sid}")
    print(f"Sheet URL : {url}")
