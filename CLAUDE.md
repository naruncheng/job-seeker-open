# CLAUDE.md — Job Hunt Automation Pipeline

You are building this project largely unsupervised. Read this whole file before writing code. When in doubt, prefer shipping a working vertical slice over a perfect abstraction.

---

## 1. What we're building

A nightly pipeline that:

1. **Ingests** job listings from aggregator APIs, ATS boards, and a list of target company career pages.
2. **Filters & scores** them against my profile using an LLM, classifying each as local/international and by industry + role type.
3. **Delivers** a daily digest (Discord + email) grouped by those categories.
4. **Generates** cover letters tailored to a specific listing, written in my voice, on demand from the digest.
5. ~~Pre-fills application forms~~ — **dropped (2026-09-20):** I apply by hand. The apply stage was built, then removed; see `DECISIONS.md`.

Goal: I never miss a relevant job, and I spend ~5 minutes a day reviewing and applying.

---

## 2. My profile (fill/adjust `config/profile.md` — this is the source of truth for scoring)

My background, interests, target roles, location and hard excludes are all in
`config/profile.md`; read it there rather than assuming. In short: an
engineering student or graduate looking for graduate programs, entry-level
roles and internships, based in Australia.

**Scoring must read this file at runtime, not hardcode it.** I will edit it to tune results.

---

## 3. Autonomy rules — work without asking me

**Do these freely, no permission needed:**

- Create, restructure, and delete files inside the repo.
- Choose libraries, install dependencies, set up the project layout.
- Write and run tests, run the pipeline end to end against real (read-only) sources.
- Make small schema changes and write the migration.
- Commit after each working increment with a clear message. Don't ask before committing.
- Make reasonable judgment calls on anything unspecified here — record the decision in `DECISIONS.md` (one line: date, decision, why) and move on.
- Fix your own bugs. If a test fails, debug it; don't report the failure back to me as a question.

**Stop and ask me only if:**

- A task requires a credential or account I haven't provided.
- A source requires payment above free tier.
- You'd need to do something that risks my accounts (see §7).
- Two reasonable interpretations of a requirement would lead to very different architectures.

**Never:**

- Submit a job application automatically.
- Message a recruiter, hiring manager, or any human on my behalf.
- Commit secrets. Ever. `.env` is gitignored from commit #1.
- Delete or overwrite anything in `data/` or `inputs/` without an explicit backup.

---

## 4. Stack (use unless you have a strong, recorded reason not to)

- Python 3.11+, `uv` for env/deps.
- SQLite for storage (`data/jobs.db`). Plain SQL or SQLModel, no heavy ORM.
- `python-jobspy` for LinkedIn/Indeed/Glassdoor scraping.
- `httpx` for API calls; `playwright` only where nothing else works (not currently a dependency).
- Anthropic API for scoring and generation — Haiku for bulk filtering/classification, Sonnet for cover letters.
- `pydantic` for all LLM structured output. Validate every response; retry once on parse failure, then log and skip.
- Jinja2 for digest templates.
- Delivery: Discord webhook (primary) + SMTP email (archive/fallback). Read-only digest; no bot.
- Scheduling: GitHub Actions cron for ingest+digest; local CLI for everything else.
- `pytest`, `ruff`, `mypy` (non-strict).

---

## 5. Architecture

```
src/
  ingest/       one module per source, all return list[RawJob]
  normalize/    dedupe, canonicalise, location parsing
  score/        LLM relevance scoring + classification
  digest/       grouping, rendering, delivery
  letters/      style guide extraction + cover letter generation
  db.py  cli.py  config.py
config/
  profile.md          my interest profile (I edit this)
  companies.yaml      target companies + ATS type + board slug
  settings.yaml       thresholds, schedule, delivery prefs
inputs/
  resume.pdf
  cover_letters/      my past letters, any format
data/
  jobs.db
  style_guide.md      generated once, then I tweak it
outputs/
  letters/            generated cover letters
```

Every stage is independently runnable via CLI: `jobs ingest`, `jobs score`, `jobs digest`, `jobs letter <job_id>`. This matters — I want to debug one stage without running all of them.

### Ingest sources, in build order

1. **Aggregators** — Adzuna API (AU), `python-jobspy`. Easy wins, build first.
2. **ATS boards** — public JSON endpoints, no auth, very stable:
   - Greenhouse: `https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true`
   - Lever: `https://api.lever.co/v0/postings/{slug}?mode=json`
   - Ashby: `https://api.ashbyhq.com/posting-api/job-board/{slug}`
   - Workday: per-tenant CXS JSON endpoint (POST, varies — write a helper that discovers it).
   Write one generic client per ATS, driven by `companies.yaml`. Adding a company must be a one-line config change.
