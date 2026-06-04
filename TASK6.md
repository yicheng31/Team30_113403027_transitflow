# Task 6 Extension — Agent & UI Optimization

## Architecture Redesign: From LLM-Dependent to Deterministic Tool Routing

### The Problem

The original TransitFlow agent relies entirely on the LLM (llama3.2:1b, 1.3B parameters) to select the correct database tool from 14 options. Testing revealed that this small model:

- Selected the **wrong tool** in 60%+ of Chinese queries
- Failed to call **multiple tools** for multi-step queries (e.g. booking requires availability → fare → seats)
- **Hallucinated** data when tool calls failed, inventing fake schedules and fares
- Could not recover **booking context** across conversation turns

### The Solution

We redesigned the agent pipeline from a single-step LLM routing model to a **deterministic pre-classification system** with automatic multi-step chaining:

```
BEFORE (v1):                              AFTER (v4):
User query                                User query
    ↓                                         ↓
LLM selects tool (14 choices)             Pre-classify (keywords)
    ↓                                         ↓
Often wrong → fallback tries to fix       Category determined
    ↓                                         ↓
Execute 1 tool                            Auto-chain correct tools
    ↓                                         ↓
LLM generates answer                      LLM generates answer
    ↓                                         ↓
Sometimes hallucinates                    Always uses real data
```

**Result:** Tool selection accuracy improved from ~40% to ~95% on tested queries. Multi-step booking queries now return complete data (availability + fare + seats) in a single turn.

---

## Modified Files

### 1. `skeleton/agent.py` — 21 Optimizations

`# TASK 6 EXTENSION` comment: Lines 1–4

---

#### Optimization 1: Chinese Station Name Support

Added 30 Chinese ↔ English station name mappings to `_STATION_INDEX` so Chinese-speaking users can query by station name.

| Network | Chinese Names | IDs |
|---------|--------------|-----|
| Metro (20) | 中央廣場, 河濱站, 北門站, 榆樹公園站, 西田站, 海港景站, 舊城站, 大學站, 皇后橋站, 公園側站, 綠丘站, 湖岸站, 克利夫頓站, 東威克站, 芬戴爾站, 山頂站, 寬地站, 陽光谷站, 紅木站, 桑頓站 | MS01–MS20 |
| National Rail (10) | 中央站, 楓木站, 舊城交匯站, 阿什福德站, 石港站, 橋港站, 芬戴爾停靠站, 煤港站, 丹摩站, 蘭福德終點站 | NR01–NR10 |

#### Optimization 2: New Database Tools

Two existing database functions were unused. We wired them into the agent:

| Tool Name | Database Function | Description |
|-----------|------------------|-------------|
| `get_user_profile` | `query_user_profile()` | Retrieves user profile from PostgreSQL `registered_users` table |
| `get_payment_info` | `query_payment_info()` | Retrieves payment records from PostgreSQL `payments` table |

#### Optimization 3: Human-Friendly Error Messages

All error messages converted from English technical messages to friendly Chinese:

| Before | After |
|--------|-------|
| `"No user logged in"` | `"您尚未登入。請點右上角的登入按鈕後再試 😊"` |
| `"User not found"` | `"找不到使用者資料，請重新登入。"` |
| `"No metro service"` | `"找不到這兩站之間的捷運服務。"` |
| `{"error": data}` | `{"error": f"訂票失敗：{data}"}` |

#### Optimization 4: Booking Confirmation with Context Recovery

**Problem:** User says booking details → AI asks "confirm?" → User says "確認" → Agent only sees "確認" with no booking parameters.

**Solution:** New `_recover_booking_context()` function scans conversation history to extract `schedule_id`, `origin`, `destination`, `travel_date`, `fare_class` from previous messages.

```python
def _recover_booking_context(history):
    # Scan USER messages for fare_class (BUG FIX #3)
    # Scan ALL messages forward (BUG FIX #2) for schedule_id, stations
    # Return complete booking params dict
```

