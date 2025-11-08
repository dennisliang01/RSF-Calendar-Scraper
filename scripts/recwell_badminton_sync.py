import re, hashlib, os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

import json

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from google.auth.transport.requests import Request

URL = "https://recwell.berkeley.edu/schedules-reservations/badminton/"
WIDGET_URL = "https://events.berkeley.edu/live/widget/15/tag/Open%20Rec%20Badminton"
RECWELL_PAGE = URL
TIMEZONE = "America/Los_Angeles"
TITLE = "Badminton (RecWell)"
LOCATION_FALLBACK = "UC Berkeley RecWell"
SCOPES = ["https://www.googleapis.com/auth/calendar"]

def auth_calendar():
    # Read verbatim JSON written by the workflow
    with open("token.json", "r", encoding="utf-8") as f:
        token_info = json.loads(f.read())

    creds = Credentials.from_authorized_user_info(token_info, SCOPES)

    if not creds.valid and creds.refresh_token:
        creds.refresh(Request())
        # persist refreshed token
        with open("token.json", "w", encoding="utf-8") as f:
            f.write(creds.to_json())

    return build("calendar", "v3", credentials=creds)


def fetch_html(url: str) -> str:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://recwell.berkeley.edu/",
        "Connection": "keep-alive",
    }
    r = requests.get(url, headers=headers, timeout=30)
    r.raise_for_status()
    return r.text

def _absolutize(base: str, href: str) -> str:
    # No longer used; kept for minimal diff compatibility. Will be removed in a future cleanup.
    return href

def parse_slots(html: str):
    return []

def discover_related_pages(start_url: str, html: str) -> list[str]:    return []
def gather_slots_from_site(start_url: str) -> list[dict]:    return gather_slots()
def gather_slots() -> list[dict]:
    """Fetch and parse only the official events widget feed."""
    widget_html = fetch_html(WIDGET_URL)
    return parse_livewhale_widget(widget_html)

def to_gcal(slot):
    return {
        "summary": slot["title"],
        "location": slot["location"],
        "start": {"dateTime": slot["start"].isoformat(), "timeZone": TIMEZONE},
        "end":   {"dateTime": slot["end"].isoformat(),   "timeZone": TIMEZONE},
        "description": f"Source: RecWell Badminton (auto-sync){RECWELL_PAGE}",
        "extendedProperties": {"private": {"source":"recwell-badminton","recwell_uid":slot["uid"]}}
    }

def sync(service, calendar_id, slots):
    tz = ZoneInfo(TIMEZONE)
    now = datetime.now(tz)
    window_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    window_end = window_start + timedelta(days=7)

    events, page = [], None
    while True:
        resp = service.events().list(
            calendarId=calendar_id, timeMin=window_start.isoformat(),
            timeMax=window_end.isoformat(), singleEvents=True,
            showDeleted=False, pageToken=page
        ).execute()
        events += resp.get("items", [])
        page = resp.get("nextPageToken")
        if not page: break

    existing_by_uid = {}
    for ev in events:
        ep = (ev.get("extendedProperties") or {}).get("private", {})
        if ep.get("source") == "recwell-badminton" and "recwell_uid" in ep:
            existing_by_uid[ep["recwell_uid"]] = ev

    desired_by_uid = {s["uid"]: s for s in slots}

    for uid, s in desired_by_uid.items():
        body = to_gcal(s)
        if uid in existing_by_uid:
            service.events().update(calendarId=calendar_id, eventId=existing_by_uid[uid]["id"], body=body).execute()
        else:
            service.events().insert(calendarId=calendar_id, body=body).execute()

    for uid, ev in existing_by_uid.items():
        if uid not in desired_by_uid:
            service.events().delete(calendarId=calendar_id, eventId=ev["id"]).execute()

def main():
    calendar_id = os.environ["RECWELL_CALENDAR_ID"]
    # Use the specified site with a fallback to related internal pages
    slots = gather_slots()
    service = auth_calendar()
    sync(service, calendar_id, slots)
    print(f"✅ Synced {len(slots)} events for the next 6 days.")

if __name__ == "__main__":
    _dry = os.environ.get("RECWELL_DRY_RUN", "").strip()
    if _dry not in ("", "0", "false", "False", "no", "No"):
        slots = gather_slots()
        print(f"Found {len(slots)} events (dry run):")
        for s in slots:
            try:
                print(f"- {s['start'].strftime('%a %Y-%m-%d %H:%M')} -> {s['end'].strftime('%H:%M')} | {s['location']} | {s['title']}")
            except Exception:
                print(s)
    else:
        main()





