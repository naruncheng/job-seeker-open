# Setup guide

Everything you need to go from a fresh clone to a working nightly pipeline.
Work through it top to bottom — each stage degrades to a clear "skipped:
missing X" message rather than crashing, so you can add credentials one at a
time and watch each piece come alive.

For what the system does and why, see `README.md`. For judgment calls made
while building it, see `DECISIONS.md`.

---

## What it costs to run

All of it is free tier except the Anthropic API.

| Thing | Cost |
|---|---|
| **Anthropic API — scoring** (Haiku 4.5, $1/$5 per 1M tokens) | **~$0.02–0.03 per nightly run** |
| **Anthropic API — cover letters** (Sonnet 5, $2/$10 per 1M tokens) | **~$0.10–0.25 per letter** |
| **Anthropic API — style guide** (Sonnet 5, one-off) | **~$0.02 once** (or $0 if you write it by hand) |
| **First-run backfill** (scoring ~650 existing listings once) | **~$0.50 once** |
| Adzuna API | $0 (free tier; 2 calls/night) |
| jobspy (LinkedIn/Indeed/Glassdoor) | $0 (no account) |
| Greenhouse / Lever / Ashby board APIs | $0 (public, no auth) |
| Discord webhook | $0 |
| Gmail SMTP | $0 |
| GitHub Actions | $0 — this repo is private, so the GitHub Free plan's 2,000 min/month applies; the nightly run uses ~3 min × 30 = **~90 min/month**, about 4.5% of it |
| GitHub Actions cache (for `jobs.db`) | $0 (10 GB limit; the db is a few MB) |

