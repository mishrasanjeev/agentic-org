"""Generic portal automation — login to any website and perform actions.

This script handles the common pattern of:
  1. Navigate to a portal URL
  2. Fill in login credentials
  3. Click the login button
  4. Wait for post-login page
  5. Navigate to a target page (optional)
  6. Extract data or take screenshots
  7. Download files if specified

Users configure this via params — no code changes needed per portal.

Required params:
  portal_url     — The login page URL
  username       — Login username/email
  password       — Login password
  username_field  — CSS selector for username input (default: auto-detect)
  password_field  — CSS selector for password input (default: auto-detect)
  login_button    — CSS selector for login/submit button (default: auto-detect)

Optional params:
  target_url      — URL to navigate after login
  wait_for        — CSS selector to wait for after login (confirms success)
  extract_selector — CSS selector to extract text content from
  download_link    — CSS selector for a download link to click
  action           — "screenshot" | "extract" | "download" (default: screenshot)
"""

from __future__ import annotations

from typing import Any

SCRIPT_META = {
    "name": "Generic Portal Automator",
    "description": (
        "Automate any web portal that doesn't have APIs. Provide the "
        "login URL, credentials, and what to do after login. Supports "
        "auto-detection of login forms, data extraction, file "
        "downloads, and screenshots."
    ),
    "category": "general",
    "params_schema": {
        "portal_url": {"type": "string", "label": "Portal Login URL", "required": True},
        "username": {"type": "string", "label": "Username / Email", "required": True},
        "password": {"type": "password", "label": "Password", "required": True},
        "username_field": {"type": "string", "label": "Username field selector", "required": False},
        "password_field": {"type": "string", "label": "Password field selector", "required": False},
        "login_button": {"type": "string", "label": "Login button selector", "required": False},
        "target_url": {"type": "string", "label": "URL to navigate after login", "required": False},
        "action": {"type": "string", "label": "Action: screenshot / extract / download", "required": False},
        "extract_selector": {"type": "string", "label": "CSS selector to extract", "required": False},
        "download_link": {"type": "string", "label": "CSS selector for download link", "required": False},
    },
    "estimated_duration_s": 60,
    "admin_only": True,  # HIGH-09: SSRF-capable
}


# Common login form selectors — tried in order until one matches
_USERNAME_SELECTORS = [
    'input[type="email"]',
    'input[name="username"]',
    'input[name="email"]',
    'input[name="user"]',
    'input[name="login"]',
    'input[name="userid"]',
    'input[id="username"]',
    'input[id="email"]',
    'input[id="login"]',
    '#txtUserName',
    '#user_id',
    'input[type="text"]:first-of-type',
]

_PASSWORD_SELECTORS = [
    'input[type="password"]',
    'input[name="password"]',
    'input[name="pass"]',
    '#txtPassword',
    '#password',
]

_LOGIN_BUTTON_SELECTORS = [
    'button[type="submit"]',
    'input[type="submit"]',
    'button:has-text("Log in")',
    'button:has-text("Login")',
    'button:has-text("Sign in")',
    'button:has-text("Submit")',
    '#btnLogin',
    '#loginBtn',
    '.login-button',
    '.btn-login',
]


async def _find_element(page: Any, selectors: list[str], custom: str = "") -> str | None:
    """Try each selector until one matches on the page."""
    if custom:
        try:
            el = page.locator(custom)
            if await el.count() > 0:
                return custom
        except Exception:  # noqa: S110, BLE001
            pass  # selector may not exist — try next

    for sel in selectors:
        try:
            el = page.locator(sel)
            if await el.count() > 0:
                return sel
        except Exception:  # noqa: S110, S112, BLE001
            continue  # selector may not exist — try next
    return None


