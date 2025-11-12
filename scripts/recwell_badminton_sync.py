import re, hashlib, os, sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

import json

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from google.auth.transport.requests import Request

URL = "https://recwell.berkeley.edu/schedules-reservations/badminton/"
# LiveWhale widget for Open Rec Badminton events. We add a cache-busting param
# at request time to avoid CDN-stale responses.
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


def _cache_busted(url: str, key: str | None = None) -> str:
    """Append a lightweight cache-busting query param.

    By default, uses the current YYYYMMDD so repeated runs within the same day
    won't spam the CDN but day-to-day updates get a fresh response. Override by
    setting RECWELL_CACHE_BUST=timestamp to force a per-run timestamp.
    """
    bust_mode = (os.getenv("RECWELL_CACHE_BUST") or "day").lower()
    if bust_mode in ("ts", "timestamp", "now"):
        value = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    else:
        value = datetime.utcnow().strftime("%Y%m%d")
    param = key or "_cv"
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}{param}={value}"


def fetch_html(url: str) -> str:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://recwell.berkeley.edu/",
        "Connection": "keep-alive",
        # Try to discourage intermediate caches from serving stale content
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
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

def parse_livewhale_widget(html: str, window_days: int | None = None):
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
    # By default we fetch all available dates. If window_days is provided
    # (positive integer), limit parsed events to the next `window_days` days
    # (inclusive). Historically this script only captured events through
    # the upcoming Sunday; that behavior can be reproduced by setting
    # RECWELL_WINDOW_DAYS=7. When None, no date-based filtering is applied.
    if window_days is None:
        window_end = None
    else:
        # include today through the next (window_days-1) days
        window_end = today + timedelta(days=max(0, int(window_days) - 1))

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

    # Accept headers like "Sun., Nov. 9" or "Sunday, November 9"
    day_pat = re.compile(
        r"^(?P<dow>Mon(?:day)?|Tue(?:sday)?|Wed(?:nesday)?|Thu(?:rsday)?|Fri(?:day)?|Sat(?:urday)?|Sun(?:day)?)\.?\s*,\s*"
        r"(?P<mon>Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?\s*"
        r"(?P<day>\d{1,2})(?:\s*,\s*(?P<year>\d{4}))?$",
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
            # Normalize punctuation in time strings (convert en/em dashes to hyphen)
            time_txt = time_txt.replace("–", "-").replace("—", "-")

            # Extract time range like "Noon - 4 p.m." or "2 - 6 p.m."
            tm = re.search(r"(?P<s>(?:\d{1,2}(?::\d{2})?)|Noon|Midnight)\s*(?:-|–|—|to)\s*(?P<e>(?:\d{1,2}(?::\d{2})?)|Noon|Midnight)\s*(?P<ampm>a\.?m\.?|p\.?m\.?|am|pm)?", time_txt, re.I)
            if not tm:
                # Fallback: allow am/pm on both sides and unicode dashes
                alt = re.search(
                    r"(?P<s>(?:\d{1,2}(?::\d{2})?)|Noon|Midnight)\s*(?P<sampm>a\.?m\.?|p\.?m\.?|am|pm)?\s*(?:-|–|—|to)\s*"
                    r"(?P<e>(?:\d{1,2}(?::\d{2})?)|Noon|Midnight)\s*(?P<eampm>a\.?m\.?|p\.?m\.?|am|pm)?",
                    time_txt,
                    re.I,
                )
                if not alt:
                    continue
                tm = alt
            s_raw, e_raw = tm.group("s"), tm.group("e")
            gd = tm.groupdict()
            sampm = gd.get("sampm") or gd.get("ampm")
            eampm = gd.get("eampm") or gd.get("ampm")
            sh, sm = to_24h(s_raw, sampm)
            eh, em = to_24h(e_raw, eampm)

            start_dt = datetime(date_obj.year, date_obj.month, date_obj.day, sh, sm, tzinfo=tz)
            end_dt   = datetime(date_obj.year, date_obj.month, date_obj.day, eh, em, tzinfo=tz)
            if end_dt <= start_dt:
                end_dt += timedelta(days=1)

            # If window_end is None we accept all dates; otherwise only
            # accept events whose start date is between today and window_end.
            if window_end is not None and not (today <= start_dt.date() <= window_end):
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
    """Fetch and parse only the official events widget feed.

    Adds a daily cache-busting parameter to ensure fresh content after updates.
    Set RECWELL_CACHE_BUST=timestamp to force per-run cache busting.
    """
    widget_html = fetch_html(_cache_busted(WIDGET_URL))
    if os.environ.get("RECWELL_DEBUG"):
        try:
            with open("debug_widget.html", "w", encoding="utf-8") as f:
                f.write(widget_html)
        except Exception:
            pass
    # Allow controlling the time window parsed (in days) via environment.
    # If RECWELL_WINDOW_DAYS is not set, parse all available dates.
    window_days = None
    wd = os.environ.get("RECWELL_WINDOW_DAYS")
    if wd:
        try:
            window_days = int(wd)
            if window_days <= 0:
                window_days = None
        except Exception:
            # ignore parse errors and treat as no limit
            window_days = None

    return parse_livewhale_widget(widget_html, window_days=window_days)

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
    # Query Google Calendar up to next Monday 00:00 (timeMax is exclusive),
    # ensuring all Sunday events are included.
    days_until_next_mon = (7 - window_start.weekday()) % 7 or 7
    next_monday = (window_start + timedelta(days=days_until_next_mon)).replace(hour=0, minute=0, second=0, microsecond=0)
    window_end = next_monday

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
    # Accept calendar id from RECWELL_CALENDAR_ID env var or first CLI arg
    calendar_id = os.environ.get("RECWELL_CALENDAR_ID")
    if not calendar_id and len(sys.argv) > 1:
        # allow: python recwell_badminton_sync.py <calendar_id>
        calendar_id = sys.argv[1]

    if not calendar_id:
        print("ERROR: RECWELL_CALENDAR_ID environment variable is not set and no calendar id was provided as a command-line argument.")
        print("")
        print("Set it in your shell before running the script, for example (bash):")
        print("  export RECWELL_CALENDAR_ID='your_calendar_id'")
        print("")
        print("Or pass it as the first argument:")
        print("  python recwell_badminton_sync.py your_calendar_id")
        # On Windows PowerShell use: $Env:RECWELL_CALENDAR_ID = 'your_calendar_id'
        sys.exit(1)

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





