import os
import time
import calendar
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright
from playwright.sync_api import Error as PlaywrightError


# =============================================================================
# Configuration
# =============================================================================

@dataclass(frozen=True)
class Config:
    base_url: str
    username: str
    password: str
    download_dir: Path


def load_config() -> Config:
    load_dotenv()

    required = ["PFP_BASE_URL", "PFP_USERNAME", "PFP_PASSWORD", "PFP_DOWNLOAD_DIR"]
    missing = [k for k in required if not os.getenv(k)]
    if missing:
        raise SystemExit(f"Missing env vars: {missing}")

    return Config(
        base_url=os.getenv("PFP_BASE_URL", "").rstrip("/"),
        username=os.getenv("PFP_USERNAME", ""),
        password=os.getenv("PFP_PASSWORD", ""),
        download_dir=Path(os.getenv("PFP_DOWNLOAD_DIR", "./downloads")),
    )


# =============================================================================
# Small helpers (env parsing, navigation, month calculation)
# =============================================================================

def env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def env_int(name: str, default: int, min_value: int | None = None, max_value: int | None = None) -> int:
    raw = os.getenv(name)
    if not raw:
        val = default
    else:
        try:
            val = int(raw.strip())
        except ValueError:
            val = default

    if min_value is not None:
        val = max(min_value, val)
    if max_value is not None:
        val = min(max_value, val)
    return val


def safe_goto(page, url: str, attempts: int = 5) -> None:
    """
    Robust navigation helper:
    retries common Playwright navigation interruption errors.
    """
    for _ in range(attempts):
        try:
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_load_state("networkidle")
            return
        except PlaywrightError as e:
            msg = str(e)
            if "is interrupted by another navigation" in msg or "net::ERR_ABORTED" in msg:
                page.wait_for_timeout(1000)
                continue
            raise
    raise RuntimeError(f"Failed to navigate to {url} after {attempts} attempts")


def last_n_months(n: int, *, include_current: bool = True) -> list[tuple[int, int]]:
    """
    Returns [(year, month), ...] for the last n months.
    include_current=True  -> current month + previous (n-1)
    include_current=False -> previous n completed months
    """
    now = datetime.now()
    y = now.year
    m = now.month

    if not include_current:
        m -= 1
        if m == 0:
            m = 12
            y -= 1

    out: list[tuple[int, int]] = []
    for _ in range(n):
        out.append((y, m))
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    return out


# =============================================================================
# Exporte page helper
# =============================================================================

def apply_exports_filters(page, year: int, month: int, *, inhalt: str | None = None) -> None:
    """
    Sets filters on Exporte page and clicks the magnifier search button.
    REQUIRED because there is NO 'Alle' month option on Exporte.
    """

    # --- Optional: set Inhalt dropdown to reduce results ---
    # CHANGE: try to set a 3rd select (Inhalt) if present and if it has the label you want
    if inhalt:
        try:
            # pick any <select> that is NOT month/year and try selecting by label
            all_selects = page.locator("select.form-control")
            for i in range(all_selects.count()):
                sel = all_selects.nth(i)
                sel_id = sel.get_attribute("id") or ""
                if sel_id in {"month", "year"}:
                    continue
                # try selecting "Bestellungen" by visible label
                sel.select_option(label=inhalt)
                break
        except PlaywrightError:
            pass  # ignore if not found / not selectable

    # --- Month/Year are mandatory on Exporte page ---
    # CHANGE: wait + set month/year (no 'Alle' month exists)
    page.locator("#month").wait_for(state="visible", timeout=15000)
    page.locator("#year").wait_for(state="visible", timeout=15000)

    page.select_option("#month", str(month))
    page.select_option("#year", str(year))

    # Click magnifier search button
    search_btn = page.locator("button:has(i.fa-search), button:has(.fa-search)").first
    search_btn.wait_for(state="visible", timeout=15000)

    # CHANGE: some sites reload on search; handle both cases
    try:
        with page.expect_navigation(wait_until="domcontentloaded", timeout=10000):
            search_btn.click()
    except PlaywrightError:
        search_btn.click()

    page.wait_for_load_state("networkidle")


# =============================================================================
# Kunden export (kept as-is in behavior)
# =============================================================================

def trigger_kunden_export(page, cfg: Config) -> None:
    kunden_url = f"{cfg.base_url}/carebox/supplier"
    print("Opening Kunden page:", kunden_url)
    safe_goto(page, kunden_url)

    cookie_btn = page.locator("button:has-text('Alle Cookies akzeptieren')").first
    if cookie_btn.is_visible():
        cookie_btn.click()
        page.wait_for_timeout(300)

    export_btn = page.locator("button.btn-success:has-text('Export')").first
    export_btn.wait_for(state="visible", timeout=15000)
    export_btn.click()

    kunden_item = page.locator("a.dropdown-item:has-text('Kunden'), button.dropdown-item:has-text('Kunden')").first
    kunden_item.wait_for(state="visible", timeout=15000)
    kunden_item.click()

    print("Triggered Kunden export from Kunden page.")
    # time.sleep(1 * 60)  # keep your existing wait strategy


