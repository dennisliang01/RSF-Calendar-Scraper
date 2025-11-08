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

def parse_livewhale_widget(html: str):
    """Parse the events.berkeley LiveWhale widget HTML for Open Rec Badminton.
    Expected structure:
      <div class="lw_events_day">
        <h4 class="lw_events_header_date">Sunday, Nov. 9</h4>
        <div class="event row">
          <div class="time column">Noon - 4 p.m.</div>
          <div class="location column">RSF FieldHouse Court C</div>
          <div class="event-title column">Open Rec Badminton</div>
        </div>
      </div>
    """
    soup = BeautifulSoup(html, "html.parser")
    tz = ZoneInfo(TIMEZONE)
    today = datetime.now(tz).date()
    window_end = today + timedelta(days=6)

    def month_to_int(mon: str) -> int:
        mon = mon.lower().rstrip(".")
        names = {
            "jan":1,"january":1,"feb":2,"february":2,"mar":3,"march":3,"apr":4,"april":4,
            "may":5,"jun":6,"june":6,"jul":7,"july":7,"aug":8,"august":8,"sep":9,"sept":9,
            "september":9,"oct":10,"october":10,"nov":11,"november":11,"dec":12,"december":12
        }
        return names[mon]

    def parse_time_word(word):
        w = (word or "").strip().lower()
        if w == "noon":
            return 12, 0, "pm"
        if w == "midnight":
            return 0, 0, "am"
        return None

    def to_24h(val: str, ampm_hint: str | None):
        word = parse_time_word(val)
        if word:
            h, m, ampm = word
        else:
            ampm = (ampm_hint or "").replace(".","").lower()
            v = val.strip()
            if ":" in v:
                h, m = [int(x) for x in v.split(":", 1)]
            else:
                h, m = int(v), 0
            if ampm in ("pm","p m") and h < 12:
                h += 12
            if ampm in ("am","a m") and h == 12:
                h = 0
        return h, m

    day_pat = re.compile(
        r"^(?P<dow>Mon|Monday|Tue|Tuesday|Wed|Wednesday|Thu|Thursday|Fri|Friday|Sat|Saturday|Sun|Sunday),\s*(?P<mon>Jan|January|Feb|February|Mar|March|Apr|April|May|Jun|June|Jul|July|Aug|August|Sep|Sept|September|Oct|October|Nov|November|Dec|December)\.?\s*(?P<day>\d{1,2})$",
        re.I
    )
    events = []
    for day in soup.select(".lw_events_day"):
        head = day.select_one(".lw_events_header_date")
        if not head:
            continue
        m = day_pat.match(head.get_text(strip=True))
        if not m:
            continue
        mon_i = month_to_int(m.group("mon"))
        day_i = int(m.group("day"))
        year = today.year
        try:
            date_obj = datetime(year, mon_i, day_i, tzinfo=tz).date()
            if date_obj < today - timedelta(days=7):
                date_obj = datetime(year+1, mon_i, day_i, tzinfo=tz).date()
        except Exception:
            continue

        for row in day.select(".event.row"):
            time_txt = (row.select_one(".time") or row.select_one(".time.column") or row).get_text(" ", strip=True)
            loc_node = (row.select_one(".location") or row.select_one(".location.column"))
            loc_txt = loc_node.get_text(" ", strip=True) if loc_node else LOCATION_FALLBACK
            title_node = (row.select_one(".event-title") or row.select_one(".event-title.column") or row)
            title_txt = title_node.get_text(" ", strip=True)
            # Strip audible labels that may be included in text
            loc_txt = re.sub(r"^(?:Location:?)\s*", "", loc_txt, flags=re.I)
            title_txt = re.sub(r"^(?:Event:?)\s*", "", title_txt, flags=re.I)

            # Extract time range like "Noon - 4 p.m." or "2 - 6 p.m."
            tm = re.search(r"(?P<s>(?:\d{1,2}(?::\d{2})?)|Noon|Midnight)\s*(?:-|–|—|to)\s*(?P<e>(?:\d{1,2}(?::\d{2})?)|Noon|Midnight)\s*(?P<ampm>a\.?m\.?|p\.?m\.?|am|pm)?", time_txt, re.I)
            if not tm:
                continue
            s_raw, e_raw, ampm = tm.group("s"), tm.group("e"), tm.group("ampm")
            sh, sm = to_24h(s_raw, ampm)
            eh, em = to_24h(e_raw, ampm)

            start_dt = datetime(date_obj.year, date_obj.month, date_obj.day, sh, sm, tzinfo=tz)
            end_dt   = datetime(date_obj.year, date_obj.month, date_obj.day, eh, em, tzinfo=tz)
            if end_dt <= start_dt:
                end_dt += timedelta(days=1)

            if not (today <= start_dt.date() <= window_end):
                continue

            title = title_txt or TITLE
            loc = loc_txt or LOCATION_FALLBACK
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