async def run(page: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Execute generic portal automation.

    Returns:
        {
            "logged_in": bool,
            "page_title": str,
            "current_url": str,
            "extracted_text": str | None,
            "downloaded": bool,
            "error": str | None,
        }
    """
    portal_url = params.get("portal_url", "")
    username = params.get("username", "")
    password = params.get("password", "")
    username_field = params.get("username_field", "")
    password_field = params.get("password_field", "")
    login_button = params.get("login_button", "")
    target_url = params.get("target_url", "")
    wait_for = params.get("wait_for", "")
    extract_selector = params.get("extract_selector", "")
    download_link = params.get("download_link", "")
    action = params.get("action", "screenshot")

    if not portal_url:
        return {"logged_in": False, "error": "portal_url is required"}
    if not username or not password:
        return {"logged_in": False, "error": "username and password are required"}

    # SEC-2026-05-P1-006 (PR-E): validate every caller-supplied URL
    # against the egress guard BEFORE the browser ever navigates. The
    # guard rejects non-http(s) schemes, IP-literal URLs, and any
    # hostname whose DNS resolution lands on a private / loopback /
    # link-local / metadata range. ``apply_playwright_route_guard``
    # also re-validates every request mid-navigation so DNS
    # rebinding can't slip a metadata fetch past the initial check.
    from rpa.egress_guard import (  # noqa: PLC0415 — runtime-only import for the RPA path
        EgressBlocked,
        apply_playwright_route_guard,
        validate_egress_url,
    )

    try:
        await validate_egress_url(portal_url)
    except EgressBlocked as exc:
        return {
            "logged_in": False,
            "error": f"egress blocked for portal_url: {exc}",
            "error_class": "egress_blocked",
            "egress_reason": exc.reason,
        }
    if target_url:
        try:
            await validate_egress_url(target_url)
        except EgressBlocked as exc:
            return {
                "logged_in": False,
                "error": f"egress blocked for target_url: {exc}",
                "error_class": "egress_blocked",
                "egress_reason": exc.reason,
            }

    # Mid-navigation route-level re-validation. Defends against DNS
    # rebinding (host resolves to a public IP for the initial check
    # but to ``169.254.169.254`` once the page is parked).
    await apply_playwright_route_guard(page)

    result: dict[str, Any] = {
        "logged_in": False,
        "page_title": "",
        "current_url": "",
        "extracted_text": None,
        "downloaded": False,
        "error": None,
    }

    # Step 1: Navigate to portal
    await page.goto(portal_url, wait_until="networkidle", timeout=30000)
    await page.wait_for_timeout(2000)  # let JS render

    # Step 2: Find and fill username
    user_sel = await _find_element(page, _USERNAME_SELECTORS, username_field)
    if not user_sel:
        result["error"] = "Could not find username input field. Try specifying username_field selector."
        return result

    await page.locator(user_sel).fill(username)
    await page.wait_for_timeout(500)

    # Step 3: Find and fill password
    pass_sel = await _find_element(page, _PASSWORD_SELECTORS, password_field)
    if not pass_sel:
        result["error"] = "Could not find password input field. Try specifying password_field selector."
        return result

    await page.locator(pass_sel).fill(password)
    await page.wait_for_timeout(500)

    # Step 4: Click login button
    btn_sel = await _find_element(page, _LOGIN_BUTTON_SELECTORS, login_button)
    if not btn_sel:
        # Try pressing Enter as fallback
        await page.locator(pass_sel).press("Enter")
    else:
        await page.locator(btn_sel).click()

    # Step 5: Wait for login to complete
    await page.wait_for_timeout(3000)

    if wait_for:
        try:
            await page.wait_for_selector(wait_for, timeout=15000)
        except Exception:  # noqa: BLE001
            result["error"] = f"Login may have failed — wait_for selector '{wait_for}' not found after login"
            result["page_title"] = await page.title()
            result["current_url"] = page.url
            return result

    # Check if we're still on the login page (login failed)
    current_url = page.url
    if current_url == portal_url and not wait_for:
        # Try to detect error messages
        for err_sel in [".error", ".alert-danger", "#error", ".login-error", '[role="alert"]']:
            try:
                el = page.locator(err_sel)
                if await el.count() > 0:
                    err_text = await el.first.text_content()
                    result["error"] = f"Login failed: {err_text}"
                    return result
            except Exception:  # noqa: S112, BLE001
                continue  # error selector not found — try next

    result["logged_in"] = True
    result["page_title"] = await page.title()
    result["current_url"] = page.url

    # Step 6: Navigate to target page if specified
    if target_url:
        await page.goto(target_url, wait_until="networkidle", timeout=30000)
        await page.wait_for_timeout(2000)
        result["current_url"] = page.url
        result["page_title"] = await page.title()

    # Step 7: Perform the requested action
    if action == "extract" and extract_selector:
        try:
            el = page.locator(extract_selector)
            if await el.count() > 0:
                result["extracted_text"] = await el.first.text_content()
            else:
                result["error"] = f"Extract selector '{extract_selector}' not found"
        except Exception as exc:
            result["error"] = f"Extract failed: {exc}"

    elif action == "download" and download_link:
        try:
            async with page.expect_download(timeout=30000) as download_info:
                await page.locator(download_link).click()
            download = await download_info.value
            result["downloaded"] = True
            result["downloaded_filename"] = download.suggested_filename
        except Exception as exc:
            result["error"] = f"Download failed: {exc}"

    # action == "screenshot" is handled by the executor's auto-screenshot

    return result
