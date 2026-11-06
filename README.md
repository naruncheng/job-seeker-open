# Job hunt automation pipeline

## First time here

1. Follow [`SETUP.md`](SETUP.md): install `uv`, run `uv sync --dev`, and copy
   `.env.example` to `.env` with your own API keys (at minimum
   `ANTHROPIC_API_KEY`).
2. Rewrite [`config/profile.md`](config/profile.md) about yourself: it's what
   every job is scored against.
3. In [`config/settings.yaml`](config/settings.yaml), check the job search
   terms and location, and fill in `letters.author_name` and
   `letters.portfolio_url` (blank if you have none).
4. Put your resume in `inputs/` as `resume.pdf` or `resume.docx`, and a few of
   your past cover letters in `inputs/cover_letters/`, then run
   `uv run jobs style-guide` once to learn your writing style.
5. Optional: the nightly Discord/email digest runs on GitHub Actions in your
   own GitHub repo with your own secrets; see `SETUP.md`.

## Your daily routine

The digest arrives by itself every morning (Discord + email, about 5:17am
Melbourne time — 6:17am during daylight saving). Skim it. Most days that's all.

When a job is worth applying to, the easiest way is the web UI:

```powershell
cd path\to\job_seeker
uv run jobs web                   # opens http://127.0.0.1:8765 in your browser; Ctrl+C to stop
```

Click **Refresh jobs**, search for the job, open it, click **Write cover
letter**, then **Copy text** or **Download .docx**. For a job the nightly
search didn't find (LinkedIn post, company site), use **Add a job** and paste
the ad text in. It only runs on your PC; nobody else can reach it.

Or the same thing from the command line:

```powershell
uv run jobs ingest                # 1. pull the latest jobs into your local database (~40s)
uv run jobs letter <job_id>       # 2. draft the cover letter (~1 min, ~10-25 cents)
```

- **`<job_id>`** is the full 16-character `id` shown under each job in the
  digest (e.g. `ed2ca7223898797f`). Copy it exactly.
- **Step 1 is needed** because the nightly run happens on GitHub's servers with
  its own database; your PC only knows about a job once it has ingested it.
  Skip it if you've already ingested since that job appeared.
- **Adzuna jobs only give the first ~500 characters of the ad.** If the letter
  warns about that, copy the full ad from the listing into a text file and run
  `uv run jobs letter <job_id> --jd-file ad.txt` for a better letter.
- The letter is written to `outputs\letters\Cover Letter <Company> <year>.docx`.
  **Read it and edit it** — it's a first draft in your style, and anything
  listed under "Check these against your resume" needs a look. Then apply on
  the employer's own site yourself; nothing here ever submits for you.

Occasionally: after editing `config\profile.md` or `config\settings.yaml`,
`git add`, `git commit` and `git push` so the nightly run picks the change up.
Full setup and troubleshooting are in [`SETUP.md`](SETUP.md).

---

Nightly pipeline that ingests job listings, scores them against a profile,
sends a digest, and drafts cover letters on demand. Applying is left to
you. See `CLAUDE.md` for the full spec and `DECISIONS.md` for judgment
calls made along the way.

**Status: feature-complete** — ingest + dedupe + scoring + grouping +
Discord/email digest + style guide + cover letters. (Form pre-fill was
built and then removed — see `DECISIONS.md`.)

## Setup

**→ For a full step-by-step walkthrough (getting each API key, wiring up
Discord/SMTP, scheduling the nightly run, and what it all costs), see
[`SETUP.md`](SETUP.md).** The short version:

```bash
uv sync --dev
cp .env.example .env   # fill in the secrets you have; missing ones degrade gracefully
```

Edit `config/profile.md` to describe yourself — the scorer reads it at
runtime on every `jobs score` run, so edits take effect immediately. Edit
`config/companies.yaml` to add target company career pages (one line per
company, on a supported ATS). Edit `config/settings.yaml` for thresholds,
rate limits, budget cap, and which ingest sources run.

For cover letters, also drop in:

- `inputs/resume.pdf` — your resume (PDF, DOCX, or plain text/markdown all
  work despite the fixed `.pdf` name in this doc; see Cover letters below).
- `inputs/cover_letters/` — a handful of past cover letters, any of
  `.pdf`/`.docx`/`.txt`/`.md`. The style guide has nothing to learn from
  without at least one.

Both are gitignored — they never leave your machine via this repo.

## Usage

Every stage runs independently from the CLI:

