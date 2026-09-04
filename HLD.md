# High-Level Design — Forecast Reconciliation Agent

## 1. Purpose

Source concept, from *Five Agents, Ten Modules* (Merchandising: Agentic AI
Research Note):

> Sits between the top-down financial target and each planner's bottom-up
> build — Store Plan, Line Plan, Class Plan — and surfaces every gap the
> moment it appears: why it exists (a rate-of-sale shift, a carryover
> assumption, a new-store ramp), and a reconciled number both sides can
> approve. Turns the plan-vs-plan handshake from o9's MFP process into an
> always-on collaborative negotiation, not a week of email back-and-forth
> before sign-off.
>
> Feeds → OTB Planning, QPO Planning

This build is a scoped-down, standalone demo of that agent: same
reconciliation logic and interaction pattern, against a synthetic dataset
instead of a live o9 MFP feed, so the concept can be evaluated end-to-end
without integration work.

## 2. Goals / non-goals

**Goals**
- Detect and quantify gaps between a top-down financial target and the
  bottom-up planner build, per merchandise class.
- Explain *why* a gap likely exists, grounded in concrete signals — not a
  generic LLM guess.
- Propose a single reconciled number, with confidence and a suggested
  approver, that a planner or finance lead can accept, reject, or override.
- Offer a conversational interface for ad hoc questions, matching the
  "Conversational Planning Copilot" interaction pattern in the same
  research note, scoped to this agent's data.

**Non-goals (out of scope for this demo)**
- Live integration with o9 MFP / Assortment Planning or any upstream
  planning system.
- Writing approved reconciliations back into a planning system (OTB, QPO).
- Real authentication — the role picker in the UI is self-declared, not
  enforced by a login.
- Statistically calibrated thresholds — the rule thresholds here are
  illustrative starting points, not tuned against real historical data.

## 3. Architecture

```
                          ┌─────────────────────────┐
                          │   sample_data/plans.csv   │
                          │  (or uploaded CSV)        │
                          └────────────┬─────────────┘
                                       │
                                       ▼
                          ┌─────────────────────────┐
                          │       data.py             │
                          │  load + validate columns   │
                          └────────────┬─────────────┘
                                       │ DataFrame
                                       ▼
                          ┌─────────────────────────┐
                          │   reconciliation.py       │
                          │  deterministic math:       │
                          │  - bottom-up consensus      │
                          │  - gap $ / gap %            │
                          │  - plan divergence %        │
                          │  - root-cause flags         │
                          └────────────┬─────────────┘
                                       │ ClassSignals (per class)
                                       ▼
                          ┌─────────────────────────┐
                          │        agent.py           │
                          │  LangChain + ChatGroq       │
                          │  (openai/gpt-oss-20b)       │
                          │                             │
                          │  A) reconcile_class():      │
                          │     signals → structured     │
                          │     output (models.py)       │
                          │     = root cause, reconciled  │
                          │       number, rationale,      │
                          │       confidence, owner       │
                          │                             │
                          │  B) chat(): tool-calling      │
                          │     AgentExecutor with tools   │
                          │     wrapping (A) + the signals │
                          │     table, for free-form Q&A   │
                          └────────────┬─────────────┘
                                       │
                                       ▼
                          ┌─────────────────────────┐
                          │        app.py             │
                          │  Gradio UI:                │
                          │  - Reconciliation Dashboard │
                          │  - Ask the Copilot (chat)   │
                          └─────────────────────────┘
```

### Why the math is deterministic and the LLM is not

Variance, percentages, and threshold flags are computed in plain
pandas/Python (`reconciliation.py`). The LLM (`agent.py`) never does
arithmetic on the raw numbers — it receives already-computed signals and is
asked to (a) narrate the likely cause in planner-friendly language and
(b) weigh those signals into a single recommended number. This keeps the
numbers exact and reproducible while using the LLM for what it's good at:
synthesis and explanation, matching the report's framing of "why it exists"
as the differentiator over a plain variance dashboard.

## 4. Data model

`sample_data/plans.csv` (or any CSV with the same columns), one row per
merchandise class per season:

