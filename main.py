"""
Google Forms Automated Answering & Headless Submission Orchestrator.

Extracts questions, generates AI answers (with optional web search augmentation),
fills form elements programmatically, handles multi-page forms, and writes structured
audit logs without requiring any visible UI.
"""

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional

from dotenv import load_dotenv
from playwright.async_api import async_playwright, Browser, BrowserContext, Page, Error as PlaywrightError

from form_scraper import FormScraper, Question
from answer_engine import create_answer_engine, BaseAnswerEngine, GeneratedAnswer
from form_filler import FormFiller
from browser_manager import BrowserManager, DEFAULT_AUTH_FILE

# Load environment variables from .env and .env.example
load_dotenv()
load_dotenv(".env.example")
load_dotenv(".env.local")


def setup_logger(log_file_path: Optional[str] = None, verbose: bool = False) -> logging.Logger:
    """Configure structured console and file logging with UTF-8 encoding."""
    # Ensure Windows stdout uses UTF-8
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    logger = logging.getLogger("google_forms_bot")
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    logger.handlers.clear()

    formatter = logging.Formatter(
        fmt="[%(asctime)s] [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    # File Handler
    if log_file_path:
        Path(log_file_path).parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(log_file_path, encoding="utf-8")
        fh.setLevel(logging.DEBUG if verbose else logging.INFO)
        fh.setFormatter(formatter)
        logger.addHandler(fh)

    # Console Handler (clean format for background/cron execution)
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.DEBUG if verbose else logging.INFO)
    ch.setFormatter(formatter)
    logger.addHandler(ch)

    return logger