Essentially all the cost is the Anthropic API, paid per run: a few cents
for each nightly scoring run, plus each letter you ask for. You'll need to
load credit on the Anthropic console to start: **$5 is all you need, and it
should last you a while** (roughly a month of nightly runs plus 15 or more
letters, or far longer if you only refresh when you're looking).

Two things keep this cheap by design:

- Scoring only ever looks at **newly ingested** jobs (dedupe means a listing
  is scored once, ever, unless you pass `--force`), and the profile +
  instructions are prompt-cached across batches within a run.
- Letters **only run when you ask for them** — they're never part of the
  nightly job.

There's also a hard stop: `scoring.budget_cap_usd` in
`config/settings.yaml` (default `$1.00`) aborts a run mid-way if estimated
spend would exceed it. It's a circuit breaker, not a target — a normal
night costs a few cents.

---

## Step 1 — Clone and install

```bash
git clone https://github.com/<your-github-username>/job_seeker.git
cd job_seeker
uv sync --dev
```

> **Windows / Python version:** the repo pins Python 3.12 via
> `.python-version`, so `uv` downloads that automatically. Don't let it pick
> 3.14 — some dependencies (`regex`, via `python-jobspy`) have no prebuilt
> wheel there and fail with "Microsoft Visual C++ 14.0 or greater is required".

> **Note:** the code lives on the `claude/claude-md-increment-1-7vpi7p`
> branch, which is the repo's default branch — a fresh clone lands on it
> automatically, and the nightly GitHub Actions schedule fires from it
> correctly. If you ever add a `main` and make *that* the default, move the
> workflow file across with it (see Step 10, Option B).

Check it installed:

```bash
uv run jobs --help
```

You should see the six commands: `ingest`, `score`, `digest`,
`style-guide`, `letter`, `stats`.

---

## Step 2 — Anthropic API key (required for scoring + letters)

1. Go to <https://console.anthropic.com>, sign in.
2. **Billing** → load some credit (the minimum will last you a long time at
   this project's usage).
3. **API keys** → *Create key* → copy it (you only see it once).

```bash
cp .env.example .env
```

Then edit `.env`:

```
ANTHROPIC_API_KEY=sk-ant-...
```

Without this, `jobs score`, `jobs style-guide` and `jobs letter` all log a
warning and no-op. Ingest and digest still work.

---

## Step 3 — Discord webhook (primary digest channel)

No bot, no hosting, no application to register — just a webhook URL.

1. In Discord, use an existing server or create one (free, takes a second —
   the **+** at the bottom of your server list).
2. **Server Settings** → **Integrations** → **Webhooks** → *New Webhook*.
3. Pick the channel the digest should post to, then *Copy Webhook URL*.

```
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
```

---

## Step 4 — Email (durable archive / fallback)

Any SMTP server works. Gmail is the easiest:

1. Your Google Account needs 2-step verification on.
2. Go to <https://myaccount.google.com/apppasswords> and generate an app
   password (a normal account password will **not** work for SMTP).

```
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=you@gmail.com
SMTP_PASS=<the 16-character app password>
DIGEST_TO_EMAIL=you@gmail.com
DIGEST_FROM_EMAIL=you@gmail.com
```

University/work mail often blocks SMTP app passwords — if yours does, send
from Gmail and set `DIGEST_TO_EMAIL` to wherever you actually read mail.

---

## Step 5 — Adzuna (optional)

Skip this and the Adzuna source just logs a warning and is skipped; the ATS
boards and jobspy still run.

1. Register at <https://developer.adzuna.com>.
2. Copy your App ID and App Key from the dashboard.

```
ADZUNA_APP_ID=...
ADZUNA_APP_KEY=...
```

---

## Step 6 — Your contact email for scrapers

A descriptive User-Agent with a contact address is a courtesy to the sites
being read (and a hard rule in `CLAUDE.md` §7):

```
USER_AGENT_EMAIL=you@example.com
```

---

## Step 7 — Drop in your resume and past cover letters

```
inputs/resume.pdf            your resume (PDF, DOCX, TXT or MD all parse)
inputs/cover_letters/        3–5 past letters, any of .pdf/.docx/.txt/.md
```

Both are gitignored and never leave your machine via this repo. The style
guide has nothing to learn from without at least one past letter.

---

## Step 8 — Tune the config

**`config/profile.md`** — the single source of truth for scoring, read fresh
on every `jobs score` run. Edit it freely; no code change or restart needed.
It starts as a template with [bracketed] placeholders, plus sensible hard
excludes (5+ years experience, pure sales, clearance-only roles, unpaid
work). Make it yours — this is the file that decides what you get told about.

**`config/companies.yaml`** — target companies. One line each; no code
changes. To find a company's slug, look at their careers page URL:

| Careers URL looks like | `ats:` | `slug:` |
|---|---|---|
| `job-boards.greenhouse.io/gitlab/...` | `greenhouse` | `gitlab` |
| `jobs.lever.co/acme/...` | `lever` | `acme` |
| `jobs.ashbyhq.com/ramp/...` | `ashby` | `ramp` |

```yaml
  - name: Some Robotics Co
    ats: greenhouse
    slug: somerobotics
```

**`config/settings.yaml`** — `scoring.threshold` (default 7) is the cutoff
for appearing in the digest; `scoring.budget_cap_usd` is the per-run
circuit breaker. Also worth editing: the `ingest.adzuna.what` and
`ingest.jobspy.search_term` keyword strings, which currently hold generic
mechatronics/robotics/automation terms.

---

## Step 9 — First run, one stage at a time

Run these locally and check each one before moving on.

```bash
uv run jobs ingest
```
Expect several hundred jobs on the very first run (it's seeing every open
listing on your configured boards for the first time). Every subsequent run
only reports genuinely new ones. Takes ~20 seconds.

```bash
uv run jobs score
```
The first run is a backfill of everything just ingested — roughly **$0.50**
for ~650 jobs, and a few minutes. If it hits the `$1` cap it stops cleanly
and prints "Stopped early: budget cap reached" — just run it again to
continue where it left off. Nightly runs after this cost cents.

```bash
uv run jobs digest
```
Check Discord and your inbox. The first digest will be large (it's the
whole backfill at once) — it paginates across multiple Discord messages.
Everything it sends is marked notified, so tomorrow's digest only has new
listings.

```bash
uv run jobs style-guide
```
Writes `data/style_guide.md`. **Open it and read it** — correct anything
that doesn't sound like you. It's meant to be hand-edited, and a re-run
won't overwrite your edits without `--force`.

Then try the on-demand command, using the job id from the digest (the full
16-character `id` shown on each job):

```bash
uv run jobs letter ed2ca7223898797f    # writes outputs/letters/<id>.{md,docx}
```

Some sources (Adzuna) only give the first ~500 characters of an ad. If the
letter warns about that, save the full ad as text/docx/pdf and pass it in with
`--jd-file ad.txt`.

Applying is up to you: read the letter, edit it, and apply on the employer's
site yourself.

---

## Step 10 — Automate the nightly run

Pick one of these two. They differ in an important way, explained after.

### Option A — Run it on your own machine

One database, so job ids from the digest always resolve locally with no
extra step before `jobs letter`.

**macOS/Linux** — `crontab -e`, then (use absolute paths; `which uv` to find
yours):

```cron
0 5 * * * cd /Users/you/job_seeker && /Users/you/.local/bin/uv run jobs ingest && /Users/you/.local/bin/uv run jobs score && /Users/you/.local/bin/uv run jobs digest
```

**Windows** — Task Scheduler → *Create Basic Task* → daily at 5am → action
*Start a program* → `uv` with arguments `run jobs ingest`, and repeat for
`score` and `digest` (or wrap all three in a `.bat` file).

Your machine has to be awake at 5am for this to fire.

### Option B — GitHub Actions (runs whether your laptop is on or not)

> This is **GitHub Actions**, not GitHub Pages. (Pages hosts websites;
> Actions runs scheduled jobs in the cloud, which is what we want.)

`.github/workflows/nightly.yml` already exists. It runs ingest → score →
digest every night at 19:17 UTC (5:17am AEST / 6:17am AEDT) — deliberately
not on the hour, because GitHub delays scheduled runs at :00 more than at
odd minutes. Even so, a run can start up to ~30 minutes late.

The cloud run uses **only what's on GitHub**: your code, `config/*.yaml` and
`config/profile.md`, plus the secrets you add below. It never sees your
`.env` file or anything else that's gitignored.

**1. Push your code and config.** Commit anything you've changed (especially
`config/profile.md`, `config/settings.yaml` and `config/companies.yaml`) and
push:

```bash
git add config/profile.md config/settings.yaml config/companies.yaml
git commit -m "Tune profile and search settings"
git push
```

Keep the repo **private** — `profile.md` describes you. Your resume, cover
letters, `.env` and the database are gitignored and never go up. Every time
you edit one of those config files later, commit and push again or the
nightly run keeps using the old version.

GitHub only fires `schedule:` triggers from the repo's **default** branch.
`claude/claude-md-increment-1-7vpi7p` already is the default. If you later add
a `main` and switch the default to it, carry `.github/workflows/nightly.yml`
over, or the cron silently stops firing (no error anywhere).

**2. Allow the actions the workflow uses.** Repo → **Settings** → **Actions**
→ **General**. The workflow uses `actions/checkout`, `actions/cache` (both
made by GitHub) and `astral-sh/setup-uv`, which a restrictive setting blocks
with *"The action … is not allowed"*. Under **Actions permissions** pick one:

- **Allow all actions and reusable workflows** (simplest), or
- **Allow *you*, and select non-*you*, actions** (stricter): tick **Allow
  actions created by GitHub** and add `astral-sh/setup-uv@*` to the list.

Click **Save**. Optionally, under **Workflow permissions** choose **Read
repository contents and packages permissions** — the workflow never writes
to the repo.

**3. Add the secrets.** Repo → **Settings** → **Secrets and variables** →
**Actions** → **New repository secret**, once for each of these. In the
**Name** box put the name exactly as written; in the **Secret** box put just
the value from your local `.env` (the part after the `=`, no quotes):

```
ANTHROPIC_API_KEY      DISCORD_WEBHOOK_URL    SMTP_HOST
ADZUNA_APP_ID          SMTP_PORT              SMTP_USER
ADZUNA_APP_KEY         SMTP_PASS              DIGEST_TO_EMAIL
USER_AGENT_EMAIL       DIGEST_FROM_EMAIL
```

- `SMTP_PORT` is `587` for Gmail. Leaving it blank is fine — it defaults to 587.
- Skip any you're not using; the matching source or channel just skips.
- GitHub never shows a secret again after saving. To change one, add it again
  with the same name.
- A misspelt name is silently ignored (e.g. `ANTROPIC_API_KEY`), so that
  feature just skips. The digest step's log says which is missing.

**4. Run it once to test.** Repo → **Actions** tab → click **Nightly ingest +
digest** in the left sidebar → **Run workflow** (a dropdown above the list of
runs) → the green **Run workflow** button. Open the new run; it should take
a few minutes and the digest arrives in Discord and email.

Use **Run workflow**, *not* **Re-run jobs** inside an old run. Re-run repeats
the *original commit* with today's secrets, so it keeps running old code
after you've pushed a fix. A fresh run's page shows the latest commit id at
the top.

**What to expect on the first run:** the cloud starts with an empty
database, so it ingests, scores and digests everything currently available
(roughly $0.40 of scoring and a big digest, including jobs you may already
have seen locally). After that, each night only new jobs are scored and sent.
The footer lists jobs per source. If `jobspy` shows 0 or very few, LinkedIn
and Indeed are probably blocking GitHub's servers (common for cloud IPs);
Adzuna is API-based and is the reliable source.

**Persistence and starting afresh.** The workflow keeps `data/jobs.db`
between runs via `actions/cache`, so dedupe works across nights. To wipe the
cloud database and start over: **Actions** tab → **Caches** (left sidebar,
under *Management*) → delete the `jobs-db-…` entries. The next run then
treats everything as new. (A cache unused for 7 days is deleted
automatically; a nightly run keeps it fresh. If it's ever lost, old jobs show
up once more and then it settles.)

**Cost and failures.** The run takes a few minutes, about 90 min/month, well
inside GitHub's free allowance for private repos, and the per-run
`scoring.budget_cap_usd` still applies. If a scheduled run fails, GitHub
emails you and the Actions tab shows the log. The "Node.js 20 is deprecated"
warning on a run page is harmless.

### The catch with Option B

The GitHub Actions run has **its own** copy of `data/jobs.db`, in the cloud.
Your laptop's copy stays empty. So a job id from a cloud-generated digest
won't resolve locally, and `jobs letter a1b2c3d4` will say *"no job found
with id 'a1b2c3d4'"*.

Fix: run `uv run jobs ingest` locally once (~40s) before using `jobs
letter`. Job ids are a deterministic hash of
title + company + location, so the same listing gets the same id on any
machine — and letters only need the job row to exist, not a score.

Option A avoids this entirely.

---

## Daily routine, once it's all running

1. Overnight: digest lands in Discord (and email).
2. Morning, on your phone: skim it. Usually you do nothing.
3. When something's worth applying to, at your PC:
   ```bash
   uv run jobs ingest        # only if the nightly runs on GitHub Actions — see Step 10
   uv run jobs letter <id>   # read it, edit it
   ```

---

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `Scoring skipped: missing ANTHROPIC_API_KEY` | `.env` not filled in, or you're in a different directory than the repo root. |
| `Digest not sent` / `Discord: not sent` | Missing `DISCORD_WEBHOOK_URL` or SMTP vars. The log line names exactly which. |
| `no job found with id 'x'` | Cloud/local database split — run `uv run jobs ingest` locally first (see Step 10). |
| `Letter not generated: no style guide` | Run `uv run jobs style-guide` (or hand-write `data/style_guide.md`). |
| jobspy returns 0 jobs | Normal and expected sometimes — LinkedIn/Indeed/Glassdoor actively block scrapers. It degrades to zero results and is marked degraded in the digest footer; other sources are unaffected. |
| Scoring stopped early | Budget cap hit. Re-run `uv run jobs score` to continue, or raise `scoring.budget_cap_usd`. |
| Actions run fails at once: *"The action … is not allowed"* | Repo Settings → Actions → General blocks the outside actions. Allow GitHub-made actions and `astral-sh/setup-uv@*` (Step 10, Option B, part 2). |
| Digest step crashes on `int()` / `SMTP_PORT` | Old code plus an unset secret (GitHub passes it as blank). Fixed in the current code — push, then use **Run workflow**, not **Re-run jobs**. |
| Cloud digest still shows removed companies / Adzuna = 0 | You re-ran an old run, which reuses the old commit. Start a fresh run with **Run workflow**. |
| Digest footer shows `jobspy` at 0 in the cloud | LinkedIn/Indeed often block GitHub's servers. Adzuna still works; nothing to fix. |
| Nightly Actions run never fires | The workflow isn't on the repo's default branch (see Step 10, Option B). |
| Nightly run arrives late, or skips a night | Expected occasionally — GitHub queues scheduled workflows globally and can delay or drop them under load. The cron is set to an odd minute (19:17 UTC) rather than on the hour to reduce this. Nothing is lost: undelivered jobs stay unnotified and appear in the next digest. |
| Digest suddenly huge again, and scoring re-charges you | The Actions cache holding `jobs.db` is evicted after ~7 days without being accessed, so if the nightly stops running for a week or more, the next run starts from an empty database and re-scores everything (~$0.50). Harmless but noisy — the `--force`-free dedupe means it settles down again the next night. |
