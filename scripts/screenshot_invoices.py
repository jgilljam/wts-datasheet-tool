"""Screenshots: Rechnungen-Page (alle 3 Tabs) + neuer Login-Titel."""
from playwright.sync_api import sync_playwright

URL = "http://localhost:8501"
PASSWORD = "wts-2026"

with sync_playwright() as p:
    browser = p.chromium.launch()
    context = browser.new_context(viewport={"width": 1500, "height": 1600})
    page = context.new_page()

    page.goto(URL, wait_until="networkidle")
    page.wait_for_timeout(2000)
    # Login-Titel screenshotten
    page.screenshot(path="/tmp/wts_login.png", full_page=False)
    page.get_by_placeholder("Passwort").fill(PASSWORD)
    page.get_by_role("button", name="Anmelden").click()
    page.wait_for_timeout(2500)

    try:
        page.get_by_role("link", name="Rechnungen").first.click()
    except Exception:
        page.goto(f"{URL}/rechnungen", wait_until="networkidle")
    page.wait_for_timeout(3000)

    for tab_name, fname in [
        ("Liste", "/tmp/wts_invoices_list.png"),
        ("Detail", "/tmp/wts_invoices_detail.png"),
    ]:
        try:
            page.get_by_role("tab", name=tab_name).click()
            page.wait_for_timeout(3000)
            page.screenshot(path=fname, full_page=True)
            print(f"Rechnungen/{tab_name} → {fname}")
        except Exception as e:
            print(f"could not click '{tab_name}': {e}")

    browser.close()
