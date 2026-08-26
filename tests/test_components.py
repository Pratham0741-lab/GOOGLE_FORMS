import os
import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import json
import pytest
import asyncio
from playwright.async_api import async_playwright

from form_scraper import FormScraper, QuestionType, Question, QuestionOption
from answer_engine import MockAnswerEngine, GeneratedAnswer, create_answer_engine
from form_filler import FormFiller
from main import FormAutomationRunner


MOCK_FORM_HTML_PAGE_1 = """
<!DOCTYPE html>
<html>
<head>
  <title>Mock Google Form Survey</title>
  <style>
    div[role="radio"], div[role="checkbox"] { display: inline-block; width: 16px; height: 16px; border: 1px solid black; margin: 4px; }
  </style>
</head>
<body>
  <div role="heading" aria-level="1" class="F9vfv">AI & Technology Survey</div>
  <div class="freebirdFormviewerViewHeaderDescription">A sample survey evaluating AI and software capabilities.</div>

  <form>
    <!-- Q0: Radio / Multiple Choice -->
    <div role="listitem" class="Qr7Oae">
      <div role="heading" class="M7eMe">What is the primary programming language for Playwright?</div>
      <div role="radiogroup">
        <label class="docssharedWizToggleLabeledContainer">
          <div role="radio" data-value="Python" aria-label="Python">P</div>
          <span class="aDTYNe">Python</span>
        </label>
        <label class="docssharedWizToggleLabeledContainer">
          <div role="radio" data-value="Ruby" aria-label="Ruby">R</div>
          <span class="aDTYNe">Ruby</span>
        </label>
        <label class="docssharedWizToggleLabeledContainer">
          <div role="radio" data-value="Pascal" aria-label="Pascal">Pa</div>
          <span class="aDTYNe">Pascal</span>
        </label>
      </div>
    </div>

    <!-- Q1: Checkbox / Multi-select -->
    <div role="listitem" class="Qr7Oae">
      <div role="heading" class="M7eMe">Select all headless browsers supported:</div>
      <label class="docssharedWizToggleLabeledContainer">
        <div role="checkbox" aria-label="Chromium" aria-checked="false">C</div>
        <span class="aDTYNe">Chromium</span>
      </label>
      <label class="docssharedWizToggleLabeledContainer">
        <div role="checkbox" aria-label="Firefox" aria-checked="false">F</div>
        <span class="aDTYNe">Firefox</span>
      </label>
      <label class="docssharedWizToggleLabeledContainer">
        <div role="checkbox" aria-label="Internet Explorer 6" aria-checked="false">IE</div>
        <span class="aDTYNe">Internet Explorer 6</span>
      </label>
    </div>

    <!-- Q2: Short Answer -->
    <div role="listitem" class="Qr7Oae">
      <div role="heading" class="M7eMe">Enter your feedback username:</div>
      <input type="text" class="whsOnd" />
    </div>

    <!-- Q3: Paragraph -->
    <div role="listitem" class="Qr7Oae">
      <div role="heading" class="M7eMe">Explain your automation goals:</div>
      <textarea class="KHxj8b"></textarea>
    </div>

    <!-- Q4: Linear Scale -->
    <div role="listitem" class="Qr7Oae">
      <div role="heading" class="M7eMe">Rate the speed of this execution from 1 to 5:</div>
      <div class="freebirdMaterialScaleviewLabels">
        <span class="jT5UQc">Slow</span>
        <span class="snByac">Fast</span>
      </div>
      <div role="radiogroup">
        <div role="radio" data-value="1" aria-label="1">1</div>
        <div role="radio" data-value="2" aria-label="2">2</div>
        <div role="radio" data-value="3" aria-label="3">3</div>
        <div role="radio" data-value="4" aria-label="4">4</div>
        <div role="radio" data-value="5" aria-label="5">5</div>
      </div>
    </div>

    <!-- Submit Button -->
    <button type="button" id="submit-btn" onclick="document.body.innerHTML='<div class=\\'freebirdFormviewerViewResponseConfirmationMessage\\'>Your response has been recorded.</div>';">
      Submit
    </button>
  </form>
</body>
</html>
"""


@pytest.mark.asyncio
async def test_form_scraper_and_filler(tmp_path):
    """Test extracting questions, filling them with MockAnswerEngine, and verifying DOM updates."""
    # Write mock HTML file
    test_html_file = tmp_path / "mock_form.html"
    test_html_file.write_text(MOCK_FORM_HTML_PAGE_1, encoding="utf-8")
    test_file_url = test_html_file.as_uri()

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto(test_file_url)

        scraper = FormScraper()
        title = await scraper.get_form_title(page)
        assert "AI & Technology Survey" in title

        questions = await scraper.extract_questions(page)
        assert len(questions) == 5

        # Check types
        assert questions[0].type == QuestionType.RADIO
        assert len(questions[0].options) == 3
        assert questions[0].options[0].text == "Python"

        assert questions[1].type == QuestionType.CHECKBOX
        assert len(questions[1].options) == 3

        assert questions[2].type == QuestionType.SHORT_ANSWER
        assert questions[3].type == QuestionType.PARAGRAPH
        assert questions[4].type == QuestionType.SCALE

        # Generate answers with mock engine
        engine = MockAnswerEngine(enable_search=False)
        answers = []
        for q in questions:
            ans = await engine.generate_answer(q, form_context=title)
            answers.append(ans)

        filler = FormFiller(action_delay_ms=50)
        fill_results = await filler.fill_current_page(page, questions, answers, dry_run=False)
        assert len(fill_results) == 5
        for res in fill_results:
            assert "success" in res["status"] or "fallback" in res["status"]

        # Submit form
        sub_res = await filler.submit_form(page, dry_run=False)
        assert sub_res["submitted"] is True
        assert sub_res["status"] == "submitted_successfully"
        assert "recorded" in sub_res["confirmation_message"]

        await browser.close()


@pytest.mark.asyncio
async def test_runner_dry_run(tmp_path):
    """Test full FormAutomationRunner execution in dry-run mode."""
    test_html_file = tmp_path / "mock_form_dryrun.html"
    test_html_file.write_text(MOCK_FORM_HTML_PAGE_1, encoding="utf-8")
    test_file_url = test_html_file.as_uri()
    audit_log_file = tmp_path / "test_audit.json"

    engine = MockAnswerEngine(enable_search=False)
    runner = FormAutomationRunner(
        url=test_file_url,
        engine=engine,
        dry_run=True,
        headless=True,
        timeout_ms=10000,
        action_delay_ms=10,
        json_log_path=str(audit_log_file)
    )

    audit_result = await runner.run()
    assert audit_result["metadata"]["dry_run"] is True
    assert audit_result["summary"]["total_questions"] == 5
    assert audit_result["summary"]["submission_status"] == "dry_run_success"
    assert audit_log_file.exists()

    with open(audit_log_file, "r", encoding="utf-8") as f:
        saved_data = json.load(f)
    assert saved_data["summary"]["total_questions"] == 5


if __name__ == "__main__":
    import tempfile
    import shutil
    
    print("Running integration tests directly...")
    temp_dir = Path(tempfile.mkdtemp())
    try:
        print("1. Running test_form_scraper_and_filler...")
        asyncio.run(test_form_scraper_and_filler(temp_dir))
        print("   -> PASSED!")

        print("2. Running test_runner_dry_run...")
        asyncio.run(test_runner_dry_run(temp_dir))
        print("   -> PASSED!")

        print("\nAll integration tests passed successfully!")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
