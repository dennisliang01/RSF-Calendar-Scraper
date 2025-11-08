# scripts/bootstrap_oauth.py
# Robust OAuth bootstrap: tries local-server first (several ports), then console fallback.

import json
import os
import webbrowser

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/calendar"]
CREDS_FILE = "credentials.json"
TOKEN_FILE = "token.json"

def write_token(creds):
    with open(TOKEN_FILE, "w", encoding="utf-8") as f:
        f.write(creds.to_json())
    print(f"✅ {TOKEN_FILE} created. Copy its full contents into your GitHub Secret GOOGLE_TOKEN.")

def try_local_server(flow):
    # Try a few ports in case one is blocked
    for port in (0, 8080, 8765, 5000):
        try:
            print(f"→ Trying local OAuth server on port {port} ...")
            creds = flow.run_local_server(port=port, open_browser=True)
            print("✅ OAuth success via local server.")
            return creds
        except Exception as e:
            print(f"⚠️  Local server attempt on port {port} failed: {e}")
    return None

def run_console_flow(flow):
    print("→ Falling back to console flow.")
    # This prints a URL; you paste the code back here.
    creds = flow.run_console()
    print("✅ OAuth success via console.")
    return creds

def main():
    if not os.path.exists(CREDS_FILE):
        raise FileNotFoundError(
            f"credentials.json not found in: {os.getcwd()}\n"
            "Place your Google Cloud OAuth client (Desktop) file here and name it credentials.json."
        )

    print(f"Using {CREDS_FILE} in {os.getcwd()}")
    flow = InstalledAppFlow.from_client_secrets_file(CREDS_FILE, SCOPES)

    creds = try_local_server(flow)
    if creds is None:
        # Try opening default browser manually just in case
        try:
            webbrowser.open("about:blank")
        except Exception:
            pass
        creds = run_console_flow(flow)

    write_token(creds)
    print("Done. Add GOOGLE_CREDENTIALS and GOOGLE_TOKEN as GitHub Secrets next.")

if __name__ == "__main__":
    main()