```bash
uv run jobs ingest              # fetch from every enabled source, dedupe, store new jobs
uv run jobs score                # score unscored jobs against config/profile.md via the LLM
uv run jobs score --force        # re-score jobs that already have a score
uv run jobs digest               # send the scored, grouped digest (Discord + email)
uv run jobs style-guide          # generate data/style_guide.md (once)
uv run jobs style-guide --force  # regenerate it (backs up the old one first)
uv run jobs letter <job_id>      # draft a cover letter for a job from the digest
uv run jobs add --title T --company C --url U --clipboard   # save a job you found yourself
uv run jobs web                  # local web UI over all of the above
uv run jobs stats                # print total jobs stored
```

`jobs add` takes the ad text from `--clipboard`, `--description "..."` or
`--jd-file <path>`; with none of them it tries to read it from `--url`
(works for most company career pages, never for LinkedIn/Indeed/SEEK).

Pass `-v` for debug-level logging: `uv run jobs -v ingest`.

## Ingest sources

- **Adzuna** (AU aggregator API) — needs `ADZUNA_APP_ID`/`ADZUNA_APP_KEY`.
- **jobspy** (LinkedIn/Indeed/Glassdoor, read-only, no login) — no
  credentials needed, but scraping these sites is inherently flaky; a
  failure here degrades to zero results rather than failing the run.
- **Greenhouse / Lever / Ashby** — public, no-auth JSON board APIs, driven
  entirely by `config/companies.yaml`. Add a company by adding a `- name:
  ..., ats: greenhouse|lever|ashby, slug: ...` entry — no code changes.

SEEK and Workday are not yet implemented (see `DECISIONS.md`).

Every source degrades independently: a dead source is logged and skipped,
never fatal to the run.

## Dedupe

A job's identity is `sha256(normalize(title) + normalize(company) +
normalize(location))`. Re-running `jobs ingest` against the same listings
just refreshes `last_seen_at`; it doesn't re-insert or re-notify.

## Scoring

`jobs score` batches ~10 unscored jobs per LLM call (Claude Haiku 4.5),
reading `config/profile.md` fresh each run. Each job gets a `relevance`
(0-10), `scope` (local/domestic/international), `industry`, `role_type`, a
one-sentence `reason`, and `visa_flag`/`deadline` when relevant. Responses
are pydantic-validated; a batch that fails validation is retried once, then
logged and skipped (left unscored, so it's picked up next run). A job is
never re-scored unless you pass `--force`. A budget guard tracks estimated
spend and aborts the run if it would exceed `scoring.budget_cap_usd` in
`config/settings.yaml` (default $1) — needs `ANTHROPIC_API_KEY`, otherwise
this stage logs a warning and no-ops.

Note: `$1` is a hard ceiling, not the expected spend. In steady state
(scoring only newly-ingested jobs each night, with the profile+instructions
prompt-cached) a typical night costs a few cents, not the full cap — the cap
only bites on a huge first backfill run or an unusually busy night.

## Digest

`jobs digest` sends every scored, unnotified job at or above
`scoring.threshold` (default 7), grouped Local → Domestic → International,
then by industry, then role type, to **both** channels every run:

- **Discord** (primary) — rich embeds, one per scope group, jobs as fields,
  coloured by score band. Needs `DISCORD_WEBHOOK_URL`. Paginated across
  multiple messages if it would exceed Discord's 10-embeds/6000-char/
  25-fields-per-embed limits.
- **Email** (durable archive/fallback) — same content as grouped HTML via
  SMTP. Needs `SMTP_HOST`/`SMTP_USER`/`SMTP_PASS`/`DIGEST_TO_EMAIL`.

Either channel missing its config just logs a warning and skips — never
crashes. A job is marked notified only if at least one channel actually
sent; if both are unconfigured or fail, it's left for the next run to retry.
Zero qualifying jobs still sends a "nothing new today" message on every
configured channel, so you know the pipeline is alive.

## Cover letters

Two phases, per `config/profile.md`'s companion inputs:

**`jobs style-guide`** reads `inputs/resume.pdf` (any of PDF/DOCX/TXT/MD
despite the fixed filename) and every file in `inputs/cover_letters/`, and
asks Claude Sonnet 5 to write `data/style_guide.md` — a specific,
quote-backed description of how you write (tone, sentence length,
opening/closing pattern, recurring phrasing, structure, what you never do).
It's generated **once**: re-running it without `--force` refuses to touch
an existing guide, and `--force` backs the old one up first
(`style_guide.md.bak.<timestamp>`) before overwriting. **This is meant to
be hand-edited** — open `data/style_guide.md` and correct anything that
reads wrong.

Running `jobs style-guide` is optional — `data/style_guide.md` is just a
plain markdown file, and nothing downstream cares how it was written. If
you'd rather not spend the (small, one-off) API call, paste your resume +
past letters into a regular claude.ai chat, ask for the same thing (tone,
sentence length, opening/closing pattern, recurring phrasing, structure,
things you never do — quote-backed), and save the result to
`data/style_guide.md` yourself. `jobs letter` only checks that the file
exists.

**`jobs letter <job_id>`** drafts a letter for a specific job from the
digest:
1. Picks your 2-3 most similar past letters (local TF-IDF similarity —
   see `DECISIONS.md` for why not a hosted embeddings API).
2. Does an explicit requirement → evidence mapping against your resume
   *before* drafting: for each requirement, the ad's own key terms, your
   evidence (or "not covered"), and that evidence re-worded in the ad's
   vocabulary. It also pulls out any selection criteria the ad asks you to
   address, and specifics about the employer for a "why this role" line.
3. Drafts in your voice — your greeting, sign-off and tone from the style
   guide — but writes the body fresh for the job, using the ad's terms on
   your real evidence and covering every selection criterion in prose.
4. Checks the draft (free, no LLM): which of the ad's terms for things you
   meet made it in, any stock phrases from `letters.avoid_phrases` in
   `config/settings.yaml`, and any 7+ word runs copied from the ad. If
   something's off it does one revision, kept only if it's better.
5. **Fact-checks the finished letter against your resume** and corrects
   anything it doesn't support (a skill attached to the wrong project, a
   changed detail). Anything still doubtful is printed as "Check these
   against your resume before sending". It never invents experience,
   employers, tools, dates or numbers, and uncovered requirements are named
   honestly as things you're keen to develop.
6. Writes `outputs/letters/Cover Letter <Company> <year>.docx` (plus a
   `<job_id>.md` copy the web UI uses). Regenerating a job overwrites its own
   file; a second job at the same company gets ` (2)` rather than
   overwriting. The .docx Author is `letters.author_name` from settings.
   Every letter also invites the reader to your portfolio
   (`letters.portfolio_url`, if set) just before the thank-you; if the draft
   leaves it out, a standard sentence is added. In the .docx it shows without
   the `https://` and is a clickable link.

