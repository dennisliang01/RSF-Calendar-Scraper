# scripts/recwell_badminton_sync_feed.py
import os, re, json, hashlib
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from google.auth.transport.requests import Request

TIMEZONE = "America/Los_Angeles"
TZ = ZoneInfo(TIMEZONE)
WINDOW_DAYS = 6  # rolling window
CAL_TITLE = "Badminton (RecWell)"
CAL_LOC_FALLBACK = "UC Berkeley RecWell"

# >>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>
# PASTE the exact feed URL you saw in DevTools here:
TEXT_FEED_URL = "https://events.berkeley.edu/live/widget/15/tag/Open%20Rec%20Badminton"
# <<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<

SCOPES = ["https://www.googleapis.com/auth/calendar"]

def auth_calendar():
    with open("token.json", "r", encoding="utf-8") as f:
        token_info = json.loads(f.read())
    creds = Credentials.from_authorized_user_info(token_info, SCOPES)
    if not creds.valid and creds.refresh_token:
        creds.refresh(Request())
        with open("token.json", "w", encoding="utf-8") as f:
            f.write(creds.to_json())
    return build("calendar", "v3", credentials=creds)

def fetch_text():
    r = requests.get(TEXT_FEED_URL, timeout=30)
    r.raise_for_status()
    return r.text

def parse_text_feed(txt: str):
    """
    Parses blocks like:
      Sunday, Nov. 9
      Time: Noon - 4 p.m.
      Location: RSF FieldHouse Court C
      Event: Open Rec Badminton
    into events for the next WINDOW_DAYS.
    """
    # Normalize whitespace
    lines = [re.sub(r"\s+", " ", l).strip() for l in txt.splitlines()]
    lines = [l for l in lines if l]  # drop empties

    # Split into day blocks by date heading
    # Match e.g. "Sunday, Nov. 9", "Mon, Nov 10", "Tuesday, November 11"
    day_idxs = []
    day_pat = re.compile(
        r"^(?P<dow>Mon|Tuesday|Tue|Wednesday|Wed|Thursday|Thu|Friday|Fri|Saturday|Sat|Sunday|Sun)[a-z]*,\s*"
        r"(?P<mon>Jan|January|Feb|February|Mar|March|Apr|April|May|Jun|June|Jul|July|Aug|August|Sep|Sept|September|Oct|October|Nov|November|Dec|December)\.?\s*"
        r"(?P<day>\d{1,2})$",
        re.I
    )
    for i, l in enumerate(lines):
        if day_pat.match(l):
            day_idxs.append(i)
    day_idxs.append(len(lines))  # sentinel

    today = datetime.now(TZ).date()
    end_day = today + timedelta(days=WINDOW_DAYS)

    def month_to_int(mon: str) -> int:
        mon = mon.lower().rstrip(".")
        names = {
            "jan":1,"january":1,"feb":2,"february":2,"mar":3,"march":3,"apr":4,"april":4,
            "may":5,"jun":6,"june":6,"jul":7,"july":7,"aug":8,"august":8,"sep":9,"sept":9,
            "september":9,"oct":10,"october":10,"nov":11,"november":11,"dec":12,"december":12
        }
        return names[mon]

    def parse_time_word(word):
        # Handle Noon/Midnight
        w = word.lower()
        if w in ["noon"]:
            return 12, 0, "pm"
        if w in ["midnight"]:
            return 0, 0, "am"
        return None

    time_range_pat = re.compile(
        r"^time:\s*(?P<s>(?:\d{1,2}(?::\d{2})?)|Noon|Midnight)\s*[-–—to]+\s*(?P<e>(?:\d{1,2}(?::\d{2})?)|Noon|Midnight)\s*(?P<ampm>(a\.?m\.?|p\.?m\.?|am|pm))?$",
        re.I
    )
    loc_pat = re.compile(r"^location:\s*(?P<loc>.+)$", re.I)
    event_pat = re.compile(r"^event:\s*(?P<ev>.+)$", re.I)

    def to_24h(val: str, ampm_hint: str | None):
        word = parse_time_word(val)
        if word:
            h, m, ampm = word
        else:
            ampm = (ampm_hint or "").replace(".","").lower()
            if ":" in val:
                h, m = [int(x) for x in val.split(":")]
            else:
                h, m = int(val), 0
            if ampm in ("pm","p m") and h < 12:
                h += 12
            if ampm in ("am","a m") and h == 12:
                h = 0
        return h, m

    events = []
    for di in range(len(day_idxs) - 1):
        start_i, stop_i = day_idxs[di], day_idxs[di+1]
        head = lines[start_i]
        m = day_pat.match(head)
        if not m:
            continue
        mon_i = month_to_int(m.group("mon"))
        day_i = int(m.group("day"))
        year = today.year
        # roll year on Dec/Jan boundaries if needed
        try:
            date_obj = datetime(year, mon_i, day_i, tzinfo=TZ).date()
            # if the block date already passed far in past, try next year
            if date_obj < today - timedelta(days=7):
                date_obj = datetime(year+1, mon_i, day_i, tzinfo=TZ).date()
        e
