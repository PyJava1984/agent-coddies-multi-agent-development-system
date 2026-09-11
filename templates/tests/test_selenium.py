"""Starter Selenium suite for agent-coddies.

Reads the base URL and the test account from the environment exported by
scripts/run_tests.py (from credentials.yaml -> test_environments):

    CODDIE_BASE_URL, CODDIE_USERNAME, CODDIE_PASSWORD
    CODDIE_SELENIUM_BROWSER, CODDIE_SELENIUM_HEADLESS, CODDIE_SELENIUM_REMOTE_URL

Run it with:

    python scripts/run_tests.py selenium --spec tests/selenium --browser chrome

Requires: pip install selenium pytest
Selenium 4.6+ resolves drivers itself via Selenium Manager - no webdriver-manager.
"""

from __future__ import annotations

import os
import time

import pytest
from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

BASE_URL = os.environ.get("CODDIE_BASE_URL", "http://localhost:3000").rstrip("/")
USERNAME = os.environ.get("CODDIE_USERNAME", "")
PASSWORD = os.environ.get("CODDIE_PASSWORD", "")
BROWSER = os.environ.get("CODDIE_SELENIUM_BROWSER", "chrome").lower()
HEADLESS = os.environ.get("CODDIE_SELENIUM_HEADLESS", "true").lower() == "true"
REMOTE_URL = os.environ.get("CODDIE_SELENIUM_REMOTE_URL", "")
TIMEOUT = 20


def _options():
    if BROWSER in ("chrome", "chromium"):
        opts = webdriver.ChromeOptions()
        if HEADLESS:
            opts.add_argument("--headless=new")
        opts.add_argument("--window-size=1440,900")
        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-dev-shm-usage")
        return opts
    if BROWSER == "firefox":
        opts = webdriver.FirefoxOptions()
        if HEADLESS:
            opts.add_argument("-headless")
        return opts
    if BROWSER == "edge":
        opts = webdriver.EdgeOptions()
        if HEADLESS:
            opts.add_argument("--headless=new")
        return opts
    raise ValueError(f"unsupported browser: {BROWSER}")


@pytest.fixture(scope="function")
def driver():
    if not USERNAME or not PASSWORD:
        pytest.skip("CODDIE_USERNAME / CODDIE_PASSWORD not set - run via scripts/run_tests.py")
    opts = _options()
    if REMOTE_URL:
        drv = webdriver.Remote(command_executor=REMOTE_URL, options=opts)
    elif BROWSER in ("chrome", "chromium"):
        drv = webdriver.Chrome(options=opts)
    elif BROWSER == "firefox":
        drv = webdriver.Firefox(options=opts)
    else:
        drv = webdriver.Edge(options=opts)
    drv.set_page_load_timeout(int(os.environ.get("CODDIE_PAGE_LOAD_TIMEOUT", "30")))
    # No implicit wait: it fights explicit waits and hides real timing bugs.
    drv.implicitly_wait(0)
    yield drv
    drv.quit()


def wait(driver, condition, message: str = ""):
    try:
        return WebDriverWait(driver, TIMEOUT).until(condition)
    except TimeoutException as exc:
        raise AssertionError(
            f"timed out after {TIMEOUT}s waiting for {message or condition}\n"
            f"  url: {driver.current_url}"
        ) from exc


def login(driver) -> None:
    driver.get(f"{BASE_URL}/login")
    wait(driver, EC.presence_of_element_located((By.NAME, "username")), "login form")
    driver.find_element(By.NAME, "username").send_keys(USERNAME)
    driver.find_element(By.NAME, "password").send_keys(PASSWORD)
    driver.find_element(By.CSS_SELECTOR, "[type=submit]").click()
    wait(driver, EC.presence_of_element_located((By.CSS_SELECTOR, "nav")), "post-login nav")


def screenshot(driver, name: str) -> str:
    out = os.path.join(os.environ.get("CODDIE_ARTIFACTS", "."), f"selenium-{name}-{int(time.time())}.png")
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    driver.save_screenshot(out)
    return out


class TestFeature:
    """HM-XXXX - <what this suite proves>."""

    def test_ac1_happy_path(self, driver):
        login(driver)
        driver.get(f"{BASE_URL}/your/feature")

        wait(driver, EC.element_to_be_clickable((By.CSS_SELECTOR, "[data-testid=create]")), "create button").click()
        driver.find_element(By.NAME, "name").send_keys(f"coddie-{int(time.time())}")
        driver.find_element(By.CSS_SELECTOR, "[data-testid=save]").click()

        banner = wait(driver, EC.visibility_of_element_located((By.CSS_SELECTOR, "[role=alert]")), "save confirmation")
        assert "saved" in banner.text.lower(), f"unexpected banner: {banner.text!r} ({screenshot(driver, 'ac1')})"

    def test_ac2_rejects_empty_required_field(self, driver):
        login(driver)
        driver.get(f"{BASE_URL}/your/feature/new")
        driver.find_element(By.CSS_SELECTOR, "[data-testid=save]").click()

        error = wait(driver, EC.visibility_of_element_located((By.CSS_SELECTOR, ".field-error")), "validation error")
        assert error.is_displayed()
        assert driver.current_url.endswith("/new"), "form should not have submitted"

    def test_ac3_unauthorised_user_is_blocked(self, driver):
        driver.get(f"{BASE_URL}/your/feature/admin-only")
        wait(driver, EC.presence_of_element_located((By.TAG_NAME, "body")), "page body")
        body = driver.find_element(By.TAG_NAME, "body").text.lower()
        assert any(word in body for word in ("forbidden", "not authorised", "not authorized", "sign in")), (
            f"expected an authorisation block, got: {body[:200]!r}"
        )