A letter takes ~1 minute and costs ~$0.10–0.25 (it's up to six Sonnet calls:
mapping, draft, revision, fact-check, correction, re-check).

**If you change your resume or how you write later:**
- **Resume edits apply automatically.** Every `jobs letter` run re-reads
  `inputs/resume.pdf` fresh — nothing about your resume is baked into the
  style guide, so a new version is picked up on the very next letter, no
  regeneration needed.
- **Writing-style changes do not auto-apply.** The style guide is a
  snapshot, not something that silently rewrites itself when you add a new
  file to `inputs/cover_letters/`. If you fundamentally change how you
  write, either hand-edit `data/style_guide.md` directly (fastest), or run
  `jobs style-guide --force` to regenerate it from scratch.
- Which past letters get picked as *examples* for a given letter, though,
  **is** recomputed fresh every time from whatever's currently in
  `inputs/cover_letters/` — so a newly-added sample letter is eligible
  immediately, even without regenerating the guide.

Both commands need `ANTHROPIC_API_KEY` and log estimated spend per call;
missing inputs (no resume, no past letters, no style guide yet) degrade to
a clear skip message rather than a crash.

## Development

```bash
uv run pytest              # unit tests only (fast, no network, no LLM calls)
uv run pytest --run-network  # also hits real read-only endpoints
uv run ruff check .
uv run mypy src
```

Test fixtures under `tests/fixtures/` are small, real samples recorded from
each ATS's public API (except Adzuna's, hand-built from its documented
response shape since it requires a free-tier API key to query live). LLM
calls, Discord/SMTP delivery, and company-page fetches are mocked in tests —
nothing in the test suite spends real API budget, sends a real message, or
hits a real company website.

## Layout

```
src/jobs/
  ingest/       one module per source, all return list[RawJob]
  normalize/    dedupe, location classification
  score/        LLM relevance scoring + classification (schema, budget guard, batching)
  digest/       grouping, Discord embeds, scored HTML email, orchestration
  letters/      file extraction, style guide, similarity search, letter generation
  db.py  cli.py  config.py
config/
  profile.md               your interest profile, read at runtime by the scorer
  companies.yaml            target companies + ATS type + board slug
  settings.yaml             thresholds, rate limits, budget cap, delivery prefs, enabled sources
inputs/
  resume.pdf           your resume (gitignored)
  cover_letters/       your past letters, any format (gitignored)
data/
  jobs.db          SQLite store (gitignored)
  cache/           on-disk HTTP response cache for dev re-runs (gitignored)
  style_guide.md   generated once by `jobs style-guide`, then hand-tweaked (gitignored)
outputs/
  letters/         generated cover letters, .md + .docx (gitignored)
```
