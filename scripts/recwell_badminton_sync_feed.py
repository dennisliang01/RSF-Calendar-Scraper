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
TEXT_FEED_URL = "https://recwell.berkeley.edu/...YOUR-COPIED-URL..."
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
        except Exception:
            continue

        block = lines[start_i+1:stop_i]
        cur_loc, cur_ev = None, None

        for i, l in enumerate(block):
            # read location / event lines near a time
            loc_m = loc_pat.match(l)
            if loc_m:
                cur_loc = loc_m.group("loc").strip()
                continue
            ev_m = event_pat.match(l)
            if ev_m:
                cur_ev = ev_m.group("ev").strip()
                continue

            t = time_range_pat.match(l)
            if not t:
                continue

            s_raw = t.group("s")
            e_raw = t.group("e")
            ampm = t.group("ampm")  # may be None; we’ll infer

            sh, sm = to_24h(s_raw, ampm)
            eh, em = to_24h(e_raw, ampm)

            start_dt = datetime(date_obj.year, date_obj.month, date_obj.day, sh, sm, tzinfo=TZ)
            end_dt   = datetime(date_obj.year, date_obj.month, date_obj.day, eh, em, tzinfo=TZ)
            if end_dt <= start_dt:
                end_dt += timedelta(days=1)

            if not (today <= start_dt.date() <= end_day):
                continue

            title = cur_ev or CAL_TITLE
            loc   = cur_loc or CAL_LOC_FALLBACK

            uid_src = f"{start_dt.isoformat()}|{end_dt.isoformat()}|{loc}|{title}"
            uid = hashlib.sha1(uid_src.encode()).hexdigest()[:16]

            events.append({
                "title": title,
                "location": loc,
                "start": start_dt,
                "end": end_dt,
                "uid": uid
            })

    # de-dupe
    seen, out = set(), []
    for e in events:
        if e["uid"] not in seen:
            seen.add(e["uid"]); out.append(e)
    return out

def to_gcal(slot):
    return {
        "summary": slot["title"],
        "location": slot["location"],
        "start": {"dateTime": slot["start"].isoformat(), "timeZone": TIMEZONE},
        "end":   {"dateTime": slot["end"].isoformat(),   "timeZone": TIMEZONE},
        "description": "Source: RecWell Badminton (text feed)",
        "extendedProperties": {"private": {"source":"recwell-badminton","recwell_uid":slot["uid"]}}
    }

def sync(service, calendar_id, slots):
    now = datetime.now(TZ)
    win_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    win_end = win_start + timedelta(days=WINDOW_DAYS+1)

    # fetch existing
    events, page = [], None
    while True:
        resp = service.events().list(
            calendarId=calendar_id, timeMin=win_start.isoformat(),
            timeMax=win_end.isoformat(), singleEvents=True, showDeleted=False,
            pageToken=page
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

    # upsert
    for uid, s in desired_by_uid.items():
        body = to_gcal(s)
        if uid in existing_by_uid:
            service.events().update(calendarId=calendar_id, eventId=existing_by_uid[uid]["id"], body=body).execute()
        else:
            service.events().insert(calendarId=calendar_id, body=body).execute()

    # delete stale
    for uid, ev in existing_by_uid.items():
        if uid not in desired_by_uid:
            service.events().delete(calendarId=calendar_id, eventId=ev["id"]).execute()

def main():
    cal_id = os.environ["RECWELL_CALENDAR_ID"]
    txt = fetch_text()
    slots = parse_text_feed(txt)
    service = auth_calendar()
    sync(service, cal_id, slots)
    print(f"✅ Synced {len(slots)} events (text feed) for the next {WINDOW_DAYS} days.")

if __name__ == "__main__":
    main()