3. **SEEK** — no public API; their search returns JSON from an internal endpoint. Low request volume, generous delays, respect `robots.txt`.
4. **Generic fallback** — Playwright fetch + LLM extraction into the `RawJob` schema, for career pages that fit no pattern.

Each source module must fail independently. One dead source never kills the run — log it, mark the source degraded in the digest footer, continue.

### Dedupe

Hash of `normalize(title) + normalize(company) + normalize(location)`. Keep first-seen date. Re-scoring an already-seen job is wasted money — only score new rows.

### Scoring

Batch ~10 jobs per LLM call. Return per job: `relevance` 0–10, `scope` (local/domestic/international), `industry`, `role_type`, `visa_flag`, `reason` (one sentence), `deadline` if present. Threshold in settings, default 7. Store the score — never re-score unless I pass `--force`.

### Cover letters

Two phases:

- **Style guide (once):** read resume + every file in `inputs/cover_letters/`, produce `data/style_guide.md` covering tone, typical sentence length, how I open and close, recurring phrasing, what I emphasise, structural pattern, things I never do. Be specific and quote-backed, not generic advice.
- **Per letter:** fetch the full JD and the company's About/careers copy → select the 2–3 most similar past letters by embedding similarity → prompt with style guide + resume + those examples + JD. Require an explicit requirement→evidence mapping step before drafting, so claims trace to something real on my resume. **Never invent experience, employers, dates, or numbers.** Output markdown + docx to `outputs/letters/`.

### Digest

Grouped: Local (Melbourne) → Domestic (rest of AU) → International. Within each, by industry then role type. Per job: title, company, score, one-line reason, deadline, link, and the short job ID so I can run `jobs letter <id>`. Footer: counts, new sources, any degraded sources. If zero new jobs, still send a one-line "nothing new today" so I know the pipeline is alive.

**Delivery targets, in build order:**

1. **Discord webhook (build this first).** A webhook URL is created in Discord under Server Settings → Integrations → Webhooks; no bot application, no hosting, no persistent process. Post the digest as rich embeds — one embed per group, jobs as fields, colour-coded by score band. Watch the limits: 10 embeds and 6000 characters per message, 25 fields per embed. Split across multiple messages when the digest is long, and always link out rather than dumping the full JD.
2. **SMTP email.** Same content rendered as HTML via Jinja2, sent to `DIGEST_TO_EMAIL`. This is the durable archive and the fallback if Discord errors. Both channels run every night.

No interactive buttons and no bot. The digest is read-only; every action happens at my PC via the CLI. Don't build a Discord bot unless I explicitly ask for one later.

---

## 6. Definition of done for each increment

You may consider a piece finished only when all of these hold:

- It runs end to end from the CLI without a traceback.
- Tests exist and pass (unit tests with recorded fixtures for parsers; one integration test per ingest source hitting the real endpoint, marked `@pytest.mark.network` so it can be skipped).
- `ruff check` and `mypy` are clean.
- Logging exists at each stage boundary — counts in, counts out, duration.
- `README.md` setup steps are still accurate.
- Committed.

Build in this order, each fully done before the next: **ingest+dedupe+raw email → scoring+grouping+Discord digest → style guide+letters.**

---

## 7. Safety and account guardrails

These are hard rules, not preferences:

- **No auto-submit.** There is no apply stage any more — never submit an application on my behalf, by any means.
- **No LinkedIn automation while logged in as me.** Easy Apply bots get accounts banned. Read-only public scraping via jobspy only.
- Rate limit every source: ≥2s between requests, sequential per domain, exponential backoff on 429/5xx, hard cap on requests per run. Set a descriptive User-Agent with my email.
- Respect `robots.txt`. If a site clearly forbids it, skip it and note it in `DECISIONS.md` rather than working around it.
- Cache aggressively so re-runs during development don't re-hit live sites.
- Budget guard: track LLM spend per run, abort the run if a single run would exceed the cap in settings (default $1).

---

## 8. Config and secrets

`.env` (gitignored), documented in `.env.example`:

```
ANTHROPIC_API_KEY=
ADZUNA_APP_ID=
ADZUNA_APP_KEY=
DISCORD_WEBHOOK_URL=
SMTP_HOST= SMTP_USER= SMTP_PASS= DIGEST_TO_EMAIL=
```

If a key is missing, the affected source/feature degrades gracefully with a clear log line — the pipeline does not crash.

---

## 9. How to report back to me

At the end of a work session, give me a short summary: what's now working, what you decided that I might disagree with, what's next, and anything blocked on me. Keep it brief — no walkthrough of the code, I'll read it.

Don't ask me to confirm plans before starting. Start, then tell me what you did.
