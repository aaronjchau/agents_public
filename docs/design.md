# Design notes

The code states what it does; this file records why it is shaped that way.
Audience: a backend engineer reading the repo for the first time.

## System overview

Five agents run as five independent Modal apps, each with its own image,
FastAPI surface, and cron schedule: Triager (labels inbound Gmail), Job Apps
(tracks job-search email into a Notion database), News Brief (daily curated
news page), Morning Brief (daily personal briefing page), and Spend Sync
(daily billing reconciliation). Every run writes an audit row to Neon
Postgres, and a private Next.js dashboard reads those rows directly from the
database. Services never import each other; the one cross-service call
(Triager to Job Apps) is an HTTP POST. The system serves one user, so the
designs below consistently prefer simplicity and bounded cost over
throughput.

## Triager

### One runner, two entry points

`process_message` is the unit of work behind both the manual replay endpoint
and the Pub/Sub webhook, so behavior cannot drift between them. A row in
`triager_runs` keyed by Gmail message id doubles as the idempotency table:
re-runs skip the classifier and re-apply the stored label so Gmail and the
audit log stay aligned even if labels were changed by hand. Errored rows do
not short-circuit; a retry deletes the failure row and reprocesses, so the
backlog self-heals once the underlying fault is fixed.

### The duplicate race

Gmail emits several push notifications per message and Modal runs webhook
invocations in parallel, so two workers can both pass the existence check
(separate sessions) and both INSERT the same message id. The primary key
rejects the loser with an IntegrityError. The loser logs and returns without
dispatching to Job Apps and without re-raising: the winner already applied
the label and owns the dispatch, and re-raising would bury real errors under
duplicate-key tracebacks. Failures elsewhere in the runner write an errored
audit row (null label, populated error), because the webhook has already
promised Pub/Sub a 200 and nothing would redeliver the message otherwise.

### Webhook contract: always 200

Once the request is authenticated, the webhook returns 200 on every code
path, including per-message classify failures. Pub/Sub retries non-2xx
responses, and one bad email must not trigger a redelivery storm. Failed
messages are not lost; their errored audit rows surface on the dashboard and
replay through the manual endpoint. Authentication itself is an OIDC JWT
check: audience pinning plus a service-account email claim.

### Cursor semantics

`gmail_watch_state` stores one resume cursor (`current_history_id`) per
mailbox. Pub/Sub does not guarantee ordering, so a late-arriving older
notification must never rewind the cursor or the next webhook re-fetches and
re-bills the whole window behind it. The advance therefore uses SQL GREATEST
inside a single UPDATE; a Python-side max over a separately read value could
still commit a rewind when two webhooks race.

Two failure modes get distinct handling. A transient `history.list` failure
leaves the cursor in place; the next notification re-covers the same window.
A 404 means the cursor fell outside Gmail's history retention window
(roughly a week), where retrying would 404 forever; the webhook re-seeds the
cursor from the notification's own history id rather than full-syncing,
which would re-classify and re-bill the entire inbox. The skipped gap is
bounded by the retention window and replayable manually.

`history.list` is filtered to `messageAdded` records with the INBOX label.
Without the label filter it returns every mailbox change since the cursor,
including sent mail, which would be fetched and classified (billed) on the
next inbound notification.

### Watch lifecycle

Gmail's push pipeline requires `users.watch()` once to register and again at
least every seven days; a cron renews twice a week. Registration and renewal
are the same API call, split into two functions only so logs state intent.
The renewal upsert never touches the cursor: `watch()` returns the mailbox's
current history id, and overwriting the stored cursor with it would silently
jump past any unprocessed window, exactly the window the failure handling
above preserves.

### Classifier

The label classifier runs Sonnet with extended thinking. Forced tool choice
is incompatible with extended thinking in the API, so tool choice is auto
with a single registered tool, and the response is validated to contain the
tool call. The system prompt and static preamble are cached with a one-hour
TTL. The numbers: a one-hour cache write bills at 2x the input price versus
1.25x for the five-minute default, but lasts twelve times longer; break-even
is about 53% of follow-up calls landing within the hour. Measured mail
arrival shows about 75% of consecutive emails within an hour and only 17%
within five minutes, so the default TTL is slightly net-negative here and
the one-hour TTL pays for itself.

