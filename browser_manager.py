"""
Browser selection and authentication management module.
Supports interactive browser selection (Chrome, Edge, Firefox, Chromium),
manual login session capture, and persistent storage state saving/loading.
"""

import os
import sys
import json
import logging
from pathlib import Path
from typing import Optional, Dict, Any, Tuple
from playwright.async_api import async_playwright, Browser, BrowserContext, Page, Playwright

logger = logging.getLogger("google_forms_bot")

DEFAULT_AUTH_FILE = "auth_state.json"
DEFAULT_PROFILE_DIR = ".browser_profile"


class BrowserManager:
    """
    Manages browser selection, launching with appropriate channel or executable,
    and handling Google Account login persistence.
    """

    AVAILABLE_BROWSERS = {
        "1": {"id": "chrome", "name": "Google Chrome (Installed)", "channel": "chrome", "type": "chromium"},
        "2": {"id": "msedge", "name": "Microsoft Edge (Installed)", "channel": "msedge", "type": "chromium"},
        "3": {"id": "firefox", "name": "Mozilla Firefox", "channel": None, "type": "firefox"},
        "4": {"id": "chromium", "name": "Playwright Chromium (Isolated)", "channel": None, "type": "chromium"},
    }

    def __init__(
        self,
        browser_choice: Optional[str] = None,
        auth_file: str = DEFAULT_AUTH_FILE,
        user_data_dir: Optional[str] = None,
        interactive: bool = True
    ):
        self.browser_choice = browser_choice
        self.auth_file = auth_file
        self.user_data_dir = user_data_dir or DEFAULT_PROFILE_DIR
        self.interactive = interactive

    def prompt_browser_selection(self) -> str:
        """
        Prompt user interactively in the terminal to choose which browser to open.
        """
        print("\n" + "=" * 55)
        print(" GOOGLE FORMS BOT - BROWSER SELECTION ")
        print("=" * 55)
        print("Please choose which browser you want to open:")
        for key, info in self.AVAILABLE_BROWSERS.items():
            print(f"  [{key}] {info['name']}")
        print("=" * 55)

        try:
            choice = input("Enter choice [1-4] (default: 1 - Chrome): ").strip()
        except (EOFError, KeyboardInterrupt):
            choice = "1"

        if choice not in self.AVAILABLE_BROWSERS:
            # Check if user typed the name directly (e.g. 'chrome', 'firefox', 'edge')
            for k, info in self.AVAILABLE_BROWSERS.items():
                if info["id"] == choice.lower() or info["id"].startswith(choice.lower()):
                    return info["id"]
            choice = "1"

        selected = self.AVAILABLE_BROWSERS[choice]["id"]
        print(f"Selected: {self.AVAILABLE_BROWSERS[choice]['name']}\n")
        return selected

    def resolve_browser_config(self, choice_str: Optional[str]) -> Dict[str, Any]:
        """Resolve browser ID string into launch config."""
        if not choice_str:
            if self.interactive and sys.stdin.isatty():
                choice_str = self.prompt_browser_selection()
            else:
                choice_str = os.environ.get("BROWSER_TYPE", "chrome")

        choice_lower = choice_str.lower()
        for info in self.AVAILABLE_BROWSERS.values():
            if info["id"] == choice_lower or info["type"] == choice_lower:
                return info

        return self.AVAILABLE_BROWSERS["1"]

    async def launch_browser(
        self,
        pw: Playwright,
        headless: bool = False,
        browser_type_name: Optional[str] = None,
        use_auth: bool = True,
        use_system_profile: bool = False,
        cdp_url: Optional[str] = None
    ) -> Tuple[BrowserContext, Optional[Browser]]:
        """
        Launch the selected browser using a persistent user profile context.
        This preserves all Google logins, cookies, extensions, and session tokens across runs.
        """
        # If connecting via Chrome DevTools Protocol to an already open Chrome window
        if cdp_url:
            logger.info("Connecting to existing browser instance via CDP at %s...", cdp_url)
            browser = await pw.chromium.connect_over_cdp(cdp_url)
            context = browser.contexts[0] if browser.contexts else await browser.new_context()
            return context, browser

        cfg = self.resolve_browser_config(browser_type_name or self.browser_choice)
        b_type = cfg["type"]
        channel = cfg["channel"]

        # Determine user data directory
        profile_path: str
        if use_system_profile:
            if channel == "chrome":
                profile_path = os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\User Data")
            elif channel == "msedge":
                profile_path = os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Edge\User Data")
            else:
                profile_path = os.path.abspath(self.user_data_dir)
        else:
            profile_path = os.path.abspath(self.user_data_dir)

        Path(profile_path).mkdir(parents=True, exist_ok=True)
        logger.info("[Persistent Profile] Using profile directory: '%s'", profile_path)
        logger.info("[Browser Launch] Starting '%s' (type=%s, channel=%s, headless=%s)...",
                    cfg["name"], b_type, channel, headless)

        browser_engine = getattr(pw, b_type)
        launch_args = [
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-blink-features=AutomationControlled",
            "--no-first-run",
            "--no-default-browser-check",
            "--start-maximized" if not headless else "--window-size=1280,900"
        ]

        launch_kwargs: Dict[str, Any] = {
            "user_data_dir": profile_path,
            "headless": bool(headless),
            "args": launch_args,
            "viewport": None if not headless else {"width": 1280, "height": 900},
            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        }
        if channel:
            launch_kwargs["channel"] = channel

        try:
            context = await browser_engine.launch_persistent_context(**launch_kwargs)
            return context, None
        except Exception as e:
            err_msg = str(e)
            if "SingletonLock" in err_msg or "Process already running" in err_msg or "Target closed" in err_msg:
                logger.warning(
                    "[Profile Lock Detected] System browser is currently running. "
                    "Falling back to dedicated persistent automation profile at '%s'...",
                    DEFAULT_PROFILE_DIR
                )
                fallback_path = os.path.abspath(DEFAULT_PROFILE_DIR)
                Path(fallback_path).mkdir(parents=True, exist_ok=True)
                launch_kwargs["user_data_dir"] = fallback_path
                context = await browser_engine.launch_persistent_context(**launch_kwargs)
                return context, None
            raise

    async def interactive_login(self, target_url: Optional[str] = None, browser_name: Optional[str] = None) -> bool:
        """
        Open a visible browser window with persistent profile to log in to your Google Account.
        All logins, cookies, and tokens are saved permanently to the persistent profile.
        """
        login_url = target_url or "https://accounts.google.com/ServiceLogin"

        print("\n" + "=" * 65)
        print(" GOOGLE ACCOUNT LOGIN - PERSISTENT PROFILE SETUP ")
        print("=" * 65)
        print("Your installed browser will now open.")
        print("1. Log in to your Google Account (or Institutional / Organization SSO).")
        print("2. Once logged in, your session is saved permanently to this profile.")
        print("3. Return to this terminal and press [ENTER] to finish.")
        print("=" * 65 + "\n")

        async with async_playwright() as pw:
            context, browser = await self.launch_browser(
                pw=pw,
                headless=False,
                browser_type_name=browser_name,
                use_auth=True
            )

            page = context.pages[0] if context.pages else await context.new_page()
            logger.info("Opening login page: %s", login_url)
            await page.goto(login_url)

            try:
                input("\n>>> Complete your login in the browser window, then press ENTER: ")
            except (EOFError, KeyboardInterrupt):
                pass

            # Also export auth_state.json backup
            try:
                await context.storage_state(path=self.auth_file)
                print(f"[OK] Authentication backup saved to: {os.path.abspath(self.auth_file)}")
            except Exception:
                pass

            print("[OK] Persistent profile saved successfully! All future runs will stay logged in.\n")
            await context.close()

        return True

    def has_saved_session(self) -> bool:
        """Check if a saved auth_state.json exists."""
        return os.path.exists(self.auth_file)
