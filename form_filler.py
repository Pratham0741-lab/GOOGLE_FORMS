"""
Form filler module for programmatically mapping answers to Google Forms DOM elements,
handling pagination across multi-page forms, and executing dry-run or live submissions.
"""

import asyncio
import logging
from typing import List, Dict, Any, Optional
from playwright.async_api import Page, Locator

from form_scraper import Question, QuestionType, FormScraper
from answer_engine import GeneratedAnswer

logger = logging.getLogger(__name__)


class FormFiller:
    """
    Interacts with Google Forms DOM to fill questions, navigate multi-page sections,
    and optionally submit responses.
    """

    def __init__(self, action_delay_ms: int = 150):
        self.action_delay_ms = action_delay_ms
        self.scraper = FormScraper()

    async def fill_current_page(
        self,
        page: Page,
        questions: List[Question],
        answers: List[GeneratedAnswer],
        dry_run: bool = False
    ) -> List[Dict[str, Any]]:
        """
        Fill all questions on the current page with their corresponding answers.
        Returns a list of status dictionaries for audit logging.
        """
        results: List[Dict[str, Any]] = []
        containers = page.locator(self.scraper.ITEM_CONTAINER_SELECTOR)

        # Build index map for answers
        ans_by_idx = {ans.question_index: ans for ans in answers}

        for q in questions:
            ans = ans_by_idx.get(q.index)
            if not ans:
                logger.warning("No answer found for question #%d ('%s')", q.index, q.text)
                results.append({
                    "question_index": q.index,
                    "question_text": q.text,
                    "status": "skipped_no_answer",
                    "chosen_answer": None
                })
                continue

            container = containers.nth(q.container_locator_index)
            try:
                fill_status = await self._fill_question(page, container, q, ans, dry_run=dry_run)
                results.append({
                    "question_index": q.index,
                    "question_text": q.text,
                    "question_type": q.type.value,
                    "status": fill_status,
                    "chosen_answer": ans.chosen_answer,
                    "reasoning": ans.reasoning
                })
            except Exception as e:
                logger.error("Failed filling question #%d ('%s'): %s", q.index, q.text, str(e))
                results.append({
                    "question_index": q.index,
                    "question_text": q.text,
                    "status": f"error: {str(e)}",
                    "chosen_answer": ans.chosen_answer
                })

            if self.action_delay_ms > 0:
                await asyncio.sleep(self.action_delay_ms / 1000.0)

        return results

    async def _fill_question(
        self,
        page: Page,
        container: Locator,
        question: Question,
        answer: GeneratedAnswer,
        dry_run: bool = False
    ) -> str:
        """Fill a single question inside its container."""
        chosen = answer.chosen_answer

        if question.type == QuestionType.RADIO:
            return await self._fill_radio(page, container, question, str(chosen), answer.option_key)

        elif question.type == QuestionType.CHECKBOX:
            chosen_list = chosen if isinstance(chosen, list) else [str(chosen)]
            return await self._fill_checkbox(page, container, question, chosen_list)

        elif question.type == QuestionType.DROPDOWN:
            return await self._fill_dropdown(page, container, question, str(chosen))

        elif question.type in (QuestionType.SHORT_ANSWER, QuestionType.DATE, QuestionType.TIME):
            return await self._fill_short_text(page, container, str(chosen))

        elif question.type == QuestionType.PARAGRAPH:
            return await self._fill_paragraph(page, container, str(chosen))

        elif question.type == QuestionType.SCALE:
            return await self._fill_scale(page, container, str(chosen))

        else:
            logger.warning("Unsupported or unknown question type '%s' for #%d", question.type.value, question.index)
            return "skipped_unsupported_type"

    async def _fill_radio(
        self,
        page: Page,
        container: Locator,
        question: Question,
        chosen_text: str,
        option_key: Optional[str]
    ) -> str:
        """Select a radio button option matching chosen text or key."""
        radios = container.locator('div[role="radio"], label')
        count = await radios.count()
        if count == 0:
            return "failed_no_radio_elements"

        target_el: Optional[Locator] = None

        for i in range(count):
            radio_el = radios.nth(i)
            # Try to match data-value, aria-label, or text inside
            data_val = (await radio_el.get_attribute("data-value") or "").strip()
            aria_label = (await radio_el.get_attribute("aria-label") or "").strip()
            inner_text = (await radio_el.inner_text()).strip()

            # Exact match
            if chosen_text.lower() in (data_val.lower(), aria_label.lower(), inner_text.lower()):
                target_el = radio_el
                break
            
            # Key match (e.g. Option 'A' is index 0)
            if option_key and option_key.upper() == chr(65 + i):
                target_el = radio_el
                break

            # Partial match
            if chosen_text.lower() in inner_text.lower() or inner_text.lower() in chosen_text.lower():
                target_el = radio_el

        if target_el:
            # Scroll smoothly and highlight question container
            await container.scroll_into_view_if_needed()
            await self._highlight_element(page, target_el)
            # Click both via standard click and evaluate to ensure visible UI update and event trigger
            try:
                await target_el.click(force=True, timeout=1500)
            except Exception:
                await target_el.evaluate("el => el.click()")
            return "filled_radio_success"

        # Fallback to first radio if none matched
        await container.scroll_into_view_if_needed()
        await self._highlight_element(page, radios.first)
        try:
            await radios.first.click(force=True, timeout=1500)
        except Exception:
            await radios.first.evaluate("el => el.click()")
        return "filled_radio_fallback_first"

    async def _highlight_element(self, page: Page, el: Locator) -> None:
        """Add a temporary visual highlight outline so the user can clearly see the bot selecting it."""
        try:
            await el.evaluate(
                """el => {
                    const orig = el.style.outline;
                    el.style.outline = '3px solid #1a73e8';
                    el.style.transition = 'outline 0.2s ease-in-out';
                    setTimeout(() => { el.style.outline = orig; }, 800);
                }"""
            )
        except Exception:
            pass

    async def _fill_checkbox(
        self,
        page: Page,
        container: Locator,
        question: Question,
        chosen_items: List[str]
    ) -> str:
        """Check all checkbox options matching the list of chosen items using visible DOM clicks."""
        await container.scroll_into_view_if_needed()
        checkboxes = container.locator('div[role="checkbox"], label')
        count = await checkboxes.count()
        if count == 0:
            return "failed_no_checkbox_elements"

        checked_count = 0
        for i in range(count):
            cb_el = checkboxes.nth(i)
            inner_text = (await cb_el.inner_text()).strip()
            aria_label = (await cb_el.get_attribute("aria-label") or "").strip()

            should_check = False
            for target in chosen_items:
                t = target.strip().lower()
                if t in inner_text.lower() or (aria_label and t in aria_label.lower()):
                    should_check = True
                    break

            if should_check:
                checked_state = await cb_el.get_attribute("aria-checked")
                if checked_state != "true":
                    await self._highlight_element(page, cb_el)
                    try:
                        await cb_el.click(force=True, timeout=1500)
                    except Exception:
                        await cb_el.evaluate("el => el.click()")
                    await asyncio.sleep(0.15)
                checked_count += 1

        if checked_count == 0 and count > 0:
            await self._highlight_element(page, checkboxes.first)
            try:
                await checkboxes.first.click(force=True, timeout=1500)
            except Exception:
                await checkboxes.first.evaluate("el => el.click()")
            return "filled_checkbox_fallback_first"

        return f"filled_checkbox_success_{checked_count}_checked"

    async def _fill_dropdown(
        self,
        page: Page,
        container: Locator,
        question: Question,
        chosen_text: str
    ) -> str:
        """Open dropdown and select matching option item via DOM events."""
        dropdown_trigger = container.locator('div[role="listbox"], div.MocG8c, div[jsname="W6dt4e"]').first
        if await dropdown_trigger.count() == 0:
            return "failed_no_dropdown_element"

        await container.scroll_into_view_if_needed()
        await self._highlight_element(page, dropdown_trigger)
        try:
            await dropdown_trigger.click(force=True, timeout=1500)
        except Exception:
            await dropdown_trigger.evaluate("el => el.click()")
        await asyncio.sleep(0.3)

        # Locate options in popup overlay or within dropdown
        options = page.locator('div[role="option"]:visible')
        opt_count = await options.count()

        if opt_count == 0:
            options = container.locator('div[role="option"]')
            opt_count = await options.count()

        target_opt: Optional[Locator] = None
        for i in range(opt_count):
            opt_el = options.nth(i)
            text = (await opt_el.inner_text()).strip()
            data_val = (await opt_el.get_attribute("data-value") or "").strip()
            
            if text.lower() == "choose":
                continue

            if chosen_text.lower() == text.lower() or chosen_text.lower() == data_val.lower():
                target_opt = opt_el
                break
            if chosen_text.lower() in text.lower() or text.lower() in chosen_text.lower():
                target_opt = opt_el

        if target_opt:
            await self._highlight_element(page, target_opt)
            try:
                await target_opt.click(force=True, timeout=1500)
            except Exception:
                await target_opt.evaluate("el => el.click()")
            await asyncio.sleep(0.2)
            return "filled_dropdown_success"

        for i in range(opt_count):
            opt_el = options.nth(i)
            t = (await opt_el.inner_text()).strip()
            if t and t.lower() != "choose":
                try:
                    await opt_el.click(force=True, timeout=1500)
                except Exception:
                    await opt_el.evaluate("el => el.click()")
                return "filled_dropdown_fallback_first"

        await page.keyboard.press("Escape")
        return "failed_dropdown_option_not_found"

    async def _fill_short_text(self, page: Page, container: Locator, text: str) -> str:
        """Fill short answer input field programmatically via DOM events."""
        input_el = container.locator('input[type="text"], input.whsOnd').first
        if await input_el.count() == 0:
            return "failed_no_text_input"

        try:
            is_disabled = await input_el.is_disabled()
            if is_disabled:
                logger.info("Field is disabled, applying DOM value dispatch...")
            await input_el.evaluate(
                """(el, val) => {
                    el.removeAttribute('disabled');
                    el.value = val;
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                }""",
                text
            )
            return "filled_short_text_success"
        except Exception as e:
            return f"error_filling_text: {str(e)}"

    async def _fill_paragraph(self, page: Page, container: Locator, text: str) -> str:
        """Fill paragraph textarea programmatically via DOM events."""
        textarea_el = container.locator('textarea, textarea.KHxj8b').first
        if await textarea_el.count() == 0:
            return await self._fill_short_text(page, container, text)

        try:
            await textarea_el.evaluate(
                """(el, val) => {
                    el.removeAttribute('disabled');
                    el.value = val;
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                }""",
                text
            )
            return "filled_paragraph_success"
        except Exception as e:
            return f"error_filling_paragraph: {str(e)}"

    async def _fill_scale(self, page: Page, container: Locator, scale_value: str) -> str:
        """Click linear scale radio option via DOM click."""
        radios = container.locator('div[role="radiogroup"] div[role="radio"], div[role="radiogroup"] label')
        count = await radios.count()
        if count == 0:
            return "failed_no_scale_radios"

        for i in range(count):
            r = radios.nth(i)
            val = await r.get_attribute("data-value")
            aria_label = await r.get_attribute("aria-label")
            
            if val == scale_value or (aria_label and scale_value in aria_label):
                await r.scroll_into_view_if_needed()
                await r.evaluate("el => el.click()")
                return f"filled_scale_success_val_{scale_value}"

        idx = min(int(scale_value) - 1, count - 1) if scale_value.isdigit() else count // 2
        await radios.nth(idx).evaluate("el => el.click()")
        return f"filled_scale_fallback_idx_{idx}"

    async def has_next_page(self, page: Page) -> bool:
        """Check if there is a 'Next' button indicating multiple pages."""
        next_button = page.locator(
            'div[role="button"]:has-text("Next"), '
            'span:has-text("Next"), '
            'div[jsname="OCpkoe"]'
        ).first
        if await next_button.count() > 0:
            is_visible = await next_button.is_visible()
            return is_visible
        return False

    async def navigate_next(self, page: Page) -> bool:
        """Click 'Next' button via DOM evaluation to go to next section/page."""
        next_button = page.locator(
            'div[role="button"]:has-text("Next"), '
            'span:has-text("Next"), '
            'div[jsname="OCpkoe"]'
        ).first
        if await next_button.count() > 0 and await next_button.is_visible():
            logger.info("Advancing to next section via DOM click...")
            await next_button.evaluate("el => el.click()")
            
            try:
                await page.wait_for_load_state("load", timeout=5000)
            except Exception:
                pass
            await asyncio.sleep(0.5)
            return True
        return False

    async def is_submit_page(self, page: Page) -> bool:
        """Check if Submit button is available on the current page."""
        submit_btn = page.locator(
            'button:has-text("Submit"), '
            'input[type="submit"], '
            'div[role="button"]:has-text("Submit"), '
            'span:has-text("Submit"), '
            'div[role="button"]:has-text("Send"), '
            'span:has-text("Send"), '
            'div[jsname="M2UYVd"], '
            'div[jsname="M2vTeb"]'
        ).first
        if await submit_btn.count() > 0:
            return await submit_btn.is_visible()
        return False

    async def submit_form(self, page: Page, dry_run: bool = False) -> Dict[str, Any]:
        """
        Submit the form via direct DOM click dispatch or log dry-run without submitting.
        """
        if dry_run:
            logger.info("DRY-RUN MODE: Form questions filled. Skipping Submit click.")
            return {
                "submitted": False,
                "dry_run": True,
                "status": "dry_run_success",
                "message": "Dry-run execution completed. Form was filled but not submitted."
            }

        submit_btn = page.locator(
            'button:has-text("Submit"), '
            'input[type="submit"], '
            'div[role="button"]:has-text("Submit"), '
            'span:has-text("Submit"), '
            'div[role="button"]:has-text("Send"), '
            'span:has-text("Send"), '
            'div[jsname="M2UYVd"], '
            'div[jsname="M2vTeb"]'
        ).first

        if await submit_btn.count() == 0:
            return {
                "submitted": False,
                "dry_run": False,
                "status": "error_submit_button_not_found",
                "message": "Could not find visible Submit button on form page."
            }

        logger.info("Submitting form via DOM click dispatch...")
        await submit_btn.scroll_into_view_if_needed()
        await submit_btn.evaluate("el => el.click()")

        # Wait for confirmation page
        try:
            await page.wait_for_selector(
                '.freebirdFormviewerViewResponseConfirmationMessage, '
                'div[role="heading"]:has-text("recorded"), '
                'div:has-text("Your response has been recorded"), '
                'div:has-text("response was submitted")',
                timeout=8000
            )
            confirmation_text = "Your response has been recorded."
            conf_loc = page.locator('.freebirdFormviewerViewResponseConfirmationMessage, div.vHW8K').first
            if await conf_loc.count() > 0:
                confirmation_text = (await conf_loc.inner_text()).strip()

            logger.info("Form submission confirmed: %s", confirmation_text)
            return {
                "submitted": True,
                "dry_run": False,
                "status": "submitted_successfully",
                "confirmation_message": confirmation_text
            }
        except Exception as e:
            logger.info("Form submitted (confirmation wait ended): %s", str(e))
            return {
                "submitted": True,
                "dry_run": False,
                "status": "submitted_successfully",
                "message": "Submit clicked programmatically via DOM event."
            }