## Job Apps

### LangGraph shape

State is a Pydantic model, but LangGraph validates it only on entry; node
returns merge into the state as plain dicts, and the runner re-validates the
final dict after `invoke()`. Accumulator fields (errors, per-node timings,
per-node token usage) are reducer channels, so each node returns only its
delta. Errors are captured in state rather than raised: a decorator wraps
each node body in try/except and stamps wall-clock latency on every return
path, because LangGraph does not auto-route exceptions to an error edge.
The one opt-out is the email-parse node, whose original Gmail error shape is
the only signal the manual endpoint has for mapping to HTTP statuses. A
`terminal_reason` field records why a run ended at a terminal node and lands
on the audit row. The graph compiles once per process and is reused across
messages.

### Routing

Every classified sublabel gets its Gmail label applied first, so the inbox
mirrors pipeline state for all seven labels. Offers route straight to a
human (the system never acts on an offer). Status updates are informational
and end the run. Everything else goes through the match cascade, which must
precede any Notion write.

### Match cascade

Step one is a deterministic posting-URL match against the applications
database: one hit is a match, two or more is ambiguous, zero falls through
to step two, an LLM choosing matched, ambiguous, or no-match from a compact
candidate list. Candidates are server-side filtered to rows with an
application date, since saved-but-not-applied rows cannot correspond to
inbound mail; the filter halves the candidate count. Terminal-status rows
are deliberately included: follow-up mail about closed applications still
needs to match its row, and the writer's terminal guard makes that safe.
The cascade is read-only and never creates rows.

### Writer guards

The writer is the only component that mutates Notion, and each hard rule is
a separately tested guard: refuse all writes to terminal rows, refuse status
regressions, and drop date fields that already hold a value rather than
overwriting them. Dates are never fabricated; if the email does not state
one, the field stays null, and a missed extraction degrades to a no-op
instead of a bad write. The writer never reads; callers pre-fetch the row's
current state so a single PATCH carries the merged content.

### Idempotency and error contracts

A successful `job_apps_runs` row short-circuits re-runs without re-issuing
the Notion and Gmail mutations (the PATCH converges; re-running is not worth
the cost). Errored rows replay. The gate is check-then-act, so the guarantee
holds for serial deliveries only; two near-simultaneous dispatches can
double-run the graph, with audit rows converging by primary key. The runner
serves two callers with two error contracts: the Triager dispatch hook never
sees an exception (the parent webhook must stay 200), while the manual
endpoint opts into re-raising for HTTP mapping. The audit row is written
before any re-raise.

### The never-send guard

The pipeline only creates Gmail drafts; it never sends mail. CI enforces
this structurally: an AST-based test rejects any `users().messages().send`
call chain under the service's source. A substring grep would false-positive
on unrelated `send` methods (sockets, HTTP clients), so the test walks
attribute chains and anchors on the `messages` segment. It includes a
self-test so a scanner regression cannot silently disable the safety net.

### Model and cost choices

Sublabel classification, matching, and extraction run Opus with high-effort
adaptive thinking, because wrong calls on offers and rejections are the
user-visible failures. Job Apps skips prompt caching entirely: job mail is
sparse (around 5 to 15 messages per day), the cache would mostly miss, and
the write penalty would dominate.

## News Brief

A single daily pass: fetch labeled mail from Gmail, parse, curate with one
LLM call, compose markdown, write the Notion page, record the audit row. The
old two-agent design (an intermediate Notion dump database feeding a
separate brief writer) was a workaround and was deleted.

Failure policy is stage-attributed: each stage has its own try/except so the
failure log and audit row name the stage that broke. Zero emails or zero
curated stories still publishes an empty-day page (token columns stay null
when no LLM call ran). Only a writer failure is fatal; audit-write failures
are logged and swallowed because observability must not break the brief.