class FormAutomationRunner:
    """
    Coordinates the full headless lifecycle:
    Browser Selection -> DOM Scraping -> LLM Answer Gen (+ Search) -> Form Filling -> Pagination -> Submission -> Auditing.
    """

    def __init__(
        self,
        url: str,
        engine: BaseAnswerEngine,
        browser_manager: Optional[BrowserManager] = None,
        browser_choice: Optional[str] = None,
        auto_submit: bool = False,
        dry_run: bool = False,
        headless: bool = False,
        timeout_ms: int = 45000,
        action_delay_ms: int = 400,
        json_log_path: Optional[str] = None,
        logger: Optional[logging.Logger] = None
    ):
        self.url = url
        self.engine = engine
        self.browser_manager = browser_manager or BrowserManager(browser_choice=browser_choice)
        self.browser_choice = browser_choice
        self.auto_submit = auto_submit and not dry_run
        self.dry_run = dry_run
        self.headless = headless
        self.timeout_ms = timeout_ms
        self.action_delay_ms = action_delay_ms
        self.json_log_path = json_log_path
        self.logger = logger or logging.getLogger("google_forms_bot")
        
        self.scraper = FormScraper()
        self.filler = FormFiller(action_delay_ms=action_delay_ms)

        # Audit record
        self.audit_log: Dict[str, Any] = {
            "metadata": {
                "started_at": datetime.now(timezone.utc).isoformat(),
                "form_url": self.url,
                "auto_submit": self.auto_submit,
                "dry_run": self.dry_run,
                "engine": self.engine.__class__.__name__,
                "browser": self.browser_choice or "default"
            },
            "form_details": {},
            "pages": [],
            "summary": {
                "total_pages": 0,
                "total_questions": 0,
                "answered_questions": 0,
                "submission_status": "not_started"
            }
        }

    async def run(self) -> Dict[str, Any]:
        """Execute the headless form answering pipeline."""
        start_time = time.time()
        self.logger.info("Initializing browser automation for form: %s", self.url)

        async with async_playwright() as pw:
            try:
                context, browser = await self.browser_manager.launch_browser(
                    pw=pw,
                    headless=self.headless,
                    browser_type_name=self.browser_choice,
                    use_auth=True
                )
                page: Page = context.pages[0] if context.pages else await context.new_page()
                page.set_default_timeout(self.timeout_ms)

                self.logger.info("[Browser Status] Active page loaded in persistent profile (headless=%s)", self.headless)
                self.logger.info("Navigating to form URL: %s", self.url)
                try:
                    await page.goto(self.url, wait_until="domcontentloaded", timeout=self.timeout_ms)
                except Exception as ne:
                    self.logger.warning("Initial navigation notice: %s. Retrying...", str(ne))
                    await asyncio.sleep(1)
                    await page.goto(self.url, wait_until="domcontentloaded", timeout=self.timeout_ms)

                self.logger.info("[Playwright Page Active] Current Page URL: %s | Page Title: '%s'", page.url, await page.title())

                # Check if redirected to Google Sign-In
                current_url = page.url
                if "accounts.google.com" in current_url or "signin" in current_url:
                    self.logger.warning("Form requires Google Account authentication!")
                    print("\n[!] WARNING: This Google Form requires you to sign in to your Google Account.")
                    print("Run the script with `--login` to open your browser and sign in once:")
                    print(f"  python main.py \"{self.url}\" --login\n")
                    self.audit_log["summary"]["submission_status"] = "auth_required"
                    if context:
                        await context.close()
                    return self.audit_log

                # Extract title and description
                form_title = await self.scraper.get_form_title(page)
                form_desc = await self.scraper.get_form_description(page)
                self.audit_log["form_details"] = {
                    "title": form_title,
                    "description": form_desc
                }
                self.logger.info("Loaded Form: '%s'", form_title)

                page_index = 1
                all_extracted_questions: List[Question] = []
                all_generated_answers: List[GeneratedAnswer] = []

                # Multi-page pagination loop
                while True:
                    self.logger.info("Processing form Page #%d...", page_index)
                    
                    # 1. Scrape questions
                    questions = await self.scraper.extract_questions(page)
                    if not questions:
                        self.logger.warning("No questions extracted on page #%d. Checking submission button...", page_index)

                    all_extracted_questions.extend(questions)
                    page_answers: List[GeneratedAnswer] = []

                    # 2. Generate answers via LLM + optional search
                    for q in questions:
                        self.logger.info("Generating answer for Q#%d: '%s' (Type: %s)", q.index, q.text, q.type.value)
                        answer = await self.engine.generate_answer(q, form_context=form_title)
                        page_answers.append(answer)
                        all_generated_answers.append(answer)

                        if answer.search_performed:
                            self.logger.info("  -> Search performed for '%s' (%d snippets)", answer.search_query, len(answer.search_results or []))
                        self.logger.info("  -> Chosen answer: %s (Reasoning: %s)", answer.chosen_answer, answer.reasoning)

                    # 3. Fill answers into current page
                    fill_results = await self.filler.fill_current_page(
                        page=page,
                        questions=questions,
                        answers=page_answers,
                        dry_run=self.dry_run
                    )

                    # Log page audit entry
                    self.audit_log["pages"].append({
                        "page_number": page_index,
                        "questions_count": len(questions),
                        "questions": [q.to_dict() for q in questions],
                        "answers": [a.to_dict() for a in page_answers],
                        "fill_results": fill_results
                    })

                    # 4. Check navigation or submission
                    if await self.filler.has_next_page(page):
                        self.logger.info("Page #%d completed. Advancing to next section...", page_index)
                        await self.filler.navigate_next(page)
                        page_index += 1
                    else:
                        # Final page reached
                        if self.auto_submit:
                            self.logger.info("Auto-submit enabled (--submit). Initiating submission...")
                            sub_result = await self.filler.submit_form(page, dry_run=False)
                        elif self.dry_run:
                            self.logger.info("Dry-run mode active. Answering complete without submission.")
                            sub_result = await self.filler.submit_form(page, dry_run=True)
                        else:
                            self.logger.info("Form filled successfully! Auto-submit is disabled.")
                            sub_result = {
                                "submitted": False,
                                "auto_submit": False,
                                "status": "filled_ready_for_review",
                                "message": "All questions were filled in the browser. Form kept open for user review."
                            }

                        self.audit_log["summary"]["submission_status"] = sub_result.get("status")
                        self.audit_log["summary"]["submission_details"] = sub_result
                        break

                # Finalize audit summary
                duration = round(time.time() - start_time, 2)
                self.audit_log["metadata"]["completed_at"] = datetime.now(timezone.utc).isoformat()
                self.audit_log["metadata"]["duration_seconds"] = duration
                self.audit_log["summary"]["total_pages"] = page_index
                self.audit_log["summary"]["total_questions"] = len(all_extracted_questions)
                self.audit_log["summary"]["answered_questions"] = len(all_generated_answers)

                self.logger.info("Run finished in %ss. Total Questions: %d, Status: %s", 
                                 duration, len(all_extracted_questions), self.audit_log["summary"]["submission_status"])

                # If running visibly and auto_submit is False, keep the browser open for the user to review
                if not self.headless and not self.auto_submit:
                    print("\n" + "=" * 65)
                    print(" ALL QUESTIONS FILLED SUCCESSFULLY! ")
                    print("=" * 65)
                    print("Your browser is open with all answers filled and ticked.")
                    print("Feel free to review your answers or click Submit in the browser.")
                    print("Press [ENTER] in this terminal when you want to finish:")
                    print("=" * 65)
                    try:
                        input(">>> Press ENTER to close browser and exit: ")
                    except (EOFError, KeyboardInterrupt):
                        pass

                if context:
                    await context.close()

            except PlaywrightError as pe:
                self.logger.error("Playwright automation error: %s", str(pe), exc_info=True)
                self.audit_log["summary"]["submission_status"] = f"playwright_error: {str(pe)}"
                raise
            except Exception as e:
                self.logger.error("Unexpected error during form processing: %s", str(e), exc_info=True)
                self.audit_log["summary"]["submission_status"] = f"error: {str(e)}"
                raise
            finally:
                self._save_json_audit_log()

        return self.audit_log

    def _save_json_audit_log(self) -> None:
        """Write structured JSON audit record to disk."""
        if not self.json_log_path:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            self.json_log_path = f"audit_log_{timestamp}.json"

        try:
            Path(self.json_log_path).parent.mkdir(parents=True, exist_ok=True)
            with open(self.json_log_path, "w", encoding="utf-8") as f:
                json.dump(self.audit_log, f, indent=2, ensure_ascii=False)
            self.logger.info("Structured audit log written to: %s", os.path.abspath(self.json_log_path))
        except Exception as e:
            self.logger.error("Failed to write JSON audit log: %s", str(e))


