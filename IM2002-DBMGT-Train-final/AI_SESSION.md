# AI Session Context — TransitFlow

**How to use this file (required):**
At the start of **every new AI coding session**, paste the full contents of this file as your first message. This gives the assistant the exact project context, constraints, and contracts needed to generate safe, consistent code.

**Who maintains this file (required):**
Whoever changes schema, architecture, query contracts, or team ownership must update this file **in the same commit**.

---

## Project Overview

TransitFlow is a Python-based AI chat assistant for a fictional dual-network transit operator (Metro + National Rail). Users ask about schedules, fares, seat availability, booking/cancellation, route planning, and policy/compensation.

Our student task is to design and implement the database layer used by the assistant:
- Relational + vector queries in `databases/relational/queries.py`
- Graph queries in `databases/graph/queries.py`

The assistant itself does **not** query databases directly; it calls Python tool/query functions.

---

## System Architecture (must remember)

- UI: `skeleton/ui.py` (Gradio)
- Agent orchestration: `skeleton/agent.py`
- Data backends:
  1. PostgreSQL (relational / transactional)
  2. PostgreSQL + pgvector (semantic policy retrieval)
  3. Neo4j (graph routing and network traversal)

### Database responsibility boundaries

| Component | Responsibility |
|---|---|
| PostgreSQL | users/auth, ticket types, schedules, stops, seats, bookings, payments, feedback, policy metadata |
| PostgreSQL + pgvector | policy document embeddings + semantic search (RAG) |
| Neo4j | station graph, interchange, shortest/alternative routes, delay ripple |

**Hard rule:** Do not mix responsibilities. Route traversal logic belongs to Neo4j; transactional booking/auth/payment belongs to PostgreSQL.

---

## Tech Stack

- Python 3.11+
- PostgreSQL + psycopg2 (`RealDictCursor`)
- pgvector extension in PostgreSQL
- Neo4j + official Python driver
- Gradio
- LLM provider: Ollama or Gemini (`.env`/config)
- Docker + `docker-compose`

AI-generated changes must stay within this stack unless the team explicitly decides otherwise.

---

## Team Collaboration Contract (3 members)

### Ownership (fill with real names)
- Member A (_____): Relational read queries (`availability`, `fare`, `profile`, `bookings`)
- Member B (_____): Transaction + auth (`execute_booking`, `execute_cancellation`, login/register/password flow)
- Member C (_____): Neo4j seed + graph queries (`shortest`, `cheapest`, `alternatives`, `ripple`)

### Git discipline
- One focused branch per task (`feat/relational-availability`, etc.)
- Small, scoped commits
- Do not combine major schema edits with unrelated refactors

### Conflict prevention
- Pull latest before coding
- Avoid concurrent edits in the same function
- If touching another member’s area, record reason in Decision Log

---

## Coding Conventions (must follow)

### General
- Follow existing project structure; do not rename files/functions/tables/columns/labels/relationships without team agreement.
- Keep scope narrow: one clearly defined task at a time.
- If requirements/schema are unclear, ask/flag first; do not invent silently.

### Python
- PEP 8, `snake_case`, type hints
- Keep existing function signatures unchanged
- Respect documented return types (`list[dict]`, `dict`, `Optional[dict]`, `bool`, etc.)
- For not-found: return `[]` or `None` per contract (not exceptions)

### PostgreSQL / SQL
- Read first: `databases/relational/schema.sql`, `databases/relational/queries.py`, related seed JSON in `train-mock-data/`
- Use actual existing tables/columns only
- Use `%s` placeholders for dynamic values (no string concatenation)
- Prefer explicit column list; avoid `SELECT *` in production query logic
- Preserve PK/FK/UNIQUE/CHECK assumptions
- Write operations must handle transaction safety

### Neo4j / Cypher
- Read first: `skeleton/seed_neo4j.py`, `databases/graph/queries.py`
- Use Cypher parameters (no string concatenation)
- Label style: `PascalCase`
- Relationship style: `UPPER_SNAKE_CASE`
- Return plain Python `dict` / `list[dict]` matching docstring contract

