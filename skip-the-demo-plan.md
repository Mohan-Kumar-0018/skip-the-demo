# SkipTheDemo — Technical Implementation Plan

> **One-liner:** "QA marks a ticket done. Before the PM even opens their laptop, they already have a demo video, a design accuracy score, and release notes — written and delivered by AI."

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [System Architecture](#2-system-architecture)
3. [Tech Stack](#3-tech-stack)
4. [File Structure](#4-file-structure)
5. [Database Schema](#5-database-schema)
6. [Environment Variables](#6-environment-variables)
7. [API Contract](#7-api-contract)
8. [Backend Tasks — Person A](#8-backend-tasks--person-a)
9. [UI & Agent Tasks — Person B](#9-ui--agent-tasks--person-b)
10. [Lovable UI Specification](#10-lovable-ui-specification)
11. [Claude Prompts](#11-claude-prompts)
12. [Day-by-Day Schedule](#12-day-by-day-schedule)
13. [Demo Script](#13-demo-script)
14. [Setup & Run](#14-setup--run)

---

## 1. Project Overview

SkipTheDemo is a multi-agent AI pipeline triggered the moment a QA engineer marks a Jira ticket as **"Ready for Review"**.

**What happens automatically — with zero human effort:**

1. Fetches the Jira ticket (title, description, staging URL, PRD PDF, design PNG)
2. Parses the PRD PDF to extract acceptance criteria and user flows
3. Opens the staging app with a browser agent and crawls every flow
4. Records the entire session as a demo video
5. Compares screenshots against the original Figma/design file using Claude Vision
6. Generates a design accuracy score (0–100) with specific deviations called out
7. Writes a PM summary and release notes in plain product language
8. Delivers the complete briefing package to Slack
9. Updates the Jira ticket with a comment confirming delivery
10. Stores all results in Postgres for historical review

---

## 2. System Architecture

```
┌─────────────────────────────────────────────────────────┐
│                  LOVABLE UI                             │
│   Screen 1: Input  →  Screen 2: Progress  →  Screen 3: Results │
│                  Screen 4: History                      │
└─────────────────────┬───────────────────────────────────┘
                      │ POST /run { ticket_id }
                      ▼
┌─────────────────────────────────────────────────────────┐
│              FASTAPI BACKEND — Orchestrator             │
└──────┬──────────────┬───────────────┬───────────────────┘
       │              │               │
       ▼              ▼               ▼
┌──────────┐  ┌──────────────┐  ┌────────────────┐
│Jira Agent│  │Browser Agent │  │Vision Agent    │
│          │  │(Playwright)  │  │(Claude Vision) │
│-ticket   │  │-crawl app    │  │-compare design │
│-PRD PDF  │  │-record video │  │-score accuracy │
│-design   │  │-screenshots  │  │-find deviations│
│-stage URL│  └──────────────┘  └────────────────┘
└──────────┘
       │              │               │
       └──────────────┴───────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────┐
│           Claude Synthesis Agent                        │
│     PRD Analysis + PM Summary + Release Notes          │
└─────────────────────────────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────┐
│                Slack Delivery Agent                     │
│         Video + Score + Summary + Release Notes        │
└─────────────────────────────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────┐
│                   POSTGRES DATABASE                     │
│        runs / run_steps / run_results tables           │
└─────────────────────────────────────────────────────────┘
```

---

## 3. Tech Stack

| Layer | Tool | Purpose |
|---|---|---|
| UI | Lovable | 4-screen frontend |
| Backend | FastAPI (Python) | REST API + async orchestration |
| Jira | Atlassian MCP + REST API | Fetch ticket, PRD, design |
| Browser | Playwright | Crawl staging, record video |
| AI Vision | Claude Sonnet (`claude-sonnet-4-6`) | Design vs reality comparison |
| AI Brain | Claude Sonnet (`claude-sonnet-4-6`) | PRD parse, summary, release notes |
| PDF Parse | PyMuPDF (`fitz`) | Extract PRD text from PDF |
| Database | Postgres | Persist all runs and results |
| Delivery | Slack API (`slack-sdk`) | PM briefing package |
| Files | Local `/outputs` directory | Video (.webm) + screenshots (.png) |

---

## 4. File Structure

```
skipdemo/
├── SKIPDEMO_PLAN.md          ← this file
├── .env                      ← secrets (never commit)
├── .env.example              ← template
├── requirements.txt
├── outputs/                  ← auto-created, videos + screenshots per run
│
└── backend/
    ├── main.py               ← FastAPI app + all routes
    ├── orchestrator.py       ← master pipeline controller
    │
    ├── db/
    │   ├── connection.py     ← Postgres connection pool
    │   ├── models.py         ← all DB read/write functions
    │   └── schema.sql        ← run once to create tables
    │
    ├── agents/
    │   ├── jira_agent.py     ← fetch ticket + attachments + update status
    │   ├── browser_agent.py  ← Playwright crawler + video recorder
    │   ├── vision_agent.py   ← Claude Vision design comparator
    │   ├── synthesis_agent.py← Claude writer (summary + release notes)
    │   └── slack_agent.py    ← Slack delivery
    │
    └── utils/
        └── pdf_parser.py     ← PyMuPDF wrapper
```

---

## 5. Database Schema

Run this file once: `psql $DATABASE_URL -f backend/db/schema.sql`

```sql
-- backend/db/schema.sql

CREATE TABLE IF NOT EXISTS runs (
    id              VARCHAR(8)   PRIMARY KEY,
    ticket_id       VARCHAR(50)  NOT NULL,
    feature_name    VARCHAR(255),
    status          VARCHAR(50)  DEFAULT 'running',  -- running | completed | failed
    stage           VARCHAR(255),
    progress        INTEGER      DEFAULT 0,
    created_at      TIMESTAMP    DEFAULT NOW(),
    completed_at    TIMESTAMP
);

CREATE TABLE IF NOT EXISTS run_steps (
    id              SERIAL       PRIMARY KEY,
    run_id          VARCHAR(8)   REFERENCES runs(id),
    step_name       VARCHAR(100),
    step_status     VARCHAR(50),  -- pending | running | done | failed
    updated_at      TIMESTAMP    DEFAULT NOW(),
    UNIQUE(run_id, step_name)
);

CREATE TABLE IF NOT EXISTS run_results (
    id              SERIAL       PRIMARY KEY,
    run_id          VARCHAR(8)   REFERENCES runs(id) UNIQUE,
    design_score    INTEGER,
    deviations      JSONB,
    summary         TEXT,
    release_notes   TEXT,
    video_path      VARCHAR(500),
    screenshots     JSONB,
    slack_sent      BOOLEAN      DEFAULT FALSE,
    created_at      TIMESTAMP    DEFAULT NOW()
);
```

---

## 6. Environment Variables

```bash
# .env.example — copy to .env and fill in values

# Jira
JIRA_HOST=yourcompany.atlassian.net
JIRA_EMAIL=you@company.com
JIRA_API_TOKEN=your_jira_api_token
JIRA_STAGING_URL_FIELD=customfield_10100   # custom field ID for staging URL

# Anthropic
ANTHROPIC_API_KEY=sk-ant-...

# Slack
SLACK_BOT_TOKEN=xoxb-...
SLACK_CHANNEL=#skipdemo-pm

# Postgres
DATABASE_URL=postgresql://postgres:skipdemo@localhost:5432/skipdemo
```

---

## 7. API Contract

These are the endpoints Person A builds and Person B consumes.

### `POST /run`
Triggers the pipeline for a Jira ticket.
```json
// Request
{ "ticket_id": "PROJ-123" }

// Response
{ "job_id": "abc12345" }
```

### `GET /status/{job_id}`
Person B polls this every 2 seconds to update the progress screen.
```json
{
  "stage": "Exploring staging app...",
  "progress": 45,
  "status": "running",
  "steps": {
    "jira_fetch":     "done",
    "prd_parse":      "done",
    "browser_crawl":  "running",
    "design_compare": "pending",
    "synthesis":      "pending",
    "slack_delivery": "pending"
  }
}
```

### `GET /results/{job_id}`
Reads completed run results for the results screen.
```json
{
  "id": "abc12345",
  "ticket_id": "PROJ-123",
  "feature_name": "Checkout Flow v2",
  "status": "completed",
  "created_at": "2026-02-20T10:30:00",
  "design_score": 88,
  "deviations": [
    { "type": "visual", "description": "CTA button color changed from purple to blue", "severity": "low" },
    { "type": "flow",   "description": "Step 3 confirmation screen was removed",        "severity": "medium" },
    { "type": "new",    "description": "Phone number field added to checkout form",      "severity": "low" }
  ],
  "summary": "The checkout flow v2 was built successfully...",
  "release_notes": "## Checkout Flow v2\n\n...",
  "video_url": "/outputs/abc12345/demo.webm",
  "screenshots": ["/outputs/abc12345/screen_1.png"],
  "slack_sent": true
}
```

### `GET /history`
Returns all past runs for the history screen.
```json
{
  "runs": [
    {
      "id": "abc12345",
      "ticket_id": "PROJ-123",
      "feature_name": "Checkout Flow v2",
      "status": "completed",
      "design_score": 88,
      "created_at": "2026-02-20T10:30:00",
      "slack_sent": true
    }
  ]
}
```

### `GET /history/{job_id}`
Returns full detail for a specific past run (same shape as `/results/{job_id}`).

---

## 8. Backend Tasks — Person A

Work in `backend/`. All code is Python. Run with `uvicorn main:app --reload`.

---

### TASK A-1 — Project Setup
**File:** root

```bash
# Create and activate virtual environment
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate

# Install all dependencies
pip install fastapi uvicorn playwright pymupdf anthropic \
            slack-sdk requests python-dotenv psycopg2-binary

# Install Playwright browsers
playwright install chromium

# Create output directory
mkdir -p outputs
```

Create `requirements.txt`:
```
fastapi
uvicorn
playwright
pymupdf
anthropic
slack-sdk
requests
python-dotenv
psycopg2-binary
```

---

### TASK A-2 — Postgres Setup
**File:** `backend/db/`

1. Start Postgres with Docker:
```bash
docker run --name skipdemo-db \
  -e POSTGRES_PASSWORD=skipdemo \
  -e POSTGRES_DB=skipdemo \
  -p 5432:5432 -d postgres
```

2. Run schema:
```bash
psql postgresql://postgres:skipdemo@localhost:5432/skipdemo \
  -f backend/db/schema.sql
```

3. Create `backend/db/connection.py`:
```python
import psycopg2
from psycopg2.extras import RealDictCursor
import os

def get_conn():
    return psycopg2.connect(
        os.getenv("DATABASE_URL"),
        cursor_factory=RealDictCursor
    )
```

4. Create `backend/db/models.py` with these functions:

```python
from db.connection import get_conn
import json

# ── RUNS ──────────────────────────────────

def create_run(run_id, ticket_id):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO runs (id, ticket_id, status, stage, progress)
                VALUES (%s, %s, 'running', 'Starting...', 0)
            """, (run_id, ticket_id))
        conn.commit()

def update_run(run_id, stage, progress, status="running", feature_name=None):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE runs
                SET stage=%s, progress=%s, status=%s,
                    feature_name=COALESCE(%s, feature_name)
                WHERE id=%s
            """, (stage, progress, status, feature_name, run_id))
        conn.commit()

def complete_run(run_id):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE runs
                SET status='completed', progress=100, completed_at=NOW()
                WHERE id=%s
            """, (run_id,))
        conn.commit()

def fail_run(run_id, error):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE runs SET status='failed', stage=%s WHERE id=%s
            """, (f"Error: {error}", run_id))
        conn.commit()

def get_run(run_id):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM runs WHERE id=%s", (run_id,))
            return cur.fetchone()

def get_all_runs():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT r.*, rr.design_score, rr.slack_sent
                FROM runs r
                LEFT JOIN run_results rr ON r.id = rr.run_id
                ORDER BY r.created_at DESC
            """)
            return cur.fetchall()

# ── STEPS ─────────────────────────────────

def upsert_step(run_id, step_name, step_status):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO run_steps (run_id, step_name, step_status, updated_at)
                VALUES (%s, %s, %s, NOW())
                ON CONFLICT (run_id, step_name)
                DO UPDATE SET step_status=%s, updated_at=NOW()
            """, (run_id, step_name, step_status, step_status))
        conn.commit()

def get_steps(run_id):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT step_name, step_status FROM run_steps
                WHERE run_id=%s ORDER BY id
            """, (run_id,))
            rows = cur.fetchall()
            return {r["step_name"]: r["step_status"] for r in rows}

# ── RESULTS ───────────────────────────────

def save_results(run_id, results):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO run_results
                  (run_id, design_score, deviations, summary,
                   release_notes, video_path, screenshots, slack_sent)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (run_id) DO UPDATE SET
                    design_score  = EXCLUDED.design_score,
                    deviations    = EXCLUDED.deviations,
                    summary       = EXCLUDED.summary,
                    release_notes = EXCLUDED.release_notes,
                    video_path    = EXCLUDED.video_path,
                    screenshots   = EXCLUDED.screenshots,
                    slack_sent    = EXCLUDED.slack_sent
            """, (
                run_id,
                results["design_score"],
                json.dumps(results["deviations"]),
                results["summary"],
                results["release_notes"],
                results["video_path"],
                json.dumps(results["screenshots"]),
                results["slack_sent"]
            ))
        conn.commit()

def get_results(run_id):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT r.*, rr.*
                FROM runs r
                LEFT JOIN run_results rr ON r.id = rr.run_id
                WHERE r.id=%s
            """, (run_id,))
            return cur.fetchone()
```

---

### TASK A-3 — Jira Agent
**File:** `backend/agents/jira_agent.py`

```python
import requests
import os

JIRA_HOST  = os.getenv("JIRA_HOST")
JIRA_EMAIL = os.getenv("JIRA_EMAIL")
JIRA_TOKEN = os.getenv("JIRA_API_TOKEN")

def get_ticket(ticket_id: str) -> dict:
    url  = f"https://{JIRA_HOST}/rest/api/3/issue/{ticket_id}"
    auth = (JIRA_EMAIL, JIRA_TOKEN)
    res  = requests.get(url, auth=auth).json()
    f    = res["fields"]
    return {
        "title":       f["summary"],
        "description": f.get("description", ""),
        "staging_url": f.get(os.getenv("JIRA_STAGING_URL_FIELD"), ""),
        "attachments": f.get("attachment", [])
    }

def get_attachment_bytes(attachment: dict) -> bytes:
    return requests.get(
        attachment["content"],
        auth=(JIRA_EMAIL, JIRA_TOKEN)
    ).content

def get_prd_and_design(attachments: list):
    """Returns (prd_bytes, design_bytes) — either may be None."""
    prd    = None
    design = None
    for att in attachments:
        name    = att["filename"].lower()
        content = get_attachment_bytes(att)
        if name.endswith(".pdf"):
            if "design" in name:
                design = content
            else:
                prd = content          # treat any other PDF as PRD
        elif name.endswith((".png", ".jpg", ".jpeg")):
            design = content
    return prd, design

def add_comment(ticket_id: str, text: str):
    url  = f"https://{JIRA_HOST}/rest/api/3/issue/{ticket_id}/comment"
    auth = (JIRA_EMAIL, JIRA_TOKEN)
    payload = {
        "body": {
            "type": "doc", "version": 1,
            "content": [{"type": "paragraph",
                         "content": [{"type": "text", "text": text}]}]
        }
    }
    requests.post(url, json=payload, auth=auth)
```

---

### TASK A-4 — PDF Parser
**File:** `backend/utils/pdf_parser.py`

```python
import fitz  # PyMuPDF

def extract_text(pdf_bytes: bytes) -> str:
    doc  = fitz.open(stream=pdf_bytes, filetype="pdf")
    text = ""
    for page in doc:
        text += page.get_text()
    return text.strip()
```

---

### TASK A-5 — Browser Agent
**File:** `backend/agents/browser_agent.py`

```python
from playwright.async_api import async_playwright
import os

async def explore_and_record(staging_url: str, job_id: str):
    output_dir = f"outputs/{job_id}"
    os.makedirs(output_dir, exist_ok=True)
    screenshots = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            record_video_dir=output_dir,
            record_video_size={"width": 1280, "height": 720},
            viewport={"width": 1280, "height": 720}
        )
        page = await context.new_page()
        await page.goto(staging_url, wait_until="networkidle")

        # Screenshot: initial state
        s1 = f"{output_dir}/screen_1.png"
        await page.screenshot(path=s1, full_page=True)
        screenshots.append(s1)

        # Click through interactive elements
        selectors = ["button", "a[href]", "input[type=submit]", "[role=button]"]
        clicked = 0
        for selector in selectors:
            elements = await page.query_selector_all(selector)
            for el in elements:
                if clicked >= 8:
                    break
                try:
                    await el.scroll_into_view_if_needed()
                    await el.click()
                    await page.wait_for_load_state("networkidle")
                    path = f"{output_dir}/screen_{clicked + 2}.png"
                    await page.screenshot(path=path, full_page=True)
                    screenshots.append(path)
                    clicked += 1
                    await page.go_back()
                    await page.wait_for_load_state("networkidle")
                except Exception:
                    continue

        await context.close()
        await browser.close()

    # Find recorded video
    video_files = [f for f in os.listdir(output_dir) if f.endswith(".webm")]
    video_path  = f"{output_dir}/{video_files[0]}" if video_files else None

    return screenshots, video_path
```

---

### TASK A-6 — FastAPI Main
**File:** `backend/main.py`

```python
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import asyncio, uuid
from orchestrator import run_pipeline
from db.models import get_run, get_steps, get_results, get_all_runs, create_run

app = FastAPI(title="SkipTheDemo API")

app.add_middleware(CORSMiddleware,
    allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

app.mount("/outputs", StaticFiles(directory="outputs"), name="outputs")

@app.post("/run")
async def run(body: dict):
    job_id = str(uuid.uuid4())[:8]
    create_run(job_id, body["ticket_id"])
    asyncio.create_task(run_pipeline(job_id, body["ticket_id"]))
    return {"job_id": job_id}

@app.get("/status/{job_id}")
def status(job_id: str):
    run   = get_run(job_id)
    steps = get_steps(job_id)
    return {**dict(run), "steps": steps}

@app.get("/results/{job_id}")
def results(job_id: str):
    return get_results(job_id)

@app.get("/history")
def history():
    return {"runs": [dict(r) for r in get_all_runs()]}

@app.get("/history/{job_id}")
def history_detail(job_id: str):
    return get_results(job_id)
```

---

### TASK A-7 — Orchestrator
**File:** `backend/orchestrator.py`

```python
from db.models import update_run, upsert_step, complete_run, fail_run, save_results
from agents.jira_agent    import get_ticket, get_prd_and_design, add_comment
from agents.browser_agent import explore_and_record
from agents.vision_agent  import compare_design_vs_reality
from agents.synthesis_agent import generate_pm_summary
from agents.slack_agent   import send_pm_briefing
from utils.pdf_parser     import extract_text

STEPS = [
    "jira_fetch", "prd_parse", "browser_crawl",
    "design_compare", "synthesis", "slack_delivery"
]

def step(run_id, label, progress, name, status, feature_name=None):
    update_run(run_id, label, progress, feature_name=feature_name)
    upsert_step(run_id, name, status)

async def run_pipeline(run_id: str, ticket_id: str):
    try:
        # 1. Jira
        step(run_id, "Fetching Jira ticket...", 10, "jira_fetch", "running")
        ticket = get_ticket(ticket_id)
        prd_bytes, design_bytes = get_prd_and_design(ticket["attachments"])
        step(run_id, "Jira ticket fetched", 20, "jira_fetch", "done",
             feature_name=ticket["title"])

        # 2. PRD
        step(run_id, "Reading PRD...", 25, "prd_parse", "running")
        prd_text = extract_text(prd_bytes) if prd_bytes else ticket["description"]
        step(run_id, "PRD parsed", 30, "prd_parse", "done")

        # 3. Browser
        step(run_id, "Exploring staging app...", 35, "browser_crawl", "running")
        screenshots, video_path = await explore_and_record(
            ticket["staging_url"], run_id)
        step(run_id, "Staging app recorded", 55, "browser_crawl", "done")

        # 4. Vision
        step(run_id, "Comparing design vs reality...", 60, "design_compare", "running")
        design_result = compare_design_vs_reality(design_bytes, screenshots) \
            if design_bytes else {"score": 0, "deviations": [],
                                  "summary": "No design file attached"}
        step(run_id, "Design comparison complete", 70, "design_compare", "done")

        # 5. Synthesis
        step(run_id, "Writing PM summary and release notes...", 75, "synthesis", "running")
        content = generate_pm_summary(ticket["title"], prd_text, design_result)
        step(run_id, "Content generated", 85, "synthesis", "done")

        # 6. Slack
        step(run_id, "Sending PM briefing to Slack...", 88, "slack_delivery", "running")
        results = {
            "feature_name":  ticket["title"],
            "design_score":  design_result["score"],
            "deviations":    design_result["deviations"],
            "summary":       content["summary"],
            "release_notes": content["release_notes"],
            "video_path":    video_path,
            "screenshots":   screenshots,
            "slack_sent":    False
        }
        send_pm_briefing(results)
        results["slack_sent"] = True
        step(run_id, "Slack briefing sent", 95, "slack_delivery", "done")

        # Save + close
        save_results(run_id, results)
        add_comment(ticket_id, "✅ SkipTheDemo briefing delivered to PM via Slack.")
        complete_run(run_id)

    except Exception as e:
        fail_run(run_id, str(e))
        raise
```

---

## 9. UI & Agent Tasks — Person B

---

### TASK B-1 — Claude Vision Agent
**File:** `backend/agents/vision_agent.py`

```python
import anthropic, base64, json

client = anthropic.Anthropic()

def _b64(path: str) -> str:
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()

def _b64_bytes(data: bytes) -> str:
    return base64.b64encode(data).decode()

def compare_design_vs_reality(design_bytes: bytes, screenshots: list) -> dict:
    design_b64 = _b64_bytes(design_bytes)
    actual_b64 = _b64(screenshots[0])

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1500,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image",
                 "source": {"type": "base64", "media_type": "image/png", "data": design_b64}},
                {"type": "image",
                 "source": {"type": "base64", "media_type": "image/png", "data": actual_b64}},
                {"type": "text", "text": """
First image = original design. Second image = actual built feature.

Compare them carefully. Return ONLY valid JSON — no markdown, no explanation:
{
  "score": <integer 0-100, how closely built feature matches design>,
  "deviations": [
    {
      "type": "visual | flow | missing | new",
      "description": "specific human-readable difference",
      "severity": "low | medium | high"
    }
  ],
  "summary": "One sentence overall assessment."
}
"""}
            ]
        }]
    )

    text  = response.content[0].text
    clean = text.replace("```json", "").replace("```", "").strip()
    return json.loads(clean)
```

---

### TASK B-2 — Synthesis Agent
**File:** `backend/agents/synthesis_agent.py`

```python
import anthropic, json

client = anthropic.Anthropic()

def generate_pm_summary(feature_name: str, prd_text: str, design_result: dict) -> dict:
    deviations = "\n".join(
        f"- [{d['severity'].upper()}] {d['description']}"
        for d in design_result.get("deviations", [])
    )

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1200,
        messages=[{
            "role": "user",
            "content": f"""
You are writing an automated PM briefing for a completed software feature.

Feature name: {feature_name}
Design accuracy score: {design_result['score']}/100
Deviations from design:
{deviations if deviations else "None — feature matches design perfectly."}

PRD (first 3000 chars):
{prd_text[:3000]}

Write two things:

1. SUMMARY — 3-4 sentences in plain product language for a PM.
   Mention what was built, the design score, and highlight any significant deviations.
   Do NOT use engineering jargon.

2. RELEASE NOTES — Professional, user-facing release notes in markdown.
   Use ## heading with the feature name, then bullet points.
   Write for an end user, not an engineer. Keep it punchy and positive.

Return ONLY valid JSON — no markdown fences:
{{
  "summary": "...",
  "release_notes": "..."
}}
"""
        }]
    )

    text  = response.content[0].text
    clean = text.replace("```json", "").replace("```", "").strip()
    return json.loads(clean)
```

---

### TASK B-3 — Slack Agent
**File:** `backend/agents/slack_agent.py`

```python
from slack_sdk import WebClient
import os

client  = WebClient(token=os.getenv("SLACK_BOT_TOKEN"))
CHANNEL = os.getenv("SLACK_CHANNEL", "#skipdemo-pm")

def send_pm_briefing(results: dict):
    score = results["design_score"]
    emoji = "🟢" if score >= 90 else "🟡" if score >= 75 else "🔴"

    devs = "\n".join(
        f"  ⚠️ {d['description']}" for d in results["deviations"]
    ) or "  ✅ No deviations — feature matches design."

    message = f"""
🚀 *SkipTheDemo — PM Briefing Ready*

*Feature:* {results['feature_name']}

{emoji} *Design Accuracy: {score}/100*
{devs}

📋 *Summary*
{results['summary']}

📝 *Release Notes*
{results['release_notes']}

📹 Demo recording attached in thread.
    """.strip()

    res = client.chat_postMessage(channel=CHANNEL, text=message)
    ts  = res["ts"]

    if results.get("video_path"):
        client.files_upload_v2(
            channel=CHANNEL,
            thread_ts=ts,
            file=results["video_path"],
            title=f"{results['feature_name']} — Demo Recording"
        )
```

---

### TASK B-4 — Lovable UI
See full specification in [Section 10](#10-lovable-ui-specification).

**Backend URL to configure in Lovable:**
```
VITE_API_URL=http://localhost:8000
```

**Polling logic (Screen 2):**
```js
// Poll /status/{job_id} every 2 seconds while status === "running"
const poll = setInterval(async () => {
  const res  = await fetch(`${API_URL}/status/${jobId}`)
  const data = await res.json()
  setStatus(data)
  if (data.status !== "running") {
    clearInterval(poll)
    if (data.status === "completed") fetchResults()
  }
}, 2000)
```

---

## 10. Lovable UI Specification

Paste this prompt into Lovable:

```
Build a clean, modern web app called SkipTheDemo.

Dark theme. Minimal. Professional. Purple (#7C3AED) as primary accent.
Font: Inter. No gradients. Clean sharp cards.

── SCREEN 1: Input ─────────────────────────────────────────
- Top left: "⚡ SkipTheDemo" logo in white, bold
- Top right: "History" button — ghost button, purple border
- Center content:
    - Heading: "Skip the demo call."
    - Subheading (grey): "Enter a Jira ticket ID. AI does the rest."
    - Large input field with placeholder "PROJ-123"
    - Large purple CTA button: "Run SkipTheDemo →"
    - Small text below: "Fetches PRD, crawls staging, records demo, briefs your PM. Automatically."

── SCREEN 2: Progress ──────────────────────────────────────
- Header: "⚡ Running..." (animated pulse on the lightning bolt)
- Ticket ID shown as subtitle: "PROJ-123"
- Purple animated progress bar (0–100%)
- 6 step checklist — each row has:
    - Icon: ⏳ pending | 🔄 spinner running | ✅ done | ❌ failed
    - Step name
  Steps in order:
    1. Fetching Jira ticket
    2. Reading PRD
    3. Exploring staging app
    4. Comparing with design
    5. Writing release notes
    6. Sending to PM
- Current action text below bar in grey italic
- Polls GET /status/{job_id} every 2 seconds
- Auto-navigates to Screen 3 when status === "completed"

── SCREEN 3: Results ───────────────────────────────────────
- Header: "✅ Done. PM has been briefed."
- Subtitle: feature name from results

- Design Accuracy Card (large, center):
    - Big number: "88" with "/100"
    - Color: green ≥90, yellow ≥75, red <75
    - Label: "Design Accuracy Score"

- Deviations list (if any):
    - Each row: ⚠️ icon + deviation description + severity badge
    - Severity badge colors: low=grey, medium=orange, high=red

- Two expandable accordion cards:
    - "📋 PM Summary" — shows summary text
    - "📝 Release Notes" — shows markdown-rendered release notes

- Video player:
    - Label: "Demo Recording"
    - Plays the video from video_url
    - Controls visible

- Footer badge: "📨 Briefing sent to #skipdemo-pm ✅"

- "← Run Another" button returns to Screen 1

── SCREEN 4: History ───────────────────────────────────────
- Accessible from History button on Screen 1
- Title: "Run History"
- Table columns:
    Ticket | Feature Name | Date | Score | Status | Actions
- Score: colored number (green/yellow/red)
- Status badge: ✅ Completed | ⏳ Running | ❌ Failed
- "View Results" button — opens Screen 3 for that run
- "← Back" button returns to Screen 1
- Auto-refreshes every 10 seconds if any row has status "running"
- Reads from GET /history

── API config ──────────────────────────────────────────────
All API calls go to: process.env.VITE_API_URL (default http://localhost:8000)
```

---

## 11. Claude Prompts

### Vision Agent Prompt (Design vs Reality)
```
First image = original design. Second image = actual built feature.
Compare them carefully. Return ONLY valid JSON — no markdown, no explanation:
{
  "score": <integer 0-100>,
  "deviations": [
    {
      "type": "visual | flow | missing | new",
      "description": "specific human-readable difference",
      "severity": "low | medium | high"
    }
  ],
  "summary": "One sentence overall assessment."
}
```

### Synthesis Agent Prompt (PM Summary + Release Notes)
```
You are writing an automated PM briefing for a completed software feature.
Feature name: {feature_name}
Design accuracy score: {score}/100
Deviations from design: {deviations}
PRD: {prd_text[:3000]}

Write two things:
1. SUMMARY — 3-4 sentences in plain product language for a PM.
2. RELEASE NOTES — Professional, user-facing release notes in markdown.

Return ONLY valid JSON:
{ "summary": "...", "release_notes": "..." }
```

---

## 12. Day-by-Day Schedule

### Day 1

| Hour | Person A (Backend) | Person B (UI + Agents) |
|---|---|---|
| 1 | Setup repo, venv, FastAPI, .env, Postgres | Lovable: build Screen 1 + Screen 2 with dummy data |
| 2 | TASK A-3: Jira agent — fetch ticket + attachments | TASK B-1: Vision agent — Claude Vision comparator |
| 3 | TASK A-4: PDF parser + TASK A-5: Browser agent crawling | TASK B-2: Synthesis agent — summary + release notes |
| 4 | Browser agent: video recording working, screenshots saving | TASK B-3: Slack agent — message + video upload |
| 5 | TASK A-7: Orchestrator — wire steps 1, 2, 3 | Lovable: build Screen 3 results UI |
| 6 | FastAPI routes live: /run /status /results /history | Wire Lovable to real backend, test polling |
| 7 | Integration: full pipeline end-to-end in terminal | Fix whatever is broken |

**Day 1 goal:** Trigger pipeline → Slack message arrives with score, summary, release notes.

---

### Day 2

| Hour | Person A | Person B |
|---|---|---|
| 1 | Stability: run pipeline 5x, fix crashes | Polish: Screen 4 history, animations, loading states |
| 2 | Error handling: no design file, no video, bad URL | Polish: Slack message formatting, results screen |
| 3 | Demo data: create perfect Jira ticket for demo | Polish: video player, deviation badges |
| 4 | Rehearse demo end-to-end | Prepare 3 presentation slides |
| 5 | Buffer — fix anything that breaks | Final UI tweaks |
| 6 | Full rehearsal together | Full rehearsal together |

**Day 2 goal:** Demo works reliably 5 times in a row. Story is rehearsed.

---

## 13. Demo Script

**90 seconds. Practice this exactly.**

> *"Let me show you what happens the moment QA marks a ticket done."*

1. Open Screen 1. Type `PROJ-123`. Click **Run SkipTheDemo →**
2. *"The agent just woke up. It's fetching the Jira ticket right now."*
3. Watch progress screen fill live — step by step.
4. *"It's inside the staging app — clicking through every flow, recording everything."*
5. Design score appears — *"88 out of 100. Three things changed during development."*
6. Switch to Slack — the PM briefing has already arrived.
7. Show the message: video, score, deviations, summary, release notes.
8. *"QA touched one button. That's it. The PM already has everything."*

---

### 3 Presentation Slides

**Slide 1 — The Problem**
> Every feature ships with a tax. Demo calls. Documentation. Back-and-forth Slack messages.
> Engineers explain. PMs wait. Everyone loses time.

**Slide 2 — The Pipeline**
Show the architecture diagram. One sentence per step.

**Slide 3 — The One-Liner**
> "QA marks a ticket done. Before the PM even opens their laptop, they already have
> a demo video, a design accuracy score, and release notes — written and delivered by AI."

Then run the live demo.

---

## 14. Setup & Run

### First time setup
```bash
# Clone and enter repo
git clone <repo> && cd skipdemo

# Backend
cd backend
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
playwright install chromium

# Copy env and fill in values
cp .env.example .env

# Start Postgres
docker run --name skipdemo-db \
  -e POSTGRES_PASSWORD=skipdemo \
  -e POSTGRES_DB=skipdemo \
  -p 5432:5432 -d postgres

# Run DB schema
psql postgresql://postgres:skipdemo@localhost:5432/skipdemo \
  -f db/schema.sql

# Start backend
uvicorn main:app --reload --port 8000
```

### Run the demo
```bash
# Trigger a run via curl
curl -X POST http://localhost:8000/run \
  -H "Content-Type: application/json" \
  -d '{"ticket_id": "PROJ-123"}'

# Check status
curl http://localhost:8000/status/<job_id>

# Get results
curl http://localhost:8000/results/<job_id>

# View history
curl http://localhost:8000/history
```

---

## Notes for Claude Code

- All Python files live inside `backend/`
- Always activate the virtual environment before running anything
- The `/outputs` directory is auto-created by the browser agent
- Video files are `.webm` format — served via FastAPI static files at `/outputs/`
- Jira custom field for staging URL must be set in `.env` as `JIRA_STAGING_URL_FIELD`
- If no design file is attached to the Jira ticket, the design score will be 0 and deviations will be empty — handle this gracefully in the UI
- The pipeline is async — `run_pipeline` runs as a background task via `asyncio.create_task`
- All DB writes use `psycopg2` — not an ORM — keep it simple
- Claude model to use everywhere: `claude-sonnet-4-6`
- Never commit `.env` — it contains secrets