def wait_and_download_latest_kunden_export(
    page,
    cfg: Config,
    timeout_s: int = 600,
    *,
    run_dir: Path,     
    run_date: str,    
) -> None:
    """
    Waits until the newest 'Kunden' export is Abgeschlossen, then downloads it.
    """
    exports_url = f"{cfg.base_url}/carebox/exports"
    deadline = time.time() + timeout_s

    while time.time() < deadline:
        safe_goto(page, exports_url)

        rows = page.query_selector_all("table tbody tr")
        if not rows:
            print("No export rows yet... waiting 5s")
            time.sleep(5)
            continue

        tr = rows[0]
        tds = tr.query_selector_all("td")
        if len(tds) < 7:
            print("Unexpected row format... waiting 5s")
            time.sleep(5)
            continue

        zeitraum = tds[0].inner_text().strip()
        inhalt = tds[1].inner_text().strip()
        status = tds[4].inner_text().strip()
        print(f"Latest export: {zeitraum} | {inhalt} | {status}")

        if inhalt == "Kunden" and "Abgeschlossen" in status:
            print("Export finished. Starting download...")

            download_a = tds[6].query_selector('a[title="Download"]')
            if not download_a:
                print("No download link yet... waiting 5s")
                time.sleep(5)
                continue

            with page.expect_download() as download_info:
                download_a.click()

            download = download_info.value
            #target_path = cfg.download_dir / download.suggested_filename  
            target_path = run_dir / f"{run_date}_Kunden_{download.suggested_filename}"
            download.save_as(target_path)
            print("Saved to:", target_path.resolve())
            return

        print("Still processing... waiting 5s")
        time.sleep(5)

    raise TimeoutError("Kunden export did not finish within timeout.")


# =============================================================================
# Bestellungen export + download
# =============================================================================

def trigger_bestellungen_export_for_month(page, cfg: Config, year: int, month: int) -> None:
    orders_url = f"{cfg.base_url}/carebox/supplier/orders"
    print(f"\nOpening Bestellungen page for {year}-{month:02d}")
    safe_goto(page, orders_url)

    # Set filters on Orders page
    page.select_option("#month", str(month))
    page.select_option("#year", str(year))

    # Apply filters
    search_btn = page.locator("button:has-text('Suche')").first
    if search_btn.is_visible():
        print("Clicking Suche to apply filters...")
        with page.expect_navigation(wait_until="domcontentloaded"):
            search_btn.click()
        page.wait_for_load_state("networkidle")

    # Open Export dropdown
    print("Clicking Export dropdown...")
    export_btn = page.locator("button.btn-success:has-text('Export')").first
    export_btn.wait_for(state="visible", timeout=15000)
    export_btn.click()

    # Submit Bestellungen export (this submits a form -> navigation)
    print("Choosing 'Bestellungen' from dropdown...")
    bestellungen_submit = page.locator(
        ".dropdown-menu.show button.dropdown-item[name='exportButton']",
        has_text="Bestellungen",
    ).first
    bestellungen_submit.wait_for(state="visible", timeout=15000)

    with page.expect_navigation(wait_until="domcontentloaded"):
        bestellungen_submit.click()
    page.wait_for_load_state("networkidle")

    print(f"Triggered Bestellungen export for {year}-{month:02d}")


