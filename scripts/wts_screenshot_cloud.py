"""Screenshots der ONLINE-Version auf Streamlit Cloud."""
from playwright.sync_api import sync_playwright

URL = "https://wtstrading-datenblatt.streamlit.app"
PASSWORD = "wts-2026"

with sync_playwright() as p:
    browser = p.chromium.launch()
    context = browser.new_context(viewport={"width": 1500, "height": 1100})
    page = context.new_page()

    page.goto(URL, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(3000)

    # Warte bis Login-Form sichtbar (5min, falls Cloud noch baut)
    page.locator('input[placeholder="Passwort"]').wait_for(timeout=300000)
    print("login form ready")
    page.fill('input[placeholder="Passwort"]', PASSWORD)
    page.get_by_role("button", name="Anmelden").click()
    page.wait_for_timeout(5000)
    page.screenshot(path="/tmp/wts_cloud_dash.png", full_page=True)
    print("dashboard → /tmp/wts_cloud_dash.png")

    for nav in ["Lieferungen", "Lager", "Artikel", "Parteien"]:
        try:
            page.get_by_role("link", name=nav).first.click()
            page.wait_for_timeout(5000)
            fname = f"/tmp/wts_cloud_{nav.lower()}.png"
            page.screenshot(path=fname, full_page=True)
            print(f"{nav} → {fname}")
        except Exception as e:
            print(f"could not load '{nav}': {e}")

    browser.close()