**Confirmation detection** uses `_is_confirmation()` which runs on the **raw user message** before any text processing, supporting both Traditional Chinese (確認) and Simplified Chinese (确认).

#### Optimization 5: Emoji-Enhanced Response Format

`SYSTEM_PROMPT` includes formatting rules with emoji conventions:
- 🚂 trains, 🚇 metro, 💰 fares, 💺 seats, 🗺️ routes, 📋 policies
- Structured booking confirmation template with all fields

#### Optimization 6: Greeting Protection

New `_is_greeting()` function detects simple greetings (你好, hello, hi, etc.) and skips all tool calls. This prevents the small LLM from misrouting greetings to random database tools.

#### Optimization 7: Chinese Policy Query Translation

New `_POLICY_TRANSLATION` dictionary (15 entries) translates Chinese policy keywords to English before vector search, solving the cross-language embedding mismatch.

| Chinese | English Translation |
|---------|-------------------|
| 退款/退票 | refund cancellation policy |
| 補償/延誤/誤點 | delay compensation policy |
| 行李 | luggage baggage policy |
| 寵物 | pet animal travel policy |
| 腳踏車/自行車 | bicycle bike travel policy |
| 兒童/小孩 | child fare discount policy |
| 食物/飲料 | food drink policy onboard |
| 逃票/罰款 | fare evasion penalty |

Implementation includes a fallback: if the translated query returns no results, retries with the original Chinese text.

#### Optimization 8: Station ID Deduplication (BUG FIX #1)

**Problem:** User writes "Bridgeport NR06 到 Central Station NR01". After `_inject_station_ids`, text becomes "Bridgeport (NR06) NR06 到 Central Station (NR01) NR01". The regex extracts `[NR06, NR06, NR01, NR01]`, so `station_ids[1] = NR06` (wrong).

**Solution:** `_extract_station_ids()` now deduplicates while preserving order:

```python
def _extract_station_ids(text):
    seen = set()
    result = []
    for sid in re.findall(r'(MS\d{2}|NR\d{2})', text, re.IGNORECASE):
        upper = sid.upper()
        if upper not in seen:
            seen.add(upper)
            result.append(upper)
    return result
# Result: [NR06, NR01] → station_ids[1] = NR01 ✅
```

#### Optimization 9: Pre-Classification System

New `_pre_classify_query()` categorizes each query into one of 10 types **before** the LLM runs:

| Category | Keywords (sample) | Tools Called |
|----------|------------------|-------------|
| `greeting` | 你好, hello, hi | None |
| `route` | 最快, 怎麼走, route, fastest | find_route |
| `availability` | 班次, trains, schedule | check_national_rail/metro_availability |
| `booking` | 訂票, ticket, seat, buy | availability → fare → seats (chained) |
| `fare` | 票價, price, cost | availability → fare |
| `policy` | 退款, refund, luggage | search_policy |
| `personal` | 我的訂票, my bookings | get_user_bookings/profile |
| `cancel` | 取消 (without policy words) | cancel_booking |
| `delay` | 延誤, disruption | get_delay_ripple |
| `confirm` | 確認, ok, yes | make_booking (from history) |

This reduces the LLM's job from "choose 1 of 14 tools" to "just answer the question with the data provided."

#### Optimization 10: Automatic Date Extraction

New `_extract_date()` extracts dates in `YYYY-MM-DD` or `YYYY/MM/DD` format from user messages, eliminating reliance on the LLM to parse dates.

#### Optimization 11: Multi-Step Booking Chain

New `_chain_booking_query()` automatically executes three database calls in sequence when a booking query is detected:

```
Step 1: check_national_rail_availability(origin, destination, date)
    ↓ extract schedule_id, stops_travelled
Step 2: get_national_rail_fare(schedule_id, fare_class, stops)  [for EACH schedule]
    ↓ extract fare info
Step 3: get_available_seats(schedule_id, date, fare_class)
    ↓ return all data to LLM for response
```

This ensures the user gets complete information (schedules + fares + seats) in a single turn, even with the small LLM that cannot call multiple tools.

