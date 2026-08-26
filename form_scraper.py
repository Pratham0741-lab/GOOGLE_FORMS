"""
Data models and scraper module for extracting questions and options from Google Forms DOM.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Dict, Any
import logging
from playwright.async_api import Page, Locator

logger = logging.getLogger(__name__)


class QuestionType(str, Enum):
    RADIO = "multiple_choice"
    CHECKBOX = "checkbox"
    DROPDOWN = "dropdown"
    SHORT_ANSWER = "short_answer"
    PARAGRAPH = "paragraph"
    SCALE = "linear_scale"
    DATE = "date"
    TIME = "time"
    GRID_RADIO = "grid_radio"
    GRID_CHECKBOX = "grid_checkbox"
    UNKNOWN = "unknown"


@dataclass
class QuestionOption:
    """Represents a single answer option for a question."""
    index: int
    text: str
    key: Optional[str] = None  # e.g., "A", "B", "1", "2"
    is_other: bool = False     # If it's an "Other:" option with custom input


@dataclass
class Question:
    """Represents a parsed Google Form question item."""
    index: int
    text: str
    type: QuestionType
    options: List[QuestionOption] = field(default_factory=list)
    required: bool = False
    description: Optional[str] = None
    container_locator_index: int = 0
    scale_range: Optional[tuple[int, int]] = None
    scale_labels: Optional[tuple[str, str]] = None
    extra_metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "index": self.index,
            "text": self.text,
            "type": self.type.value,
            "options": [{"index": opt.index, "key": opt.key, "text": opt.text, "is_other": opt.is_other} for opt in self.options],
            "required": self.required,
            "description": self.description,
            "scale_range": self.scale_range,
            "scale_labels": self.scale_labels
        }


class FormScraper:
    """
    Extracts structured form metadata and questions from a live Google Form page.
    Uses resilient Google Forms DOM selectors (modern Material Design 3 and legacy DOM).
    """

    # Common container selectors used in Google Forms
    ITEM_CONTAINER_SELECTOR = (
        'div[role="listitem"], '
        'div[jsmodel][data-item-id], '
        'div.Qr7Oae, '
        'div.geS5nc'
    )

    async def get_form_title(self, page: Page) -> str:
        """Extract the main form title."""
        title_selectors = [
            'div[role="heading"][aria-level="1"]',
            'div.F9vfv',
            'div.freebirdFormviewerViewHeaderTitle',
            'div.M7eMe:first-of-type',
            'title'
        ]
        for sel in title_selectors:
            loc = page.locator(sel).first
            if await loc.count() > 0:
                text = (await loc.inner_text()).strip()
                if text and text != "Google Forms":
                    return text
        page_title = await page.title()
        return page_title or "Google Form"

    async def get_form_description(self, page: Page) -> Optional[str]:
        """Extract form header description if present."""
        desc_selectors = [
            'div.F9vfv + div',
            'div.freebirdFormviewerViewHeaderDescription',
            'div[role="heading"][aria-level="1"] ~ div.cBGG4e'
        ]
        for sel in desc_selectors:
            loc = page.locator(sel).first
            if await loc.count() > 0:
                text = (await loc.inner_text()).strip()
                if text:
                    return text
        return None

    async def extract_questions(self, page: Page) -> List[Question]:
        """
        Extract all questions currently visible on the active page of the form.
        """
        questions: List[Question] = []
        
        # Wait for either questions container or form body to be present
        try:
            await page.wait_for_selector(
                'div[role="listitem"], div[role="heading"], form', 
                timeout=8000
            )
        except Exception:
            logger.warning("Timeout waiting for form containers, scanning DOM directly...")

        containers = page.locator(self.ITEM_CONTAINER_SELECTOR)
        total_containers = await containers.count()
        logger.debug("Found %d candidate question containers", total_containers)

        q_index = 0
        for i in range(total_containers):
            container = containers.nth(i)
            
            # Extract question title / text
            q_text = await self._extract_question_title(container)
            if not q_text:
                # Some listitems might be section headers or decoration dividers
                continue

            # Check if required
            is_required = await self._check_is_required(container)

            # Extract description/help text
            description = await self._extract_question_description(container)

            # Determine question type and extract choices/inputs
            q_type, options, extra = await self._detect_type_and_options(container, page)

            question = Question(
                index=q_index,
                text=q_text,
                type=q_type,
                options=options,
                required=is_required,
                description=description,
                container_locator_index=i,
                scale_range=extra.get("scale_range"),
                scale_labels=extra.get("scale_labels"),
                extra_metadata=extra
            )
            questions.append(question)
            q_index += 1

        logger.info("Successfully extracted %d questions from current page", len(questions))
        return questions

    async def _extract_question_title(self, container: Locator) -> Optional[str]:
        """Extract the prompt/title for the question container."""
        title_selectors = [
            'div[role="heading"]',
            'span.M7eMe',
            'div.M7eMe',
            'div.freebirdFormviewerComponentsQuestionBaseTitle',
            '.HoFMcf'
        ]
        for sel in title_selectors:
            loc = container.locator(sel).first
            if await loc.count() > 0:
                text = (await loc.inner_text()).strip()
                if text:
                    # Clean out trailing asterisk from required markings if attached to text
                    if text.endswith("*"):
                        text = text[:-1].strip()
                    return text
        return None

    async def _check_is_required(self, container: Locator) -> bool:
        """Check if question is required."""
        required_selectors = [
            'span[aria-label*="Required"]',
            'span[aria-label*="required"]',
            'span.v3duvd',
            'span.freebirdFormviewerViewItemsItemRequiredAsterisk'
        ]
        for sel in required_selectors:
            if await container.locator(sel).count() > 0:
                return True
        return False

    async def _extract_question_description(self, container: Locator) -> Optional[str]:
        """Extract question description/subtitle if available."""
        desc_selectors = [
            'div[id$="_desc"]',
            'div.g4SkDu',
            'div.wGQFbe',
            'div.freebirdFormviewerComponentsQuestionBaseHelpText'
        ]
        for sel in desc_selectors:
            loc = container.locator(sel).first
            if await loc.count() > 0:
                text = (await loc.inner_text()).strip()
                if text:
                    return text
        return None

    async def _detect_type_and_options(
        self, 
        container: Locator, 
        page: Page
    ) -> tuple[QuestionType, List[QuestionOption], Dict[str, Any]]:
        """
        Identify whether the question is multiple choice, checkbox, dropdown, text, etc.
        """
        extra: Dict[str, Any] = {}
        options: List[QuestionOption] = []

        # 1. Linear Scale Check (role="radiogroup" with numeric scale items and scale labels)
        scale_radios = container.locator('div[role="radiogroup"] div[role="radio"]')
        scale_count = await scale_radios.count()
        if scale_count >= 2:
            first_val = await scale_radios.nth(0).get_attribute("data-value")
            last_val = await scale_radios.nth(scale_count - 1).get_attribute("data-value")
            
            # Linear scales have digit values like "1".."5" or "1".."10"
            if first_val and last_val and first_val.isdigit() and last_val.isdigit():
                labels_loc = container.locator('.jT5UQc, .snByac, .freebirdMaterialScaleviewLabels')
                scale_labels = None
                if await labels_loc.count() >= 2:
                    start_lbl = (await labels_loc.nth(0).inner_text()).strip()
                    end_lbl = (await labels_loc.nth(-1).inner_text()).strip()
                    scale_labels = (start_lbl, end_lbl)

                for idx in range(scale_count):
                    val = await scale_radios.nth(idx).get_attribute("data-value") or str(idx + 1)
                    options.append(QuestionOption(index=idx, text=val, key=val))
                
                try:
                    start_num = int(first_val)
                    end_num = int(last_val)
                    extra["scale_range"] = (start_num, end_num)
                except ValueError:
                    extra["scale_range"] = (1, scale_count)
                extra["scale_labels"] = scale_labels
                return QuestionType.SCALE, options, extra

        # 2. Standard Multiple Choice (Radio)
        radios = container.locator('div[role="radio"]')
        radio_count = await radios.count()
        if radio_count > 0:
            for idx in range(radio_count):
                radio_el = radios.nth(idx)
                opt_text = await self._get_option_label(radio_el, container)
                is_other = ("Other" in opt_text) or await radio_el.locator('input[type="text"]').count() > 0
                key = chr(65 + idx) if idx < 26 else str(idx + 1)
                options.append(QuestionOption(index=idx, text=opt_text, key=key, is_other=is_other))
            return QuestionType.RADIO, options, extra

        # 3. Checkboxes (Multi-select)
        checkboxes = container.locator('div[role="checkbox"]')
        cb_count = await checkboxes.count()
        if cb_count > 0:
            for idx in range(cb_count):
                cb_el = checkboxes.nth(idx)
                opt_text = await self._get_option_label(cb_el, container)
                is_other = ("Other" in opt_text) or await cb_el.locator('input[type="text"]').count() > 0
                key = chr(65 + idx) if idx < 26 else str(idx + 1)
                options.append(QuestionOption(index=idx, text=opt_text, key=key, is_other=is_other))
            return QuestionType.CHECKBOX, options, extra

        # 4. Dropdown Listbox
        listboxes = container.locator('div[role="listbox"], div[jsname="W6dt4e"], div.MocG8c')
        if await listboxes.count() > 0:
            opts_loc = container.locator('div[role="option"]')
            opt_count = await opts_loc.count()
            if opt_count > 0:
                for idx in range(opt_count):
                    opt_el = opts_loc.nth(idx)
                    text = (await opt_el.inner_text()).strip()
                    if not text or text.lower() == "choose":
                        continue
                    key = chr(65 + len(options)) if len(options) < 26 else str(len(options) + 1)
                    options.append(QuestionOption(index=len(options), text=text, key=key))
            return QuestionType.DROPDOWN, options, extra

        # 5. Long Answer / Paragraph (Textarea)
        textareas = container.locator('textarea, textarea.KHxj8b')
        if await textareas.count() > 0:
            return QuestionType.PARAGRAPH, [], extra

        # 6. Short Answer (Input text)
        inputs = container.locator('input[type="text"]:not([aria-label*="Other"]), input.whsOnd')
        if await inputs.count() > 0:
            return QuestionType.SHORT_ANSWER, [], extra

        # 7. Date input
        date_inputs = container.locator('input[type="date"], input[aria-label*="Month"], input[aria-label*="Day"]')
        if await date_inputs.count() > 0:
            return QuestionType.DATE, [], extra

        # 8. Time input
        time_inputs = container.locator('input[type="time"], input[aria-label*="Hour"], input[aria-label*="Minute"]')
        if await time_inputs.count() > 0:
            return QuestionType.TIME, [], extra

        return QuestionType.UNKNOWN, [], extra

    async def _get_option_label(self, option_el: Locator, parent_container: Locator) -> str:
        """Extract human-readable label for a radio or checkbox option."""
        # Try getting aria-label or data-value first
        aria_label = await option_el.get_attribute("aria-label")
        if aria_label and aria_label.strip():
            return aria_label.strip()

        data_val = await option_el.get_attribute("data-value")
        if data_val and data_val.strip():
            return data_val.strip()

        # Try parent label / wrapper
        parent = option_el.locator("..")
        label_spans = parent.locator('span.aDTYNe, span.ulDsOb, span.Y62eN, span.bz0duf, .N5QMN, span')
        if await label_spans.count() > 0:
            for s_idx in range(await label_spans.count()):
                t = (await label_spans.nth(s_idx).inner_text()).strip()
                if t:
                    return t

        parent_text = (await parent.inner_text()).strip()
        if parent_text:
            return parent_text

        return "Option"
