# Automated Google Forms Bot (Headless AI Solver)

An autonomous, headless Python automation tool that extracts questions from Google Forms, solves them using an LLM (Claude, OpenAI, or Gemini) with optional real-time DuckDuckGo web search grounding for factual questions, programmatically fills all form fields, handles multi-page navigation, and writes structured audit logs without any visible browser UI.

---

## Key Features

- **No Auto-Submit by Default (Safe Review Mode)** — The bot fills out and ticks all answers in your open browser window, then leaves the form open for you to review and click Submit whenever you are ready.
- **Optional Auto-Submit (`--submit`)** — Pass `--submit` only when you explicitly want the bot to automatically click the final Submit button (ideal for unattended cron jobs).
- **Opens Your Installed Browser (`Google Chrome`, `Microsoft Edge`, `Mozilla Firefox`)** — Opens your real installed browser directly on your screen so you can watch the form being solved in real time.
- **Real-Time Visual Option Ticking** — Smoothly scrolls to each question, highlights the selected answers, and visually ticks checkboxes and radio buttons on your screen.
- **Persistent Profile & Saved Google Account** — Uses `.browser_profile/` so all Google accounts, passwords, and sessions remain logged in across runs.
- **Background Headless Mode (`--headless`)** — Can switch to 100% headless mode for background and cron jobs when no visible window is desired.
- **Comprehensive Question Extraction (`form_scraper.py`)** — Detects and parses:
  - Multiple Choice (Radio groups)
  - Checkboxes (Multi-select)
  - Dropdown lists
  - Short Answer inputs
  - Paragraph text areas
  - Linear Scales (1–5, 1–10 rating scales)
  - Date and Time pickers
- **Multi-Provider AI Answering (`answer_engine.py`)** — Supports Anthropic Claude, OpenAI GPT-4o, Google Gemini, and offline Mock engines with automatic multi-provider fallback.
- **Live Web Search Grounding** — Automatically detects factual or trivia questions and queries DuckDuckGo (`ddgs`) to ground answers with real-time web facts.
- **Dry-Run Mode (`--dry-run`)** — Ticks and fills all options visually in the browser without clicking final "Submit".
- **Structured Audit Logging (`main.py`)** — Outputs machine-readable JSON audit logs with full reasoning trails and clean standard logging for background/cron tracking.

---

## Architecture & Code Structure

```
d:/PROJECTS/GOOGLEFORMS/
├── browser_manager.py   # Browser selection, launcher & Google login session manager
├── form_scraper.py      # DOM parser & question/option extractor
├── answer_engine.py     # AI generation engine (Claude/OpenAI/Gemini/Mock + Web Search)
├── form_filler.py       # DOM interaction, field filling, pagination & submission
├── main.py              # CLI entry point, headless orchestration, structured logging
├── requirements.txt     # Python dependencies
├── .env.example         # Template for environment variables and API keys
├── tests/
│   └── test_components.py # End-to-end integration and unit tests
└── README.md            # Documentation
```

---

## Google Account Login (Persistent Browser Profile)

The bot uses your **installed Google Chrome / Microsoft Edge** with a **dedicated persistent browser profile** (`.browser_profile/`). This means it is **not a temporary guest window** — all your Google logins, cookies, passwords, and sessions are saved permanently to your disk across runs!

### Step 1: Sign in Once to Your Google Account
Run the one-time login command to sign into your Google account:
```bash
python main.py --login --browser chrome
```
1. Google Chrome will open on your screen to the Google Login page.
2. Sign in to your Google Account (or your organization/school account with 2FA).
3. Once signed in, return to your terminal and press **[ENTER]**.
4. Your login is now permanently saved to your persistent browser profile!

### Step 2: Run Form Solver (Uses Your Saved Google Account)
Every time you run the bot, it opens with your saved Google account already logged in:
```bash
python main.py "https://docs.google.com/forms/d/e/.../viewform" --browser chrome
```

---

## Running with Different AI Providers

If your Anthropic Claude account credit balance is 0, you can easily use **Google Gemini**, **OpenAI GPT-4o**, or the **Offline Mock Engine**:

```bash
# Using Google Gemini (Free & Fast)
python main.py "FORM_URL" --provider gemini --model gemini-2.5-flash

# Using OpenAI GPT-4o
python main.py "FORM_URL" --provider openai --model gpt-4o

# Using Offline Mock Engine (No API Keys needed)
python main.py "FORM_URL" --provider mock --dry-run
```

---

## Installation & Setup

1. **Clone or navigate to the project directory**:
   ```bash
   cd d:/PROJECTS/GOOGLEFORMS
   ```

2. **Install Python dependencies**:
   ```bash
   pip install -r requirements.txt
   playwright install chromium
   ```

