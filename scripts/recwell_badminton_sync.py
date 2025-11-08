import re, hashlib, os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from google.auth.transport.requests import Request

URL = "https://recwell.berkeley.edu/schedules-reservations/badminton/"
TIMEZONE = "America/Los_Angeles"
TITLE = "Badminton (RecWell)"
LOCATION_FALLBACK = "UC Berkeley RecWell"
SCOPES = ["https://www.googleapis.com/auth/calendar"]

def auth_calendar():
    with open("credentials.json", "r", encoding="utf-8") as f:
        creds_json = f.read()
    with open("token.json", "r", encoding="utf-8") as f:
        token_json = f.read()
    creds = Credentials.from_authorized_user_info(eval(token_json), SCOPES)
    if not creds.valid and creds.refresh_token:
        creds.refresh(Request())
        with open("token.json", "w", encoding="utf-8") as f:
            f.write(creds.to_json())
    return build("calendar", "v3", credentials=creds)

def fetch_html(url: str) -> str:
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    return r.text

def parse_slots(html: str):
    soup = BeautifulSoup(html, "html.parser")
    root = soup.select_one("main") or soup

    time_pat = re.compile(
        r"(?P<s>\d{1,2}(:\d{2})?)\s*(?P<samp>a\.?m\.?|p\.?m\.?|am|pm)?\s*[-–—to]+\s*(?P<e>\d{1,2}(:\d{2})?)\s*(?P<eamp>a\.?m\.?|p\.?m\.?|am|pm)?",
        re.I
    )
    tz = ZoneInfo(TIMEZONE)
    today = datetime.now(tz).date()
    window_end = today + timedelta(days=6)

    weekday_map = ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"]
    def find_day(text):
        for w in weekday_map:
            if w.lower() in text.lower():
                return w
        return None

    def to_24h(val, ampm):
        if ":" not in val:
            val += ":00"
        hh, mm = [int(x) for x in val.split(":")]
        a = (ampm or "").replace(".","").lower()
        if a in ("pm","p m") and hh < 12: hh += 12
        if a in ("am","a m") and hh == 12: hh = 0
        return hh, mm

    events = []
    blocks = root.select("h2, h3, .schedule-day, .day") or [root]

    for blk in blocks:
        container = blk.parent if getattr(blk, "name", "") in ("h2","h3") else blk
        lines = [t.get_text(" ", strip=True) for t in container.find_all(["li","p","div","span"])] or [container.get_text(" ", strip=True)]

        header = (blk.get_text(" ", strip=True) if hasattr(blk, "get_text") else "") + " " + " ".join(lines[:3])
        dow = find_day(header)

        # Optional explicit month/day like “Nov 8” or “11/08”
        explicit_date = None
        md = re.search(r"(?P<mon>[A-Za-z]{3,9}|\d{1,2})[ /-](?P<day>\d{1,2})", header)
        if md:
            mon = md.group("mon"); day = int(md.group("day")); year = today.year
            try:
                if mon.isdigit(): explicit_date = datetime(year, int(mon), day, tzinfo=ZoneInfo(TIMEZONE)).date()
                else: explicit_date = datetime.strptime(f"{mon} {day} {year}", "%b %d %Y").date()
            except: pass

        for line in lines:
            m = time_pat.search(line)
            if not m: continue

            loc = LOCATION_FALLBACK
            U = line.upper()
            if "FIELD HOUSE" in U: loc = "RSF Field House, UC Berkeley"
            elif "RSF" in U: loc = "RSF, UC Berkeley"

            event_date = explicit_date
            if not event_date and not dow: dow = find_day(line)
            if not event_date and dow:
                ptr = today
                for _ in range(7):
                    if ptr.strftime("%a").lower().startswith(dow.lower()[:3]):
                        event_date = ptr; break
                    ptr += timedelta(days=1)
            if not event_date: continue
            if not (today <= event_date <= window_end): continue

            s_raw, e_raw = m.group("s"), m.group("e")
            s_ampm, e_ampm = m.group("samp"), m.group("eamp")
            if not e_ampm and s_ampm: e_ampm = s_ampm

            sh, sm = to_24h(s_raw, s_ampm)
            eh, em = to_24h(e_raw, e_ampm)

            start_dt = datetime(event_date.year, event_date.month, event_date.day, sh, sm, tzinfo=ZoneInfo(TIMEZONE))
            end_dt   = datetime(event_date.year, event_date.month, event_date.day, eh, em, tzinfo=ZoneInfo(TIMEZONE))
            if end_dt <= start_dt: end_dt += timedelta(days=1)

            uid_src = f"{start_dt.isoformat()}|{end_dt.isoformat()}|{loc}|{TITLE}"
            uid = hashlib.sha1(uid_src.encode()).hexdigest()[:16]

            events.append({
                "start": start_dt, "end": end_dt,
                "title": TITLE, "location": loc, "uid": uid
            })

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
        "description": "Source: RecWell Badminton (auto-sync)",
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
    html = fetch_html(URL)
    slots = parse_slots(html)
    service = auth_calendar()
    sync(service, calendar_id, slots)
    print(f"✅ Synced {len(slots)} events for the next 6 days.")

if __name__ == "__main__":
    main()