---

## Current Agreed Relational Schema (project-specific)

**Source of truth:** `databases/relational/schema.sql`

### Existing schema domains
- Users/auth: `registered_users`, `password`
- Metro network: `metro_stations`, `metro_station_lines`, `metro_station_interchange_lines`, `metro_adjacent_stations`
- National rail network: `national_rail_stations`, `national_rail_station_lines`, `national_rail_interchange_lines`, `national_rail_adjacent_stations`
- Ticketing rules: `ticket_types`, `ticket_type_available_on`, `ticket_type_metro_rules`, `ticket_type_metro_valid_lines`, `ticket_type_national_rail_rules`, `ticket_type_national_rail_fare_classes`
- Schedules/stops/time: `metro_schedules`, `metro_schedule_operates_on`, `metro_schedule_stops`, `metro_schedule_travel_time_from_origin`, `national_rail_schedules`, `national_rail_schedule_operates_on`, `national_rail_schedule_stops`, `national_rail_schedule_travel_time_from_origin`, `national_rail_schedule_fare_classes`
- Seats/bookings: `national_rail_seat_layouts`, `national_rail_coaches`, `national_rail_seats`, `national_rail_bookings`, `metro_bookings`
- Payment/feedback/policy: `payments`, `feedback`, `refund_policy`, `refund_policy_applies_to`, `refund_policy_applies_to_ticket_types`, `refund_cancellation_windows`, `refund_compensation_rules`, `booking_rules`, `booking_rule_network_sections`, `travel_policies`, `travel_policy_network_sections`
- Vector/RAG: `policy_documents`

### Critical constraints to preserve
- Interchange cross-FK between metro and national rail station tables
- `payments.booking_id` / `feedback.booking_id` polymorphic reference by prefix (`BK%`, `MT%`)
- Rail booking seat integrity via `(schedule_id, coach, seat_id)` foreign key

---

## Current Agreed Graph Schema (project-specific baseline)

> `skeleton/seed_neo4j.py` still includes TODO seeding logic; implement consistently with this baseline unless team decides otherwise.

### Node labels
- `MetroStation`
- `NationalRailStation`

### Relationship types
- `METRO_LINK`
- `RAIL_LINK`
- `INTERCHANGE_TO`

### Key properties
- Node: `station_id`, `name`, optional `lines`
- Link rel: `line`, `travel_time_min`
- Interchange rel: optional transfer metadata (document any additions in Decision Log)

---

## Function Signatures We Are Implementing (fixed contracts)

### Relational (`databases/relational/queries.py`)

```python
# Read-only
def query_national_rail_availability(origin_id: str, destination_id: str, travel_date: Optional[str] = None) -> list[dict]: ...
def query_national_rail_fare(schedule_id: str, fare_class: str, stops_travelled: int) -> Optional[dict]: ...
def query_metro_schedules(origin_id: str, destination_id: str) -> list[dict]: ...
def query_metro_fare(schedule_id: str, stops_travelled: int) -> Optional[dict]: ...
def query_available_seats(schedule_id: str, travel_date: str, fare_class: str) -> list[dict]: ...
def query_user_profile(user_email: str) -> Optional[dict]: ...
def query_user_bookings(user_email: str) -> dict: ...  # {"national_rail": [...], "metro": [...]}
def query_payment_info(booking_id: str) -> Optional[dict]: ...

# Write operations
def execute_booking(user_id, schedule_id, origin_station_id, destination_station_id, travel_date, fare_class, seat_id, ticket_type="single") -> tuple[bool, dict | str]: ...
def execute_cancellation(booking_id: str, user_id: str) -> tuple[bool, dict | str]: ...

# Auth
def register_user(email, first_name, surname, year_of_birth, password, secret_question, secret_answer) -> tuple[bool, str]: ...
def login_user(email: str, password: str) -> Optional[dict]: ...
def get_user_secret_question(email: str) -> Optional[str]: ...
def verify_secret_answer(email: str, answer: str) -> bool: ...
def update_password(email: str, new_password: str) -> bool: ...
```