3. **Configure Environment Variables**:
   Copy `.env.example` to `.env` and set your API keys:
   ```bash
   cp .env.example .env
   ```

   Inside `.env`:
   ```ini
   ANTHROPIC_API_KEY=sk-ant-api03-...
   LLM_PROVIDER=claude
   LLM_MODEL=claude-3-5-sonnet-20241022
   ```

---

## Usage

### 1. Basic Headless Execution (Claude API)
```bash
python main.py "https://docs.google.com/forms/d/e/.../viewform"
```

### 2. Dry-Run Mode (Test without submitting)
```bash
python main.py "https://docs.google.com/forms/d/e/.../viewform" --dry-run
```

### 3. Using OpenAI GPT-4o
```bash
python main.py "https://docs.google.com/forms/d/e/.../viewform" --provider openai --model gpt-4o
```

### 4. Using Google Gemini
```bash
python main.py "https://docs.google.com/forms/d/e/.../viewform" --provider gemini --model gemini-2.5-flash
```

### 5. Running with Mock Engine (No API Key required)
```bash
python main.py "https://docs.google.com/forms/d/e/.../viewform" --provider mock --dry-run
```

### 6. Custom Audit and Text Log Files
```bash
python main.py "https://docs.google.com/forms/d/e/.../viewform" --log-file my_run.log --json-log audit_results.json
```

---

## CLI Options Reference

| Flag | Description | Default |
|------|-------------|---------|
| `form_url` or `--url` | Target Google Form URL | Environment variable `GOOGLE_FORM_URL` |
| `--dry-run` | Extract and fill questions without submitting | `False` |
| `--provider` | AI Provider (`claude`, `openai`, `gemini`, `mock`) | `claude` |
| `--model` | Model name override | Provider default |
| `--api-key` | API Key override | Provider env var |
| `--enable-search` | Enable DuckDuckGo web search grounding | `True` |
| `--no-search` | Disable web search grounding | `False` |
| `--headless` / `--no-headless` | Run in headless mode (no UI) or visible UI | `True` (headless) |
| `--log-file` | Path to human-readable log file | `form_runner.log` |
| `--json-log` | Path to structured JSON audit file | `audit_log_<timestamp>.json` |
| `--timeout` | Page navigation timeout (milliseconds) | `30000` |
| `--delay` | Delay between actions (milliseconds) | `200` |
| `-v`, `--verbose` | Enable debug logging | `False` |

---

## Running in the Background / Cron Jobs

This script is designed to run non-interactively in the background without terminal popups.

### Exit Codes
- `0`: Clean success (Form completed and submitted).
- `2`: Dry-run success (Form parsed and filled without submitting).
- `1`: Error encountered (Logged in audit file).

### Linux/macOS Cron Example
Run daily at 09:00 AM in the background:
```cron
0 9 * * * cd /path/to/GOOGLEFORMS && /usr/bin/python3 main.py "https://docs.google.com/forms/d/e/.../viewform" >> /var/log/forms_cron.log 2>&1
```

### Windows Task Scheduler / Background PowerShell
```powershell
Start-Process -NoNewWindow python -ArgumentList "main.py 'https://docs.google.com/forms/d/e/.../viewform' --log-file bg_run.log"
```

---

## Audit Log Format

Every run produces an audit file (`audit_log_<timestamp>.json`) detailing all extraction and generation steps:

```json
{
  "metadata": {
    "started_at": "2026-08-26T12:00:00Z",
    "completed_at": "2026-08-26T12:00:04Z",
    "duration_seconds": 4.12,
    "form_url": "https://docs.google.com/forms/...",
    "dry_run": false,
    "engine": "ClaudeAnswerEngine"
  },
  "form_details": {
    "title": "Quarterly Developer Feedback",
    "description": "Please fill out this survey."
  },
  "pages": [
    {
      "page_number": 1,
      "questions_count": 2,
      "questions": [
        {
          "index": 0,
          "text": "What is Python?",
          "type": "multiple_choice",
          "options": [
            {"index": 0, "key": "A", "text": "A programming language"},
            {"index": 1, "key": "B", "text": "A snake only"}
          ]
        }
      ],
      "answers": [
        {
          "question_index": 0,
          "chosen_answer": "A programming language",
          "option_key": "A",
          "reasoning": "Python is a widely used high-level programming language.",
          "search_performed": false
        }
      ],
      "fill_results": [
        {
          "question_index": 0,
          "status": "filled_radio_success"
        }
      ]
    }
  ],
  "summary": {
    "total_pages": 1,
    "total_questions": 2,
    "answered_questions": 2,
    "submission_status": "submitted_successfully"
  }
}
```

---

## Running Tests

Run the test suite offline using Pytest:
```bash
pytest tests/test_components.py -v
```