def parse_arguments() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Automated Headless Google Forms Bot with AI Answer Generation & Web Search Grounding.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )

    parser.add_argument(
        "form_url",
        nargs="?",
        default=os.environ.get("GOOGLE_FORM_URL"),
        help="Google Form URL (e.g. https://docs.google.com/forms/d/e/.../viewform)"
    )
    parser.add_argument(
        "--url",
        dest="url_opt",
        help="Alternative flag for Google Form URL"
    )
    parser.add_argument(
        "--submit",
        action="store_true",
        default=False,
        help="Automatically click Submit when done (by default, the bot leaves the form open for you to review and submit)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Run in test mode without auto submission"
    )
    parser.add_argument(
        "--provider",
        choices=["search", "free", "claude", "openai", "gemini", "mock"],
        default=os.environ.get("LLM_PROVIDER", "search" if not os.environ.get("ANTHROPIC_API_KEY") else "claude"),
        help="Answer provider: 'search' (100% free web search grounding), 'claude', 'openai', 'gemini', or 'mock'"
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("LLM_MODEL"),
        help="Model identifier (e.g. claude-3-5-sonnet-20241022, gpt-4o, gemini-2.5-flash)"
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="API Key override (defaults to ANTHROPIC_API_KEY, OPENAI_API_KEY, or GEMINI_API_KEY)"
    )
    parser.add_argument(
        "--enable-search",
        dest="enable_search",
        action="store_true",
        default=True,
        help="Enable DuckDuckGo search grounding for factual questions"
    )
    parser.add_argument(
        "--no-search",
        dest="enable_search",
        action="store_false",
        help="Disable web search grounding"
    )
    parser.add_argument(
        "--log-file",
        default=os.environ.get("LOG_FILE", "form_runner.log"),
        help="Path to write execution text log"
    )
    parser.add_argument(
        "--json-log",
        default=os.environ.get("JSON_AUDIT_LOG"),
        help="Path to write JSON audit trail (defaults to audit_log_<timestamp>.json)"
    )
    parser.add_argument(
        "--browser",
        choices=["chrome", "msedge", "firefox", "chromium", "ask"],
        default=os.environ.get("BROWSER_TYPE", "ask"),
        help="Browser to use: 'chrome' (Google Chrome), 'msedge' (Edge), 'firefox', 'chromium', or 'ask' to prompt"
    )
    parser.add_argument(
        "--login",
        action="store_true",
        default=False,
        help="Open visible browser to log in to your Google Account and save session"
    )
    parser.add_argument(
        "--auth-file",
        default=os.environ.get("AUTH_FILE", DEFAULT_AUTH_FILE),
        help="Path to saved authentication storage state file (default: auth_state.json)"
    )
    parser.add_argument(
        "--headless",
        dest="headless",
        action="store_true",
        default=False,
        help="Run browser in headless background mode without a visible window"
    )
    parser.add_argument(
        "--no-headless",
        dest="headless",
        action="store_false",
        help="Explicitly open visible browser window (default)"
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=45000,
        help="Browser page navigation timeout in milliseconds"
    )
    parser.add_argument(
        "--delay",
        type=int,
        default=400,
        help="Delay in milliseconds between answering and ticking form actions"
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose debug logging"
    )

    return parser.parse_args()