### Graph (`databases/graph/queries.py`)

```python
def query_shortest_route(origin_id: str, destination_id: str, network: str = "auto") -> dict: ...
def query_cheapest_route(origin_id: str, destination_id: str, network: str = "auto", fare_class: str = "standard") -> dict: ...
def query_alternative_routes(origin_id, destination_id, avoid_station_id, network="auto", max_routes=3) -> list[list[dict]]: ...
def query_interchange_path(origin_id: str, destination_id: str) -> dict: ...
def query_delay_ripple(delayed_station_id: str, hops: int = 2) -> list[dict]: ...
def query_station_connections(station_id: str) -> list[dict]: ...
```

---

## Prohibitions (must not do)

- Do not accept AI-generated code without manual review.
- Do not allow AI to rename existing functions/files/tables/columns/labels/relationships without explicit team decision.
- Do not change function signatures unless explicitly approved by the team.
- Do not invent schema or graph model silently.
- Do not add ORM/new backend framework/new DB/external service unless explicitly approved.
- Do not commit `.env`, API keys, passwords, tokens, or secrets.
- Do not concatenate user input into SQL/Cypher.
- Do not use PostgreSQL to replace graph traversal logic.
- Do not use Neo4j for transactional booking/auth/payment logic.
- Do not modify vector/RAG behavior unless the task is specifically about policy search.
- Do not rewrite unrelated large areas of the project.

---

## Definition of Done (AI-assisted task)

A task is done only if:
- It follows this file.
- Function signature remains unchanged.
- SQL/Cypher aligns with actual schema/graph model.
- Dynamic values are parameterized.
- Return shape matches docstring/contract.
- Empty/not-found behavior is handled correctly.
- Code is manually tested/reviewed against expected seed-data assumptions.
- No secrets are introduced.
- Any new team decision is recorded in Decision Log.

---

## Recommended Workflow Prompts

### Analysis prompt (before coding)
```text
Read ai_session.md first and follow it as the project contract.

I need to work on:
<function_or_file_name>

Before writing code, analyze:
1) Whether this belongs to PostgreSQL, pgvector, or Neo4j
2) Which files should be inspected first
3) Which tables/columns/labels/relationships are involved
4) Expected return format
5) Edge cases to test
6) Whether a team decision is needed before implementation

Do not write implementation code yet.
```

### Implementation prompt (single-function scope)
```text
Implement only <FUNCTION_NAME> in <TARGET_FILE>.
Constraints:
- Keep signature unchanged
- Match existing schema/model exactly
- Use parameterized SQL/Cypher
- Return contract-compliant shape
- Do not modify unrelated functions/files
Provide patch + brief logic + edge cases.
```

### Review prompt (after coding)
```text
Review this diff against ai_session.md and existing schema.
Focus only on actionable issues:
- wrong table/column/label/relationship names
- SQL/Cypher injection risks
- signature changes
- wrong return shape
- missing empty-result handling
- transaction problems
- unrelated file changes
Suggest smallest safe fix per issue.
```

---

## Team Decisions Log (append-only)

- 2026-05-22 — Decision: Use `ai_session.md` as required first message in every AI coding session. Why: keep shared context consistent across 3 team members.
- 2026-05-22 — Decision: Relational schema source of truth is `databases/relational/schema.sql`. Why: prevent schema drift between prompts and implementation.
- 2026-05-22 — Decision: Graph baseline uses `MetroStation`/`NationalRailStation` and `METRO_LINK`/`RAIL_LINK`/`INTERCHANGE_TO`. Why: align route-query implementation across members.

---

## Pre-Commit Checklist

- [ ] If schema/graph model changed, update this file in same commit.
- [ ] If return shape/contract changed, update signatures/contract section.
- [ ] If touching teammate-owned area, add decision-log note.
- [ ] Verify only relevant files were changed.
- [ ] Confirm no secrets are included.