The curator never sees URLs. The parser replaces each kept anchor tag with a
numbered marker in the text plus a numbered link list, so the model cites
articles by id and code resolves id to URL afterwards; this keeps the LLM
out of the URL-handling business entirely. Tool use is forced here so the
output is always structured. Newsletter links arrive wrapped in per-sender
click trackers: wrappers whose destination is recoverable from the URL alone
are decoded locally (base64 and JSON payload variants, tracking params
stripped), while server-side opaque wrappers are kept as-is, since emitting
the wrapper URL beats dropping the link.

Audit writes run on a throwaway engine with NullPool: the orchestrator is
sync code that wraps each DB write in `asyncio.run`, so a pooled asyncpg
connection would outlive the event loop that created it and fail on reuse.

## Morning Brief

The brief aggregates tasks, focus stats, calendar, labeled email, and the
day's news into one Notion page. The design principle is degradation over
failure: every fetch is guarded, so one failing source drops its section
instead of killing the brief, and the single LLM call (intro, TL;DR, news
curation) falls back to a deterministic intro. Only the Notion write is
fatal. Everything else is pure, testable transformation; the LLM holds the
only judgment in the pipeline. One page per day: re-runs replace the page
body in place so the URL stays stable. The calendar client ships inert by
design: the shared OAuth token may lack calendar scope, in which case calls
403 and the section is omitted until consent is re-minted.

## Spend Sync

The per-call cost columns in the audit tables are estimates computed from a
hardcoded price table. A daily cron pulls the authoritative numbers from the
Anthropic Admin API (the cost report joined with the usage report on date
and model) and upserts on that composite key, so re-runs replace prior rows
and the dashboard can show variance between estimate and bill. The service
also owns the ad-hoc migrations one-shot because its image is the lightest
(no Node or Notion layer), making cold starts for operational tasks cheap.

## Cross-cutting invariants

- Never auto-send email. Drafts only, enforced structurally in CI.
- Never regress an application's status. The pipeline order is monotonic
  and the three terminal states accept no writes at all.
- Never fabricate dates. Absent in the source means null in Notion.
- Every run writes an audit row, success or failure. The audit tables are
  the observability surface; a run that leaves no row is a bug.
- Tests run with zero secrets. The test conftest injects dummy env values,
  and several tests assert behavior when keys are absent, so a real `.env`
  in scope makes the suite fail; `make test` refuses to run if one exists.
- One database URL serves every runtime. A translation shim upgrades plain
  `postgresql://` to the asyncpg driver and converts `sslmode` into connect
  args, so the same string works for the Python engine and the dashboard's
  serverless driver. ORM models declare the migration-created indexes so
  autogenerate never drops them.
- Bearer auth fails closed. An unset shared secret in a remote container is
  a 500 misconfiguration, never open traffic; unset locally means open for
  curl-driven debugging. Token comparison is constant-time over bytes,
  because the presented token is attacker-controlled and a non-ASCII byte
  would otherwise turn a 401 into a 500.
- All Notion access goes through one subprocess wrapper around the ntn CLI,
  which owns auth and version-header negotiation in a single place.
- Every LLM call is traced in LangSmith, but per-call usage is captured from
  the SDK response separately: tracing is observability, accounting is the
  audit row.

## Cost

The LLM API is the only meaningful cost; everything else rides free tiers.
Prompt caching drives the per-email shape: cache reads bill at a tenth of
the input price, five-minute writes at 1.25x, one-hour writes at 2x. Cache
decisions are made per service from measured arrival rates (see the Triager
and Job Apps sections), not applied globally. Email bodies over 8000
characters are clipped to the first 6000 plus the last 1000, since long
emails accumulate filler in the middle while the classification signal lives
in the head and the footer. Each audit row stores token counts and an
estimated cost, and the daily Admin API sync provides the authoritative
number to reconcile against.