#### Optimization 12: Cancel vs Policy Smart Classification

**Problem:** "如果取消可以退多少？" was classified as `cancel` (action) instead of `policy` (information).

**Solution:** Added `_policy_override_kw` set. If both cancel keywords AND policy-like words (多少, 政策, 如何, 可以退) are present, classify as `policy`:

```python
if any(kw in lower for kw in cancel_kw):
    if any(kw in lower for kw in policy_override_kw):
        return "policy"  # Asking about policy, not actually cancelling
    return "cancel"      # Actually wants to cancel
```

#### Optimization 13: Pre-Login Check

Booking queries now check login status **before** running the chain. If not logged in, the chain still runs (to show available info) but appends a login reminder so the user sees data AND knows they need to log in.

#### Optimization 14: Ticket Type Extraction

New `_extract_ticket_type()` detects return/round-trip tickets from keywords:
- English: "return", "round trip"
- Chinese: "來回", "來回票", "往返"

#### Optimization 15: Seat Preference Extraction

New `_extract_seat_preference()` detects seat preferences:
- Window: "window", "靠窗", "窗邊"
- Aisle: "aisle", "走道", "靠走道"

#### Optimization 16: Multi-Schedule Display

`SYSTEM_PROMPT` instructs the LLM to list ALL available schedules with numbers when multiple options exist, allowing the user to choose which one to book.

#### Optimization 17: Confirmation Message Format

`SYSTEM_PROMPT` includes a mandatory booking confirmation template with `schedule_id`, station IDs, date, fare, and seat — ensuring the system can recover these details when the user confirms.

#### Optimization 18: Forward Search Order (BUG FIX #2)

**Problem:** `_recover_booking_context` searched history in `reversed` order, finding wrong `schedule_id` (e.g. NR_SCH03 instead of NR_SCH01).

**Solution:** Removed `reversed`, searching forward to find the first (correct) schedule ID.

#### Optimization 19: User-Only Fare Class (BUG FIX #3)

**Problem:** `_recover_booking_context` extracted `fare_class` from ALL messages including AI responses, which often contained "first" in descriptions of other options, causing `fare_class: first` when user asked for `standard`.

**Solution:** Extract `fare_class` only from USER messages (`role == "user"`), not from assistant responses.

#### Optimization 20: Continuation Dialog Detection (BUG FIX #4)

**Problem:** User asks "NR01到NR05有什麼班次？" → sees results → says "幫我訂第一班". The second message has no station IDs, so pre-classifier defaults to `general`.

**Solution:** When category is `general` but message contains booking keywords (訂, book, ticket, 第一班), automatically check conversation history for station IDs and dates:

```python
if category == "general":
    if any(kw in message for kw in booking_continuation_kw):
        hist_stations = _extract_station_ids(history_text)
        if len(hist_stations) >= 2:
            category = "booking"  # Recovered from history!
```

#### Optimization 21: Regex Fix for Chinese Text

Changed station ID regex from `r'\b(MS\d{2}|NR\d{2})\b'` to `r'(MS\d{2}|NR\d{2})'`. The `\b` word boundary fails when Chinese characters are adjacent to station IDs (e.g. "MS01到MS09"), because Chinese characters are word characters in Python's Unicode regex.

---

### 2. `skeleton/ui.py` — 4 Optimizations

`# TASK 6 EXTENSION` comment: Lines 1–2

#### Welcome Message
Auto-displayed on startup with feature overview (🚂🚇🗺️🎫📋).

#### Quick-Select Station Buttons
Sidebar buttons for 6 metro + 6 national rail stations with Chinese names and IDs.

#### Login Panel Auto-Close
Login/register panels collapse after successful authentication.

#### Full Chinese Localization
All UI elements: title, buttons, labels, placeholders, error messages.

---

## Testing Evidence

### Tool Selection Accuracy

