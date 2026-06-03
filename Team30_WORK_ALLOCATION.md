# Work Allocation Report - Team30

Repository used as evidence: https://github.com/yicheng31/DBMS-demo

This report is based on visible commit history, merged pull requests, and changed files.

## 1. Team Members

| Full Name | Student ID | GitHub Username | Email |
|---|---|---|---|
| Yicheng | 113403027 | `yicheng31` | `howie3429@gmail.com` |
| Fongyi | 113403020  | `floyd941211` | `floyd941211@gmail.com` |
| Yuhao | 113403029 | `lai-yu-hao1026` | `laiyuhao1586@gmail.com` |

## 2. Commit / PR Evidence Summary

| Member | Evidence from commit history |
|---|---|
| Yicheng | Main relational implementation and integration: `87ccc9b first_update` changed `databases/relational/schema.sql`, `databases/relational/queries.py`, `skeleton/seed_postgres.py`, `docker-compose.yml`, and `requirements.txt`; `1c736f3` adjusted fare calculation; `2c57fad` adjusted zero-seat availability behavior; `565b93a`, `ef0a976`, and `a4f1651` fixed agent/UI/Codespaces seed configuration. GitHub PRs #1 and #4 are under `yicheng31`. |
| Fongyi | Main graph implementation: `7c24106` implemented metro/rail station node seeding; `2b11d8d`, `51dc5d8`, and `9206cd8` worked on `databases/graph/queries.py`; `5d410f7` and `0e3969c` updated `skeleton/seed_neo4j.py`; PR #2 merged graph seed/query work; PR #5 refined route query return shape and delay-ripple behavior; PR #6 merged graph route readability and interchange-pair improvements. |
| Yuhao | UI and Task 6 extension: `2a3cf21` enhanced `skeleton/ui.py` with optimization and Chinese localization; `3042839`, `a35489c`, `feb6a14`, and `16293e3` updated `skeleton/agent.py`; PR #3 added optimized `agent.py` and `ui.py`, Chinese station support, Chinese policy translation, tool-routing fallback logic, user profile/payment tools, booking confirmation gate, and UI quick-select station buttons. |

## 3. Task Ownership

### Code Repository

| Task | Primary Owner | Supporting Member(s) | Notes |
|---|---|---|---|
| Task 1 - Relational schema design (`schema.sql`) | Yicheng | Fongyi, Yuhao | Commit `87ccc9b` and PR #4 show major schema work. |
| Task 2a - Core availability & fare queries | Yicheng | Fongyi | Yicheng implemented and later adjusted fare/availability behavior in `queries.py`. |
| Task 2b - Seat & user queries | Yicheng | Yuhao | Yicheng implemented query functions; Yuhao later exposed profile/payment queries through agent tools. |
| Task 2c - Write operations (`execute_booking`, `execute_cancellation`) | Yicheng | Yuhao | Yicheng implemented transaction logic; Yuhao added booking confirmation logic at agent/UI level. |
| Task 2d - Authentication queries | Yicheng | Yuhao | Yicheng handled database auth functions; Yuhao improved login/register/reset UI behavior. |
| Task 3 - PostgreSQL seeding (`seed_postgres.py`) | Yicheng | Fongyi | Commit `87ccc9b` added most PostgreSQL seed work. |
| Task 4 - Neo4j graph design & seeding (`seed_neo4j.py`, `seed.cypher`) | Fongyi | Yicheng | Commits `7c24106`, `5d410f7`, `0e3969c`, and PR #2 show graph seed implementation. Yicheng later fixed Codespaces path/config details. |
| Task 5 - Neo4j query functions (`graph/queries.py`) | Fongyi | Yicheng | Commits `2b11d8d`, `51dc5d8`, `9206cd8`, PR #2, PR #5, and PR #6 show graph route query work. |
| Task 6 - Optional extension | Yuhao | Yicheng, Fongyi | PR #3 documents Chinese UI/agent optimization; Yicheng and Fongyi supported integration and route correctness. |
| Integration / runtime fixes | Yicheng | Yuhao, Fongyi | Commits `565b93a`, `ef0a976`, and `a4f1651` fixed syntax, UI import, and Codespaces seeding. |

### Design Document

| Section | Primary Author | Supporting Member(s) | Notes |
|---|---|---|---|
| Section 1 - ER Diagram | Yicheng | Fongyi | Based on relational schema implementation and seed design. |
| Section 2 - Normalisation Justification | Yicheng | Fongyi | Based on normalized schedule stops, user/auth separation, and transaction design. |
| Section 3 - Graph Database Design Rationale | Fongyi | Yicheng | Based on graph seed/query commits and PR #2/#5/#6. |
| Section 4 - Vector / RAG Design | Yicheng | Yuhao | Yicheng maintained vector seed/config; Yuhao improved Chinese policy query translation in PR #3. |
| Section 5 - AI Tool Usage Evidence | Yuhao | Yicheng, Fongyi | Based on commit/PR evidence and AI-assisted workflow. |
| Section 6 - Reflection & Trade-offs | Yicheng | Fongyi, Yuhao | Team-level reflection across relational, graph, UI, and integration decisions. |
| Section 7 - Optional Extension | Yuhao | Yicheng, Fongyi | Based on PR #3 and later route/integration fixes. |

## 4. Estimated Contribution Percentages

| Member | Estimated % | Brief justification |
|---|---:|---|
| Yicheng | 45% | Largest visible commit count and broadest scope: project setup, relational schema, relational queries, PostgreSQL seeding, fare/availability fixes, Codespaces and UI/agent integration fixes. |
| Fongyi | 35% | Owned the graph database layer: Neo4j seed, graph route functions, route refactors, delay ripple, route return shape, and interchange improvements through several commits and PRs. |
| Yuhao | 20% | Owned the major UI/agent extension: Chinese localization, quick-select UI, Chinese station/policy support, fallback routing, profile/payment tool exposure, and booking confirmation gate. |
| **Total** | **100%** |  |

## 5. Mid-Project Changes

| Change | Original plan | Revised plan | Reason |
|---|---|---|---|
| Graph route implementation | Use Neo4j route-query functions, possibly APOC-style routing. | Implemented Python in-memory Dijkstra-style traversal over Neo4j-loaded graph data. | Avoids APOC dependency and works better with default Docker/Codespaces setup. |
| Route return shape | Some graph functions returned only route legs. | PR #5 adjusted return shape for complete route data and UI friendliness. | The UI and rubric need route path, metrics, and leg details. |
| UI language and query routing | English-first UI and LLM-dependent tool choice. | PR #3 added Chinese localization, station quick-select buttons, Chinese station/policy translation, and fallback routing. | Improves usability and reduces wrong tool calls. |
| Runtime environment | Local-first path/config assumptions. | Yicheng added Codespaces import/seed/config fixes. | Needed reliable execution in a shared cloud/dev environment. |

## 6. Team Declaration

We confirm that this work allocation reflects the visible commit history and the responsibilities shown by the repository evidence.

| Name | Signature / Typed name | Date |
|---|---|---|
| Yicheng | 林益誠 | 2026/6/3 12:14 |
| Fongyi | 曾丰翊 | 2026/6/3 12:20 |
| Yuhao | TODO | TODO |