async def async_main() -> int:
    """Async entrypoint returning exit code."""
    args = parse_arguments()
    target_url = args.url_opt or args.form_url

    logger = setup_logger(log_file_path=args.log_file, verbose=args.verbose)

    # Initialize Browser Manager
    browser_manager = BrowserManager(
        auth_file=args.auth_file,
        interactive=(args.browser == "ask")
    )

    # Resolve browser choice
    browser_choice = args.browser
    if browser_choice == "ask":
        if sys.stdin.isatty():
            browser_choice = browser_manager.prompt_browser_selection()
        else:
            browser_choice = "chrome"

    # Handle interactive login mode
    if args.login:
        logger.info("Starting interactive login session for browser '%s'...", browser_choice)
        await browser_manager.interactive_login(target_url=target_url, browser_name=browser_choice)
        return 0

    if not target_url:
        logger.error("No form URL provided! Please specify via command argument or GOOGLE_FORM_URL environment variable.")
        return 1

    try:
        engine = create_answer_engine(
            provider=args.provider,
            api_key=args.api_key,
            model=args.model,
            enable_search=args.enable_search
        )
    except Exception as e:
        logger.error("Failed to initialize answer engine (%s): %s", args.provider, str(e))
        return 1

    runner = FormAutomationRunner(
        url=target_url,
        engine=engine,
        browser_manager=browser_manager,
        browser_choice=browser_choice,
        auto_submit=args.submit,
        dry_run=args.dry_run,
        headless=args.headless,
        timeout_ms=args.timeout,
        action_delay_ms=args.delay,
        json_log_path=args.json_log,
        logger=logger
    )

    try:
        results = await runner.run()
        if results.get("summary", {}).get("submission_status") == "auth_required":
            return 1
        logger.info("Automation completed successfully. Exit code 0.")
        return 0
    except Exception as e:
        logger.error("Execution terminated with error: %s", str(e))
        return 1


def main():
    """CLI script entrypoint."""
    exit_code = asyncio.run(async_main())
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
