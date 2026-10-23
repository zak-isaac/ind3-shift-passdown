"""Screenshot walkthrough capture for the IND3 Passdown Server.

Captures real screenshots of the LIVE production app (read-only pages only --
no forms are submitted, no data is created or modified) to build a training
walkthrough for onboarding.

Run with the server already up on localhost:8766.
"""
import re
from pathlib import Path
from playwright.sync_api import sync_playwright

BASE = "http://localhost:8766"
OUT = Path(__file__).resolve().parent / "docs" / "walkthrough" / "screenshots"
OUT.mkdir(parents=True, exist_ok=True)

# Emergency/manager PIN documented in main.py (_EMERGENCY_CODE) -- grants
# lead access without needing a real associate's PIN. Read-only usage here.
LEAD_PIN = "8972"

VIEWPORT = {"width": 1440, "height": 900}


def shot(page, name: str, full_page: bool = True):
    path = OUT / f"{name}.png"
    page.screenshot(path=str(path), full_page=full_page)
    print(f"  saved {path.name}")


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport=VIEWPORT)

        print("1. Homepage")
        page.goto(f"{BASE}/", wait_until="networkidle")
        shot(page, "01_homepage")

        print("2. Tech login screen")
        page.goto(f"{BASE}/tech/login", wait_until="networkidle")
        shot(page, "02_tech_login")

        print("3. MST/Lead login screen")
        page.goto(f"{BASE}/lead/login", wait_until="networkidle")
        shot(page, "03_lead_login")

        print("4. Logging in as lead (emergency/manager code, read-only)")
        page.fill("#pinInput", LEAD_PIN)
        page.click("#submitBtn")
        page.wait_for_load_state("networkidle")
        shot(page, "04_lead_dashboard")

        print("5. Shift history")
        page.goto(f"{BASE}/history", wait_until="networkidle")
        shot(page, "05_shift_history")

        print("6. Analytics dashboard")
        page.goto(f"{BASE}/analytics", wait_until="networkidle")
        page.wait_for_timeout(2500)  # Chart.js draws async -- give it real time
        shot(page, "06_analytics")

        print("7. User guide")
        page.goto(f"{BASE}/guide", wait_until="networkidle")
        shot(page, "07_user_guide")

        print("8. Technical docs")
        page.goto(f"{BASE}/tech-docs", wait_until="networkidle")
        shot(page, "08_tech_docs")

        browser.close()
        print(f"\nDone. Screenshots in {OUT}")


if __name__ == "__main__":
    main()