| Column | Meaning |
|---|---|
| `class` | Merchandise class name |
| `category` | Department / category |
| `season` | Season code |
| `top_down_target` | Finance/leadership top-down revenue target |
| `store_plan_total` | Bottom-up total rolled up from the Store Plan |
| `line_plan_total` | Bottom-up total rolled up from the Line Plan |
| `class_plan_total` | Bottom-up total from the Class Plan |
| `rate_of_sale_trend_pct` | Recent rate-of-sale trend vs. plan assumption |
| `carryover_pct` | Share of the class plan relying on carryover stock |
| `new_store_units` | Units exposed to new-store ramp assumptions |

### Derived signals (`reconciliation.py`)

- `bottom_up_consensus` = mean(store_plan_total, line_plan_total, class_plan_total)
- `gap_abs` / `gap_pct` = top_down_target vs. bottom_up_consensus
- `plan_divergence_pct` = spread across the three bottom-up views, as a
  measure of planner-to-planner disagreement
- `flags`: `rate_of_sale_shift`, `carryover_assumption`, `new_store_ramp`,
  `planner_disagreement`, or `unexplained_gap` (material gap with no flag
  triggered — routed to joint review rather than an LLM guess)

Thresholds live as module-level constants in `reconciliation.py` for easy
tuning.

## 5. LLM integration

- **Provider**: Groq, via `langchain-groq`'s `ChatGroq`.
- **Model**: `openai/gpt-oss-20b` (overridable via `GROQ_MODEL` in `.env`).
- **Structured output**: `reconcile_class()` uses
  `llm.with_structured_output(ReconciliationRecommendation)` (Pydantic
  model in `models.py`) so the UI always receives well-typed fields
  (`root_cause`, `reconciled_number`, `rationale`, `confidence`,
  `recommended_owner`) rather than parsing free text.
- **Tool-calling agent**: the copilot tab uses
  `langchain.agents.create_tool_calling_agent` + `AgentExecutor` with four
  tools (`list_classes`, `get_class_variance`, `get_reconciliation`,
  `rank_classes_by_gap`) so the model grounds every answer in a live tool
  call instead of hallucinating figures.

## 6. Front end (Gradio)

Two tabs in a single `gr.Blocks` app (`app.py`):

1. **Reconciliation Dashboard** — variance table across all classes (sorted
   by largest absolute gap %), a class picker, an "Analyze gap" action that
   renders the LLM's recommendation, three approval actions (accept
   top-down / bottom-up / reconciled) that accumulate into a session-local
   "approved plan" table (downloadable as CSV), a role picker, a form to
   propose an alternative forecast with a justification, and a lineage
   table showing the full history of a class's forecast. Supports
   uploading a replacement CSV in place of the bundled sample data.
2. **Ask the Copilot** — a chat interface backed by the tool-calling agent
   for free-form questions grounded in the same dataset.

Session state (the agent instance, the last analyzed result, the
approved-plan table) is held in `gr.State`, scoped to the browser session.
Forecast lineage is the exception: every agent recommendation, user
proposal, and approval is appended to a SQLite database (`db.py`,
`forecast_reconciliation.db`) as an immutable, chained event, so that
history survives restarts and can be queried outside the UI.

## 7. Forecast lineage (SQLite)

Each class's forecast history is a chain of rows in the `forecast_events`
table, one per change:

| Column | Meaning |
|---|---|
| `event_type` | `agent_recommendation`, `user_proposal`, or `approval` |
| `role` | Who made the change (`db.ROLES`); null for agent events |
| `previous_value` / `new_value` | The value being superseded and the new one |
| `justification` | Free-text rationale (LLM rationale, or the user's) |
| `parent_event_id` | The event this one supersedes, forming the chain |

`record_event()` (in `db.py`) always looks up the latest event for a class
and chains to it automatically, so the UI never has to track "current
state" itself — it's just the most recent event. `get_lineage()` replays
the full chain for a class, oldest first, for the lineage table in the UI.

## 8. Extension points (if this moves beyond a demo)

- Replace `data.py`'s CSV loader with a connector into o9 MFP / Assortment
  Planning (or your data warehouse) so `top_down_target` and the three
  bottom-up totals are live.
- Feed approved reconciliations into **OTB Planning** and **QPO Planning**,
  per the "Feeds →" relationship in the source research note.
- Replace the fixed rule thresholds in `reconciliation.py` with values
  calibrated against your historical reconciliation outcomes, or a
  learned model for `unexplained_gap` cases.
- Add real authentication so `role` is enforced by login rather than
  self-declared in a dropdown.