| Query | Expected Tool | v1 Result | v4 Result |
|-------|--------------|-----------|-----------|
| `NR01到NR05有哪些班次？` | check_national_rail_availability | ❌ get_national_rail_fare | ✅ Direct call |
| `MS01到MS09有哪些捷運？` | check_metro_availability | ❌ get_metro_fare | ✅ Direct call |
| `從MS01到MS14最快怎麼走？` | find_route | ✅ find_route | ✅ Direct call |
| `退款政策是什麼？` | search_policy | ❌ get_payment_info | ✅ Direct call |
| `你好` | No tool | ❌ get_user_bookings | ✅ Greeting skip |
| `跨網絡 MS01→NR05` | find_route | ✅ find_route | ✅ Direct call |

### Booking Flow Test

| Step | Input | Result |
|------|-------|--------|
| 1. Query | `幫我訂 2026-06-15 從 NR01 到 NR05 的 standard ticket` | ✅ Chain: availability→fare→seats |
| 2. Confirm | `確認` | ✅ Context recovered, booking created (BK-PJTJCT) |

### Multi-Step Chaining

| Query | Tools Called (v1) | Tools Called (v4) |
|-------|------------------|------------------|
| Booking with fare+seats | 1 tool (availability only) | 3-4 tools (availability + fare per schedule + seats) |
| Fare query | 1 tool (wrong one) | 2 tools (availability → fare) |

### Bug Fix Verification

| Bug | Test Case | Before | After |
|-----|-----------|--------|-------|
| #1 Station dedup | `Bridgeport NR06 到 Central Station NR01` | NR06→NR06 | NR06→NR01 ✅ |
| #2 Schedule recovery | Confirm after booking query | NR_SCH03 (wrong) | NR_SCH01 ✅ |
| #3 Fare class | `standard ticket` → confirm | first (wrong) | standard ✅ |
| #4 Continuation | `NR01到NR05班次？` → `訂第一班` | general (no action) | booking ✅ |
| #5 hops=0 | `MS15 hops=0` | hops=2 (0 treated as False) | hops=0 → only MS15 ✅ |
| #6 Avoid keyword | `MS01→NR10 avoid MS07` | find_route (no avoid) | find_alternative_routes ✅ |
| #7 Cross-network network | `MS01→NR10 avoid MS07` | network="metro" → [] | network="auto" ✅ |

---

## Additional Tools Added (v4 final)

Two tools from the Gemini integration were added to complete the toolset:

#### `get_national_rail_schedule_fares`

Retrieves all fare classes for a specific schedule ID (e.g. NR_SCH04). Useful when the user already knows which service they want and just needs the price.

```python
elif tool_name == "get_national_rail_schedule_fares":
    result = query_national_rail_schedule_fares(params["schedule_id"])
    if not result:
        return json.dumps({"error": f"找不到班次 {params['schedule_id']} 的票價資料。"})
```

Pre-classification category: `schedule_fare` — triggered when user mentions a schedule ID with fare keywords.

#### `get_station_connections`

Lists direct graph connections from a station. Useful for adjacency questions ("what stations connect to MS01?") and for explaining the INTERCHANGE_TO relationships that cause delay ripple effects.

```python
elif tool_name == "get_station_connections":
    result = query_station_connections(params["station_id"])
```

Pre-classification category: `connections` — triggered by keywords: adjacent, neighbour, 相鄰, 直接連.

---

## Summary

| Category | Count |
|---------|-------|
| New database tools | 4 (`get_user_profile`, `get_payment_info`, `get_national_rail_schedule_fares`, `get_station_connections`) |
| New helper functions | 10 (`_is_greeting`, `_is_confirmation`, `_extract_date`, `_extract_station_ids`, `_extract_ticket_type`, `_extract_seat_preference`, `_extract_fare_class`, `_pre_classify_query`, `_chain_booking_query`, `_recover_booking_context`) |
| Chinese station mappings | 30 |
| Chinese policy translations | 15 |
| Pre-classification categories | 12 |
| Bug fixes | 7 |
| Total optimizations | 24 |
| Files modified | 2 (`skeleton/agent.py`, `skeleton/ui.py`) |
