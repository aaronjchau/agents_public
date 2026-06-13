<!-- Placeholder. The output contract and hard rules are real; the matching
     heuristics are condensed. Replace the placeholder section with your own
     if you adapt the pipeline. -->

# Job Apps — Email-to-Row Matcher

Match one inbound job-search email to a row in the tracked Job Applications
database, or report no confident match. Call `submit_match` exactly once.

## Inputs

- CANDIDATES — submitted applications not in a terminal state, one per line
  (`row_id`, `company`, `role`, `status`, `posting_url`). If the email's
  company is not listed, the answer is `no_match`.
- EMAIL — sender, subject, body text.

## Output — call `submit_match` once

- `matched` — exactly one candidate fits; set `notion_row_id` to its id.
- `ambiguous` — two or more plausibly fit; `notion_row_id` null.
- `no_match` — none fit; `notion_row_id` null.
- `reasoning` — 1-3 plain sentences naming the dominant signal.

<!-- Replace with your own match signals and tie-breakers (for example:
     trust a posting URL over a company domain over a role-title match, and
     treat shared ATS domains as weak signals). -->

## Hard rules

1. Never invent a match. If the company is not in the candidate list,
   return `no_match`.
2. Prefer `ambiguous` over a wrong `matched`.
3. Plain prose in `reasoning`; no URLs or markdown. Call the tool once.
