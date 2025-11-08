RSF Badminton Calendar Sync
 
Syncs UC Berkeley RecWell Open Rec Badminton sessions into a Google Calendar.

The scraper uses the official UC Berkeley events widget as the primary source of truth and upserts events into your calendar using the Google Calendar API. The RecWell badminton page is included in event details for reference.

**What It Does**
- Parses the Open Rec Badminton widget feed: `https://events.berkeley.edu/live/widget/15/tag/Open%20Rec%20Badminton`.
- Parses day headers and time ranges (supports Noon/Midnight, 2–6 p.m., etc.).
- Normalizes to America/Los_Angeles and a 6-day rolling window.
- Inserts/updates Google Calendar events; removes stale ones in the window.

## Project Layout
- `scripts/recwell_badminton_sync.py` — main scraper/sync script (LiveWhale widget parser).
- `scripts/bootstrap_oauth.py` — helper to create `token.json` for Google OAuth locally.
- `requirements.txt` — Python dependencies.
- `.github/workflows/recwell-sync.yml` — GitHub Actions job to run the sync daily.
- `credentials.json` — your Google OAuth client (Desktop) JSON (not committed; provided via Secrets in CI).
- `token.json` — your authorized user token JSON (not committed; provided via Secrets in CI).

## Prerequisites
- Python 3.11+ (3.12 tested)
- A Google Cloud project with an OAuth 2.0 Client ID (Desktop) and Calendar API enabled.
- A target Google Calendar ID where events should be synced.

## Local Setup
1. Create and activate a virtual environment, then install deps:
   - Windows PowerShell
     - `python -m venv .venv`
     - `.\.venv\Scripts\python.exe -m pip install -r requirements.txt`

2. Place your OAuth client JSON as `credentials.json` in the repo root.

3. Bootstrap OAuth to create `token.json`:
   - `python scripts/bootstrap_oauth.py`
   - Follow the browser or console flow; this writes `token.json`.

4. Dry-run (no calendar writes):
   - `set RECWELL_DRY_RUN=1`
   - `.\.venv\Scripts\python.exe scripts\recwell_badminton_sync.py`
   - You should see a list of parsed events for the upcoming days.

5. Real sync (writes to your calendar):
   - `set RECWELL_CALENDAR_ID=your_calendar_id@group.calendar.google.com`
   - `.\.venv\Scripts\python.exe scripts\recwell_badminton_sync.py`

Notes
- Timezone is America/Los_Angeles using Python `zoneinfo` (Windows users get tz data via the `tzdata` package).
- The rolling window is 6 days; adjust inside the script if desired.

## GitHub Actions (Daily Sync)
This repo includes a workflow that runs once per day and syncs events.

- File: `.github/workflows/recwell-sync.yml`
- Schedule: `15 15 * * *` (15:15 UTC daily)
- Manual run is also available via the Actions tab.

### Required GitHub Secrets
Add these in your repository Settings → Secrets and variables → Actions → New repository secret:
- `GOOGLE_CREDENTIALS` — entire contents of your `credentials.json`.
- `GOOGLE_TOKEN` — entire contents of your locally generated `token.json`.
- `RECWELL_CALENDAR_ID` — your destination Google Calendar ID.

The workflow restores these files/secrets and runs:
- `python scripts/recwell_badminton_sync.py`

## Customization
- Change the target calendar: set `RECWELL_CALENDAR_ID` (locally or in GitHub Secrets).
- Change time window: in `scripts/recwell_badminton_sync.py`, update the `timedelta(days=6)` occurrences.
- Alternate sources: if RecWell later provides additional machine-readable endpoints, they can be added easily.

## Troubleshooting
- "Found 0 events (dry run)":
  - The badminton page may not list times for the current 6‑day window; the widget feed should still populate when events exist.
  - Network or site changes can affect parsing; check the widget URL in a browser.
- OAuth errors:
  - Re-run `scripts/bootstrap_oauth.py` to refresh `token.json`.
  - Ensure Calendar API is enabled and the OAuth client type is "Desktop".
- Timezone errors on Windows:
  - Ensure `tzdata` installed (already in `requirements.txt`).

## License
- Internal project; no license file included. Add one if you plan to publish.
