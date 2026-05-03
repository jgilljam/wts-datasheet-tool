"""Screenshots: Aufträge-Page (alle 3 Tabs)."""
from playwright.sync_api import sync_playwright

URL = "http://localhost:8501"
PASSWORD = "wts-2026"

with sync_playwright() as p:
    browser = p.chromium.launch()
    context = browser.new_context(viewport={"width": 1500, "height": 1400})
    page = context.new_page()

    page.goto(URL, wait_until="networkidle")
    page.wait_for_timeout(2000)
    page.get_by_placeholder("Passwort").fill(PASSWORD)
    page.get_by_role("button", name="Anmelden").click()
    page.wait_for_timeout(2500)

    try:
        page.get_by_role("link", name="Aufträge").first.click()
    except Exception:
        page.goto(f"{URL}/auftraege", wait_until="networkidle")
    page.wait_for_timeout(3000)

    for tab_name, fname in [
        ("Liste", "/tmp/wts_orders_list.png"),
        ("Neu anlegen", "/tmp/wts_orders_new.png"),
        ("Detail", "/tmp/wts_orders_detail.png"),
    ]:
        try:
            page.get_by_role("tab", name=tab_name).click()
            page.wait_for_timeout(3000)
            page.screenshot(path=fname, full_page=True)
            print(f"Aufträge/{tab_name} → {fname}")
        except Exception as e:
            print(f"could not click '{tab_name}': {e}")

    browser.close()
