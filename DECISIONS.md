# Decisions log

One line per judgment call: date, decision, why.

- 2026-09-19: Used `click` for the CLI (not in CLAUDE.md's stack list) — needed
  a subcommand framework (`jobs ingest`, `jobs email`, ...) and it's the
  lightest well-known option with good `--option` ergonomics; stdlib
  `argparse` would work but is noticeably more boilerplate for subcommands.
- 2026-09-19: Increment 1 scope is ingest + dedupe + a raw (unscored,
  ungrouped) email digest, per CLAUDE.md §6 build order. Discord delivery and
  LLM scoring/classification land in increment 2, not this one.
- 2026-09-19: Skipped the SEEK and Workday/generic-fallback ingest sources for
  increment 1. CLAUDE.md's build order puts aggregators and stable ATS JSON
  APIs first; SEEK has no public API and Workday's per-tenant endpoint
  discovery plus the generic Playwright+LLM fallback are meaningfully more
  work. Both are one-line additions to `ingest/run.py`'s source registry when
  built.
- 2026-09-19: `jobspy`'s LinkedIn/Indeed/Glassdoor scraping is network- and
  anti-bot-sensitive, so its integration test is marked `@pytest.mark.network`
  and its ingest wrapper catches all exceptions per CLAUDE.md §5 ("one dead
  source never kills the run") rather than letting a site-side block crash
  the pipeline.
- 2026-09-19: SQLite dedupe key is `sha256(normalize(title)+normalize(company)+
  normalize(location))`, truncated to 16 hex chars for a short, CLI-friendly
  job id (`jobs letter <id>`), with the full hash also stored for collision
  safety in `job_id_full`.
- 2026-09-19: `data/jobs.db` is gitignored per CLAUDE.md §3, so the nightly
  GitHub Actions workflow persists it across runs with `actions/cache`
  (unique key per run, `restore-keys` prefix to pull the latest) rather than
  committing it — dedupe would otherwise reset to empty every night.
- 2026-09-19: Lever's own demo board (`leverdemo`) is used as a companies.yaml
  example instead of a real target company — every real company slug tried
  during setup returned 404 (most companies have moved off Lever's public
  postings API or use a different ATS now), while `leverdemo` is stable,
  public, and exercises the same client code.
- 2026-09-19: Increment 2 is scoring + grouping + Discord digest, per
  CLAUDE.md §6. This also replaces increment 1's raw (unscored) email digest
  with the real scored/grouped one, since CLAUDE.md only ever specifies one
  email digest (the scored one) — the increment-1 version was explicitly a
  stub to prove the pipeline end to end before scoring existed. `jobs email`
  is renamed `jobs digest` to match CLAUDE.md §5's stage-per-CLI-command
  list, and now sends both Discord and email, not just email.
- 2026-09-19: `jobs ingest`, `jobs score`, and `jobs digest` are independent
  CLI invocations (per CLAUDE.md §5) and, in the nightly workflow, separate
  steps — so digest can't read ingest's source_counts/degraded_sources from
  memory for its footer. Added a small `run_log` table (stage, timestamp,
  JSON summary) that `ingest`/`score` write to and `digest` reads the latest
  `ingest` row from, rather than passing that state through files or env.
- 2026-09-19: Model is `claude-haiku-4-5` for scoring (bulk classification,
  per CLAUDE.md §4) via `client.messages.parse(output_format=...)` for
  guaranteed schema-valid structured output, still wrapped in this project's
  own pydantic validation + retry-once-then-skip per CLAUDE.md §5. Pricing
  ($1/$5 per 1M input/output tokens) and cache-token multipliers (~1.25x
  write, ~0.1x read) used in the budget guard are Anthropic's documented
  first-party rates at build time — cache multipliers are an approximation,
  not billed-exact, since exact per-model cache pricing wasn't available.
- 2026-09-19: Discord embeds are one-per-scope-group (Local/Domestic/
  International), coloured by the highest score band present in that group,
  with industry/role_type folded into each field's label rather than
  separate embeds — Discord has no sub-grouping within an embed, and one
  embed per (scope, industry, role_type) combination would blow past the
  10-embeds-per-message limit on a normal night.
- 2026-09-19: A job is marked `notified_at` only if at least one digest
  channel (Discord or email) actually sent successfully; if both are
  unconfigured or both fail, it's left unnotified so the next run retries it
  instead of silently dropping it.
- 2026-09-20: Increment 3 is style guide + cover letters, per CLAUDE.md §6.
- 2026-09-20: CLAUDE.md's cover-letter step calls for picking past letters by
  "embedding similarity," but no embeddings-provider credential exists in
  `.env.example` (Voyage AI, OpenAI, etc. would all need a new key) —
  per CLAUDE.md §3, that's a "stop and ask for a credential" situation, not
  one to route around. Used local, dependency-free TF-IDF cosine similarity
  instead (`letters/similarity.py`): no API call, no cost, good enough to
  rank a handful of past letters, and isolated behind one function
  (`most_similar`) so it's a one-function swap to real embeddings if an
  embeddings API key is ever supplied.
- 2026-09-20: `data/style_guide.md` is generated once and then hand-tweaked
  (CLAUDE.md §5), so `jobs style-guide` refuses to overwrite an existing one
  unless `--force` is passed, and even then backs the old version up first
  (`style_guide.md.bak.<timestamp>`) per CLAUDE.md §3's "never overwrite
  data/ without a backup." A letter's resume grounding is re-read fresh on
  every `jobs letter` run regardless, so resume edits apply immediately —
  only the *style* narrative needs an explicit regenerate.
- 2026-09-20: Reused `score/budget.py`'s cost estimator for style-guide/
  letter generation (Sonnet pricing) but did not wire the hard per-run
  `BudgetGuard` abort into these commands — that guard exists for the
  unattended nightly `score` run; `jobs style-guide` and `jobs letter <id>`
  are one-off, user-invoked actions where aborting mid-command would be
  unhelpful. Spend is still logged/printed for each call for transparency.
- 2026-09-20: Increment 4 (final increment) is form pre-fill, per CLAUDE.md
  §6. `apply/` gained a filler per ATS (Greenhouse/Lever/Ashby), matching
  the ingest/ pattern, plus a generic fallback that opens the page untouched
  for any other source. Field selectors were confirmed against real, live
  application pages (not guessed) — see each filler module's docstring for
  which fields were verified where.
- 2026-09-20: The apply stage needs the candidate's own contact details
  (name/email/phone/LinkedIn) to fill a form, which is real PII, not the
  job-matching prose in `config/profile.md`. Added `config/applicant.yaml`
  for this (a `.example.yaml` template is committed, the real file is
  gitignored) — mirroring the `.env`/`.env.example` split already used for
  secrets, since this data deserves the same "never committed" treatment.
- 2026-09-20: Greenhouse splits first/last name and Lever/Ashby use one
  full-name field — confirmed against each platform's live form, not
  assumed. `ApplicantProfile.full_name` exists specifically to serve the
  single-field platforms without duplicating the join logic per filler.
- 2026-09-20: `try_fill`/`try_upload` (`apply/browser.py`) try a CSS
  selector list first, then label-text matching, because Greenhouse's
  standard fields (name/email/phone) have stable ids but its custom
  questions (e.g. LinkedIn) get a random id per job posting — confirmed
  live. Ashby's custom-question ids are random for nearly every field
  except its `_systemfield_*` core fields — same fix. Label matching is the
  fallback that survives an ATS changing/randomizing a field's id.
- 2026-09-20: `launch_visible_browser()` hardcodes `headless=False` with no
  parameter to override it — CLAUDE.md §7's "opens a pre-filled form in a
  visible browser and stops, I press submit" is a hard safety rule, not a
  default. Tests instead launch their own headless Playwright browser
  directly (`tests/apply/conftest.py`) against local static HTML fixtures,
  never against `launch_visible_browser()` and never against a live site —
  this keeps the real CLI path always-visible while still letting selector
  logic run against a real browser engine in CI.
- 2026-09-20: pin Python 3.12 (.python-version) — 3.14 has no regex wheel, build fails on Windows without MSVC
- 2026-09-20: Adzuna fetch skips the robots.txt check (api.adzuna.com serves 'Disallow: /'). It's their official key-authenticated API, not crawling; rate limits/caps still apply. Adzuna total failure now marks the source degraded instead of silently returning 0.
- 2026-09-20: Removed the apply/form-prefill stage (`jobs apply`, `src/jobs/apply/`, Playwright dependency, `applicant.yaml`). User decision: they will apply by hand, and reliable pre-fill across arbitrary sites was judged too complex. Adzuna-sourced jobs (most of the volume) link to an Adzuna page, not the employer form, so it would have helped little anyway.
- 2026-10-10: Added `jobs add` CLI command to manually register a job the user found themselves (not covered by any ingest source), so `jobs letter <id>` has something to work from. Stores it as `source="manual"` through the normal dedupe/store_jobs path — same job_id scheme, so it behaves identically to an ingested row (no score, which `jobs letter` doesn't require).
- 2026-10-10: `jobs add` auto-fetches the job ad from `--url` (httpx GET respecting robots.txt, reusing `FetchClient`, + a Haiku extraction pass to strip page chrome) when neither `--description` nor `--jd-file` is given, instead of always requiring a manual paste. Kept httpx-only (no Playwright) per CLAUDE.md §4 ("Playwright only where nothing else works"). Verified: works against a plain ATS board page (Greenhouse), fails cleanly against the big aggregators (Indeed 401, Adzuna 403 — they block non-browser requests) and against a non-job page (correctly detected as "not a posting" rather than hallucinating content). Falls back to asking for --description/--jd-file on any failure; the generic-fallback ingest source from §5 item 4 (Playwright + LLM extraction) is still the thing to build if auto-fetch needs to work on JS-rendered pages or the blocked aggregators.
- 2026-10-23: Scoring stops on the first 401/403 (or 400 mentioning the API key) instead of retrying every batch, and a failed scoring run is shown as Degraded in the digest. A rejected key had made the digest say "nothing new today" for days. No support for the anthropic-workspace-id header: the fix is a workspace-scoped key.
- 2026-10-25: `jobs add` gained `--clipboard` (via `pyperclip`, new dependency) as a third way to supply the job ad, so LinkedIn jobs (which auto-fetch can never reach — robots.txt disallows `/jobs/view/...`) don't require saving a file first. Select the ad text on the page, Ctrl+C, pass `--clipboard` — no `--jd-file` needed for the common case.
- 2026-10-25: Added a local web UI (`jobs web`, Flask, `src/jobs/web/`) over the same db/stages/outputs as the CLI: browse/search/filter jobs, refresh (ingest), score, add a job by pasting the ad, write a letter, copy/download it. Local-only on purpose (127.0.0.1, trusted-host check, cross-origin POSTs refused so another website can't trigger paid LLM calls) — hosting it would need db syncing with the GitHub Actions copy plus auth, a different architecture. Flask over FastAPI: everything underneath is synchronous and it's single-user. Long actions (ingest ~1 min, letter ~30 s) run inline in the request with a busy button rather than a job queue. CLI ingest/score/add logic moved into `jobs.pipeline` / `add_manual_job` so both front ends share one code path. An ad pasted on the job page is saved as that job's description before generating. Download .docx rebuilds the file from the .md if the .docx was moved.
- 2026-10-31: Cover letters tailored harder to each ad (user found them generic). Mapping now records the ad's own terms per requirement, an honest reframe of the evidence in those terms, selection criteria (addressed in prose, in order), and "why this role" specifics. The style guide now governs voice/greeting/sign-off only; stock body sentences are banned (`letters.avoid_phrases` in settings). Free deterministic checks after drafting — ad-term coverage, stock phrases, 7+ word runs copied from the ad (selection criteria exempt) — trigger one revision, kept only if strictly better. Then a Sonnet fact-check against the resume plus one correction pass, because test letters showed the model blending facts (a skills-list tool attached to an unrelated project); leftovers are shown to the user. Cost per letter rose from ~$0.04 to ~$0.10–0.25 — accepted for accuracy; the fact-check could move to Haiku if cost matters.
- 2026-10-31: Draft max_tokens 2048 → 8192 and a `max_tokens` stop now retries instead of saving: Sonnet 5's thinking tokens count against the cap and once truncated a letter mid-sentence.
- 2026-10-31: .docx files named "Cover Letter <Company> <year>.docx" (user's own convention; ` (2)` suffix instead of overwriting another job's letter, index in `outputs/letters/.docx_names.json`), with Author set from `letters.author_name` — python-docx otherwise stamps "python-docx" and a 2013 date. No header block above the greeting (user's call).
