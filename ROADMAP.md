# Roadmap

This is a working demo, not a production system. This roadmap tracks what
it would take to move it from "single-user demo" to "pilot on real data"
to "in production feeding downstream planning systems." See the repo
[issues](https://github.com/abhineer/forecast-reconciliation-agent/issues)
for the tracked backlog behind each phase.

## Phase 0 — Demo (done)

- Deterministic variance/root-cause math against a top-down vs. bottom-up
  plan dataset.
- LLM-narrated root cause + reconciled number via LangChain + Groq.
- Gradio UI: variance dashboard, approve/override actions, conversational
  copilot tab.
- Role-tagged forecast proposals with required justification.
- Full forecast lineage (agent recommendation → proposal → approval)
  persisted to SQLite as a chained, append-only audit log.

## Phase 1 — Hardening

Make the demo trustworthy and maintainable before anyone relies on it.

- Automated tests for the reconciliation math and the SQLite lineage
  layer.
- CI (lint + tests) on every push.
- Input validation on CSV upload (schema, types, duplicate classes).
- SQLite in WAL mode / documented concurrency limits for multiple
  simultaneous users.
- Configurable database path via environment variable, not a hardcoded
  project-root file.
- Structured error handling in the UI (LLM failures, malformed data)
  instead of raw tracebacks.

## Phase 2 — Pilot readiness

What's needed to run this against one real merchandising category with
real users.

- Real authentication mapping logged-in users to a role, replacing the
  self-declared role dropdown.
- Live data connector (API or warehouse query) replacing the flat CSV
  loader.
- Notification on new proposals or newly-flagged material gaps (Slack or
  email).
- Filter/search the variance table by category, season, or flag.
- Approval workflow rules (e.g., gaps above a threshold require Finance
  sign-off before they count as approved).

## Phase 3 — Scale / production

- Feed approved reconciliations back into downstream planning systems
  (OTB Planning, QPO Planning).
- Replace SQLite with a shared production database for concurrent
  multi-user access.
- Calibrate root-cause thresholds against real historical reconciliation
  outcomes instead of illustrative constants.
- Multi-tenant support across multiple categories/departments.
- Monitoring for LLM recommendation quality and drift over time.