def wait_and_download_bestellungen_export(
    page,
    cfg: Config,
    year: int,
    month: int,
    *,
    poll_seconds: int = 30,
    run_dir: Path,         
    run_date: str,          
) -> None:
    exports_url = f"{cfg.base_url}/carebox/exports"
    last_day = calendar.monthrange(year, month)[1]

    # Accept both formats (some pages show leading zeros, some do not)
    expected_a = f"01.{month:02d}.{year} - {last_day:02d}.{month:02d}.{year}"
    expected_b = f"1.{month}.{year} - {last_day}.{month}.{year}"

    print(f"Waiting for export row: {expected_a} (or {expected_b})")

    while True:
        safe_goto(page, exports_url)

        # IMPORTANT: set Exporte page filters to make that year/month visible
        apply_exports_filters(page, year, month, inhalt="Bestellungen")

        rows = page.query_selector_all("table tbody tr")
        for tr in rows:
            tds = tr.query_selector_all("td")
            if len(tds) < 7:
                continue

            zeitraum = tds[0].inner_text().strip()
            inhalt = tds[1].inner_text().strip()
            status = tds[4].inner_text().strip()

            if inhalt == "Bestellungen" and (zeitraum == expected_a or zeitraum == expected_b):
                print(f"Found row: {zeitraum} | {status}")

                if "Abgeschlossen" not in status:
                    print(f"Still processing... waiting {poll_seconds}s")
                    time.sleep(poll_seconds)
                    break  # break for-loop; continue while-loop

                download_a = tds[6].query_selector('a[title="Download"]')
                if not download_a:
                    print(f"No download link yet... waiting {poll_seconds}s")
                    time.sleep(poll_seconds)
                    break

                with page.expect_download() as d:
                    download_a.click()

                download = d.value
                # target_path = cfg.download_dir / f"Bestellungen_{year}-{month:02d}.csv"
                target_path = run_dir / f"{run_date}_Bestellungen_{year}-{month:02d}.csv"
                download.save_as(target_path)
                print("Saved:", target_path.resolve())
                return

        print(f"Row not ready yet. Waiting {poll_seconds}s...")
        time.sleep(poll_seconds)


# =============================================================================
# Login
# =============================================================================

def login(page, cfg: Config) -> None:
    login_url = f"{cfg.base_url}/login"
    safe_goto(page, login_url)

    page.fill("input[name='username']", cfg.username)
    page.fill("input[name='password']", cfg.password)

    print("Submitting login form...")
    page.click('button[type="submit"]:has-text("Anmelden")')
    page.wait_for_load_state("networkidle")

    if "/account/login" in page.url:
        raise RuntimeError("Login failed or still on login page.")


# =============================================================================
# Main
# =============================================================================

def main() -> None:
    cfg = load_config()
    cfg.download_dir.mkdir(parents=True, exist_ok=True)
    
    run_root = cfg.download_dir
    run_date = datetime.now().strftime("%Y-%m-%d")
    run_dir = run_root / run_date
    run_dir.mkdir(parents=True, exist_ok=True)

    # Use cfg_run_dir for saving
    cfg = Config(
        base_url=cfg.base_url,
        username=cfg.username,
        password=cfg.password,
        download_dir=run_dir,
    )

    print("Run folder:", run_dir.resolve())

    # Controls (env)
    test_bestellungen_only = env_bool("TEST_BESTELLUNGEN_ONLY", default=False)  # set to 1/true to skip Kunden
    months_to_download = env_int("LAST_MONTHS", default=2, min_value=1, max_value=12)  # default last 2 months
    include_current = env_bool("INCLUDE_CURRENT", default=True)  # you requested include_current=True
    bestellungen_trigger_wait_s = env_int("BESTELLUNGEN_TRIGGER_WAIT_SECONDS", default= 1 * 60, min_value=0, max_value=24 * 60 * 60)
    poll_seconds = env_int("EXPORTS_POLL_SECONDS", default=30, min_value=5, max_value=600)

    print("Starting browser...")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        # create context with downloads_path + accept_downloads
        context = browser.new_context(
            accept_downloads=True,
        )
        page = context.new_page()

        # 1) Login
        login(page, cfg)
        print("LOGIN OK (not on login page).")

        # 2) Kunden 
        if not test_bestellungen_only:
            trigger_kunden_export(page, cfg)
            # wait_and_download_latest_kunden_export(page, cfg, timeout_s=600)
            wait_and_download_latest_kunden_export(page, cfg, timeout_s=2400, run_dir=run_dir, run_date=run_date)
        else:
            print("TEST_BESTELLUNGEN_ONLY enabled -> skipping Kunden export/download")

        # 3) Bestellungen (last N months, across years)
        for (y, m) in last_n_months(months_to_download, include_current=include_current):
            trigger_bestellungen_export_for_month(page, cfg, y, m)

            if bestellungen_trigger_wait_s > 0:
                print(f"Waiting {bestellungen_trigger_wait_s}s before checking Exporte...")
                time.sleep(bestellungen_trigger_wait_s)

            # wait_and_download_bestellungen_export(page, cfg, y, m, poll_seconds=poll_seconds
            wait_and_download_bestellungen_export(page, cfg, y, m, poll_seconds=poll_seconds, run_dir=run_dir, run_date=run_date)

        context.close()
        browser.close() 
        # SUCCESS FLAG: write into RUN ROOT (runs/<run_id>)
    (run_root / "done.flag").write_text("ok", encoding="utf-8")
    

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        # FAIL FLAG: write into RUN ROOT (runs/<run_id>)
        run_root = Path(os.getenv("PFP_DOWNLOAD_DIR", "./downloads"))
        run_root.mkdir(parents=True, exist_ok=True)
        (run_root / "fail.flag").write_text(str(e), encoding="utf-8")
        raise