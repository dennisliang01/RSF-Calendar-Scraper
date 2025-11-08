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
    try:
        from urllib.parse import urljoin
        return urljoin(base, href)
    except Exception:
        return href

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


def parse_slots_v2(html: str):
    """Robust HTML parser handling Noon/Midnight, dashes, and table layouts."""
    soup = BeautifulSoup(html, "html.parser")
    root = soup.select_one("main") or soup

    time_pat = re.compile(
        r"(?P<s>(\\d{1,2}(:\\d{2})?)|Noon|Midnight)\\s*"
        r"(?P<samp>a\\.?m\\.?|p\\.?m\\.?|am|pm)?\\s*"
        r"(?:-|–|—|to)\\s*"
        r"(?P<e>(\\d{1,2}(:\\d{2})?)|Noon|Midnight)\\s*"
        r"(?P<eamp>a\\.?m\\.?|p\\.?m\\.?|am|pm)?",
        re.I
    )

    tz = ZoneInfo(TIMEZONE)
    today = datetime.now(tz).date()
    window_end = today + timedelta(days=6)

    weekday_aliases = [
        ("Mon", "Monday"), ("Tue", "Tuesday"), ("Wed", "Wednesday"),
        ("Thu", "Thursday"), ("Fri", "Friday"), ("Sat", "Saturday"), ("Sun", "Sunday")
    ]

    def find_day(text):
        T = (text or "").lower()
        for short, full in weekday_aliases:
            if short.lower() in T or full.lower() in T:
                return short
        return None

    def parse_time_word(word):
        w = (word or "").strip().lower()
        if w == "noon":
            return 12, 0
        if w == "midnight":
            return 0, 0
        return None

    def to_24h(val, ampm):
        w = parse_time_word(val)
        if w:
            return w
        v = str(val)
        if ":" not in v:
            v += ":00"
        hh, mm = [int(x) for x in v.split(":")]
        a = (ampm or "").replace(".", "").lower()
        if a in ("pm", "p m") and hh < 12:
            hh += 12
        if a in ("am", "a m") and hh == 12:
            hh = 0
        return hh, mm

    events = []

    blocks = root.select("table, .fusion-text, h2, h3, .schedule-day, .day") or [root]

    for blk in blocks:
        lines = []
        if getattr(blk, "name", "") == "table":
            for tr in blk.select("tr"):
                cells = [c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])]
                if cells:
                    lines.append(" | ".join(cells))
        else:
            container = blk.parent if getattr(blk, "name", "") in ("h2", "h3") else blk
            parts = container.find_all(["li", "p", "div", "span"])
            if parts:
                lines = [t.get_text(" ", strip=True) for t in parts]
            else:
                lines = [container.get_text(" ", strip=True)]

        header = (blk.get_text(" ", strip=True) if hasattr(blk, "get_text") else "") + " " + " ".join(lines[:3])
        dow = find_day(header)

        explicit_date = None
        md = re.search(r"(?P<mon>[A-Za-z]{3,9}|\\d{1,2})[ /-](?P<day>\\d{1,2})", header)
        if md:
            mon = md.group("mon")
            day = int(md.group("day"))
            year = today.year
            try:
                if mon.isdigit():
                    explicit_date = datetime(year, int(mon), day, tzinfo=tz).date()
                else:
                    try:
                        explicit_date = datetime.strptime(f"{mon} {day} {year}", "%b %d %Y").date()
                    except ValueError:
                        explicit_date = datetime.strptime(f"{mon} {day} {year}", "%B %d %Y").date()
            except Exception:
                explicit_date = None

        for line in lines:
            m = time_pat.search(line)
            if not m:
                continue

            loc = LOCATION_FALLBACK
            U = line.upper()
            if "FIELD HOUSE" in U or "FIELDHOUSE" in U:
                loc = "RSF Field House, UC Berkeley"
            elif "RSF" in U:
                loc = "RSF, UC Berkeley"

            event_date = explicit_date
            if not event_date and not dow:
                dow = find_day(line)

            if not event_date and dow:
                ptr = today
                for _ in range(7):
                    if ptr.strftime("%a").lower().startswith(dow.lower()[:3]):
                        event_date = ptr
                        break
                    ptr += timedelta(days=1)

            if not event_date:
                continue
            if not (today <= event_date <= window_end):
                continue

            s_raw, e_raw = m.group("s"), m.group("e")
            s_ampm, e_ampm = m.group("samp"), m.group("eamp")
            if not e_ampm and s_ampm:
                e_ampm = s_ampm

            sh, sm = to_24h(s_raw, s_ampm)
            eh, em = to_24h(e_raw, e_ampm)

            start_dt = datetime(event_date.year, event_date.month, event_date.day, sh, sm, tzinfo=tz)
            end_dt = datetime(event_date.year, event_date.month, event_date.day, eh, em, tzinfo=tz)
            if end_dt <= start_dt:
                end_dt += timedelta(days=1)

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
            loc_txt = (row.select_one(".location") or row.select_one(".location.column")).get_text(" ", strip=True) if row.select_one(".location") or row.select_one(".location.column") else LOCATION_FALLBACK
            title_txt = (row.select_one(".event-title") or row.select_one(".event-title.column") or row).get_text(" ", strip=True)
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

def discover_related_pages(start_url: str, html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    candidates, seen = [], set()
    keywords = ["schedule", "reservations", "court", "field", "open", "badminton"]
    for a in soup.find_all("a", href=True):
        href = (a.get("href") or "").strip()
        txt = (a.get_text(" ", strip=True) or "").lower()
        href_l = href.lower()
        if href_l.startswith("http") and "recwell.berkeley.edu" not in href_l:
            continue
        if any(k in href_l for k in keywords) or any(k in txt for k in keywords):
            url = _absolutize(start_url, href)
            if url not in seen:
                seen.add(url)
                candidates.append(url)
    return candidates[:8]

def gather_slots_from_site(start_url: str) -> list[dict]:
    start_html = fetch_html(start_url)
    slots = parse_slots_v2(start_html)
    out = []
    out.extend(slots)
    # Try LiveWhale widget as an additional source
    try:
        widget_html = fetch_html(WIDGET_URL)
        out.extend(parse_livewhale_widget(widget_html))
    except Exception:
        pass
    # Fall back to likely related internal pages
    for url in discover_related_pages(start_url, start_html):
        try:
            html = fetch_html(url)
            found = parse_slots_v2(html)
            out.extend(found)
        except Exception:
            continue
    seen, uniq = set(), []
    for s in out:
        uid = s.get("uid")
        if not uid or uid not in seen:
            if uid:
                seen.add(uid)
            uniq.append(s)
    return uniq

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
    # Use the specified site with a fallback to related internal pages
    slots = gather_slots_from_site(URL)
    service = auth_calendar()
    sync(service, calendar_id, slots)
    print(f"✅ Synced {len(slots)} events for the next 6 days.")

if __name__ == "__main__":
    _dry = os.environ.get("RECWELL_DRY_RUN", "").strip()
    if _dry not in ("", "0", "false", "False", "no", "No"):
        slots = gather_slots_from_site(URL)
        print(f"Found {len(slots)} events (dry run):")
        for s in slots:
            try:
                print(f"- {s['start'].strftime('%a %Y-%m-%d %H:%M')} -> {s['end'].strftime('%H:%M')} | {s['location']} | {s['title']}")
            except Exception:
                print(s)
    else:
        main()


