# TASK 6 EXTENSION: added get_user_profile and get_payment_info tools,
# Chinese keyword support, booking confirmation gate, human-friendly prompts,
# stronger fallback logic, greeting protection, Chinese policy query translation,
# pre-classification for tool routing, automatic date extraction, multi-step chaining,
# booking confirmation context recovery, cancel vs policy classification fix,
# pre-login check, ticket type extraction, seat preference detection
"""
TransitFlow — Intelligent Agent
================================
This is the brain of the system.

HOW IT WORKS (the pipeline students should understand):
  1. User asks a natural language question
  2. Pre-classification determines the query category using keywords
  3. The correct database tools are called automatically
  4. The LLM reads all the data and writes a helpful answer
  5. The answer is returned to the Gradio UI

THE THREE DATABASE ROLES IN THIS FILE:
  - Relational (PostgreSQL)  → schedules, fares, bookings, seat layouts, users
  - Vector (pgvector / RAG)  → policy documents (refunds, conduct, luggage, etc.)
  - Graph (Neo4j)            → route finding, delay ripple, cross-network paths

OPTIMIZATIONS (v4):
  1.  Chinese keyword & station name support (30 mappings)
  2.  Added get_user_profile and get_payment_info tools
  3.  Human-friendly system prompt and error messages
  4.  Booking confirmation mechanism with context recovery
  5.  Structured, emoji-enhanced response formatting
  6.  Stronger fallback: overrides wrong tool selections
  7.  Greeting protection: skip tool calls for simple greetings
  8.  Chinese policy query translation for vector search
  9.  Pre-classification: categorize query BEFORE LLM (14→2-4 tools)
  10. Automatic date extraction from natural language
  11. Multi-step chaining: booking queries auto-call availability+fare+seats
  12. Cancel vs policy smart classification
  13. Pre-login check: prompt login BEFORE running booking chain
  14. Ticket type extraction (single/return from 單程/來回)
  15. Seat preference extraction (window/aisle from 靠窗/走道)
  16. Multi-schedule selection: list options for user to choose
  17. Stronger confirmation message format in SYSTEM_PROMPT
"""

from __future__ import annotations

import json
import re
from datetime import date
from typing import Optional

from skeleton.llm_provider import llm
from databases.relational.queries import (
    query_national_rail_availability,
    query_national_rail_fare,
    query_metro_schedules,
    query_metro_fare,
    query_available_seats,
    auto_select_adjacent_seats,
    query_user_profile,
    query_user_bookings,
    query_payment_info,
    execute_booking,
    execute_cancellation,
    query_policy_vector_search,
)
from databases.graph.queries import (
    query_shortest_route,
    query_cheapest_route,
    query_alternative_routes,
    query_interchange_path,
    query_delay_ripple,
)


# ── Station name → ID lookup ─────────────────────────────────────────────────

_STATION_INDEX: dict[str, str] = {
    # Metro — English
    "central square": "MS01", "riverside":     "MS02", "northgate":    "MS03",
    "elm park":       "MS04", "westfield":     "MS05", "harbour view": "MS06",
    "old town":       "MS07", "university":    "MS08", "queensbridge": "MS09",
    "parkside":       "MS10", "greenhill":     "MS11", "lakeshore":    "MS12",
    "clifton":        "MS13", "eastwick":      "MS14", "ferndale":     "MS15",
    "hilltop":        "MS16", "broadmoor":     "MS17", "sunnyvale":    "MS18",
    "redwood":        "MS19", "thornton":      "MS20",
    # Metro — 中文
    "中央廣場": "MS01", "河濱站": "MS02", "北門站":       "MS03",
    "榆樹公園站": "MS04", "西田站": "MS05", "海港景站":   "MS06",
    "舊城站":   "MS07", "大學站": "MS08", "皇后橋站":    "MS09",
    "公園側站": "MS10", "綠丘站": "MS11", "湖岸站":      "MS12",
    "克利夫頓站": "MS13", "東威克站": "MS14", "芬戴爾站": "MS15",
    "山頂站":   "MS16", "寬地站": "MS17", "陽光谷站":    "MS18",
    "紅木站":   "MS19", "桑頓站": "MS20",
    # National Rail — English (longer names first)
    "central station":   "NR01", "maplewood":     "NR02",
    "old town junction": "NR03", "ashford":        "NR04",
    "stonehaven":        "NR05", "bridgeport":     "NR06",
    "ferndale halt":     "NR07", "coalport":       "NR08",
    "dunmore":           "NR09", "langford end":   "NR10",
    # National Rail — 中文
    "中央站":       "NR01", "楓木站":         "NR02",
    "舊城交匯站":   "NR03", "阿什福德站":     "NR04",
    "石港站":       "NR05", "橋港站":         "NR06",
    "芬戴爾停靠站": "NR07", "煤港站":         "NR08",
    "丹摩站":       "NR09", "蘭福德終點站":   "NR10",
}


# ── Chinese → English policy translation ─────────────────────────────────────

_POLICY_TRANSLATION: dict[str, str] = {
    "退款": "refund cancellation policy",
    "退票": "refund cancellation policy",
    "取消": "cancellation refund policy",
    "補償": "delay compensation policy",
    "延誤": "delay compensation policy",
    "誤點": "delay compensation policy",
    "行李": "luggage baggage policy",
    "寵物": "pet animal travel policy",
    "腳踏車": "bicycle bike travel policy",
    "自行車": "bicycle bike travel policy",
    "兒童": "child fare discount policy",
    "小孩": "child fare discount policy",
    "票種": "ticket types single return day pass",
    "票價": "fare pricing ticket cost",
    "規定": "rules policy regulations",
    "政策": "company policy rules",
    "食物": "food drink policy onboard",
    "飲料": "food drink policy onboard",
    "逃票": "fare evasion penalty",
    "罰款": "fare evasion penalty",
    "訂票規則": "booking rules policy",
}


def _translate_policy_query(query: str) -> str:
    """Translate Chinese policy keywords to English for vector search."""
    translations = []
    for zh, en in _POLICY_TRANSLATION.items():
        if zh in query:
            translations.append(en)
    return " ".join(translations) if translations else query


def _inject_station_ids(text: str) -> str:
    """Replace station names with 'name (ID)' for LLM context."""
    result = text
    seen_ids: set[str] = set()
    for name in sorted(_STATION_INDEX, key=len, reverse=True):
        sid = _STATION_INDEX[name]
        if sid in seen_ids:
            continue
        pattern = re.compile(re.escape(name), re.IGNORECASE)
        if pattern.search(result):
            result = pattern.sub(f"{name} ({sid})", result)
            seen_ids.add(sid)
    return result


# ── Greeting detection ────────────────────────────────────────────────────────

_GREETING_PATTERNS = {
    "你好", "您好", "嗨", "哈囉", "早安", "午安", "晚安",
    "hello", "hi", "hey", "good morning", "good afternoon", "good evening",
    "howdy", "greetings", "yo", "sup",
}


def _is_greeting(text: str) -> bool:
    """Return True if the message is a simple greeting."""
    clean = text.strip().lower().rstrip("!！。.~")
    if clean in _GREETING_PATTERNS:
        return True
    if len(clean) < 10:
        for g in _GREETING_PATTERNS:
            if clean.startswith(g):
                return True
    return False


# ── Date extraction ───────────────────────────────────────────────────────────

def _extract_date(text: str) -> Optional[str]:
    """Extract a date in YYYY-MM-DD format from text."""
    match = re.search(r'(\d{4}-\d{2}-\d{2})', text)
    if match:
        return match.group(1)
    match = re.search(r'(\d{4})/(\d{2})/(\d{2})', text)
    if match:
        return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"
    return None


# ── Station ID extraction ────────────────────────────────────────────────────

def _extract_station_ids(text: str) -> list[str]:
    """Extract all station IDs (MS01, NR05, etc.) from text."""
    return [sid.upper() for sid in re.findall(r'(MS\d{2}|NR\d{2})', text, re.IGNORECASE)]


# ── Ticket type extraction ───────────────────────────────────────────────────
# OPTIMIZATION v4.14: Detect whether the user wants a single or return ticket.

def _extract_ticket_type(text: str) -> str:
    """Extract ticket type from text. Defaults to 'single'."""
    lower = text.lower()
    return_kw = {"return", "round trip", "round-trip", "來回", "來回票", "往返"}
    if any(kw in lower for kw in return_kw):
        return "return"
    return "single"


# ── Seat preference extraction ────────────────────────────────────────────────
# OPTIMIZATION v4.15: Detect whether user wants a window or aisle seat.

def _extract_seat_preference(text: str) -> Optional[str]:
    """Extract seat preference from text."""
    lower = text.lower()
    if any(kw in lower for kw in ["window", "靠窗", "窗邊", "窗戶"]):
        return "window"
    if any(kw in lower for kw in ["aisle", "走道", "靠走道"]):
        return "aisle"
    return None


# ── Fare class extraction ────────────────────────────────────────────────────

def _extract_fare_class(text: str) -> str:
    """Extract fare class from text. Defaults to 'standard'."""
    lower = text.lower()
    if any(kw in lower for kw in ["first class", "first", "頭等", "商務", "一等"]):
        return "first"
    return "standard"


# ── Pre-classification ────────────────────────────────────────────────────────
# OPTIMIZATION v4.9 + v4.12: Categorize queries with smarter cancel vs policy.

def _pre_classify_query(text: str, station_ids: list[str], has_date: bool,
                        current_user_email: Optional[str]) -> str:
    """
    Classify a user query into a category:
    greeting, route, availability, booking, fare, policy, personal,
    cancel, delay, confirm, general
    """
    lower = text.lower()
    two_stations = len(station_ids) >= 2
    is_cross_network = (
        two_stations and
        station_ids[0][:2] != station_ids[1][:2]
    )

    # ── Confirmation detection (OPTIMIZATION v4.4) ────────────────────
    confirm_words = {"confirm", "yes", "確認", "好", "ok", "好的", "沒問題", "訂吧", "訂了"}
    clean = lower.strip().rstrip("!！。.~")
    if clean in confirm_words or (len(clean) < 15 and any(w in clean for w in confirm_words)):
        return "confirm"

    # ── Route keywords ────────────────────────────────────────────────
    route_kw = {
        "fastest", "quickest", "shortest", "cheapest", "route", "path",
        "directions", "how to get", "how do i get", "way from",
        "最快", "最短", "最便宜", "怎麼去", "如何前往", "怎麼走",
        "如何去", "如何搭", "怎麼搭", "路線", "轉乘",
    }

    # ── Booking / ticket / seat keywords ──────────────────────────────
    booking_kw = {
        "book", "booking", "ticket", "seat", "buy", "purchase", "reserve",
        "訂票", "訂位", "買票", "座位", "訂", "購買", "靠窗", "first class",
        "standard", "single ticket", "return ticket",
    }

    # ── Availability / schedule keywords ──────────────────────────────
    avail_kw = {
        "train", "trains", "schedule", "timetable", "service", "services",
        "available", "availability", "what runs", "are there",
        "班次", "時刻表", "列車", "有沒有車", "幾點有車", "有哪些",
        "哪些班次", "查車",
    }

    # ── Fare / price keywords ─────────────────────────────────────────
    fare_kw = {
        "fare", "price", "cost", "how much", "票價", "多少錢", "價格", "費用",
    }

    # ── Policy keywords ───────────────────────────────────────────────
    policy_kw = {
        "refund", "policy", "compensation", "luggage", "bicycle", "pet",
        "conduct", "rules", "regulation",
        "退款", "補償", "政策", "行李", "寵物", "腳踏車", "規定",
        "延誤", "誤點", "逃票", "罰款",
    }

    # ── Personal keywords ─────────────────────────────────────────────
    personal_kw = {
        "my booking", "my ticket", "my trip", "my account", "my profile",
        "show my", "view my", "my history",
        "我的訂票", "我的票", "我的帳號", "我的資料", "訂票紀錄",
    }

    # ── Cancel keywords ───────────────────────────────────────────────
    cancel_kw = {"cancel", "cancellation", "取消", "退訂"}

    # ── Delay keywords ────────────────────────────────────────────────
    delay_kw = {"delay", "disruption", "closed", "affected", "ripple",
                "延誤", "關閉", "影響"}

    # ── OPTIMIZATION v4.12: Cancel vs Policy smart classification ─────
    # If the user mentions "cancel" but also asks "how much refund",
    # "what's the policy", etc., it's a POLICY question, not a cancel action.
    _policy_override_kw = {
        "多少", "政策", "如何", "怎麼", "可以退", "退多少", "規定",
        "how much", "what is", "what's", "policy", "refund amount",
    }

    # Priority-based classification (order matters!)

    # 1. Cross-network always = route
    if is_cross_network and two_stations:
        return "route"

    # 2. Route keywords with stations
    if any(kw in lower for kw in route_kw) and two_stations:
        return "route"

    # 3. Cancel vs Policy (SMART)
    if any(kw in lower for kw in cancel_kw):
        # If also has policy-like words → it's a policy question
        if any(kw in lower for kw in _policy_override_kw):
            return "policy"
        # If also has policy keywords → it's a policy question
        if any(kw in lower for kw in policy_kw):
            return "policy"
        # Pure cancel intent
        return "cancel"

    # 4. Booking / seat / ticket (with stations)
    if any(kw in lower for kw in booking_kw) and two_stations:
        return "booking"

    # 5. Fare only
    if any(kw in lower for kw in fare_kw) and two_stations:
        return "fare"

    # 6. Availability / schedule
    if any(kw in lower for kw in avail_kw) and two_stations:
        return "availability"

    # 7. Two stations but no specific keyword → default availability
    if two_stations:
        return "availability"

    # 8. Policy
    if any(kw in lower for kw in policy_kw):
        return "policy"

    # 9. Personal
    if any(kw in lower for kw in personal_kw):
        return "personal"

    # 10. Delay
    if any(kw in lower for kw in delay_kw):
        return "delay"

    return "general"


# ── Tool filtering ────────────────────────────────────────────────────────────

_CATEGORY_TOOLS: dict[str, list[str]] = {
    "route": ["find_route", "find_alternative_routes"],
    "availability": ["check_national_rail_availability", "check_metro_availability"],
    "booking": ["check_national_rail_availability", "get_available_seats",
                "get_national_rail_fare", "make_booking"],
    "fare": ["get_national_rail_fare", "get_metro_fare", "calculate_metro_fare",
             "check_national_rail_availability", "check_metro_availability"],
    "policy": ["search_policy"],
    "personal": ["get_user_bookings", "get_user_profile", "get_payment_info"],
    "cancel": ["cancel_booking", "get_user_bookings"],
    "delay": ["get_delay_ripple"],
    "general": [],
}


def _filter_tools(tools: list[dict], category: str) -> list[dict]:
    allowed = _CATEGORY_TOOLS.get(category)
    if allowed is None:
        return tools
    return [t for t in tools if t["name"] in allowed]


# ── Booking confirmation context recovery ─────────────────────────────────────
# OPTIMIZATION v4.4: When user says "確認", parse conversation history to
# recover booking details (origin, destination, date, fare_class, etc.)
# that were discussed in previous messages.

def _recover_booking_context(history: list[dict]) -> Optional[dict]:
    """
    Scan conversation history to recover booking details.
    Looks for station IDs, dates, fare class, schedule IDs in both
    user messages and assistant messages (which contain tool results).
    Returns a dict with recovered params, or None if insufficient.
    """
    all_text = ""
    for msg in reversed(history[-8:]):  # Look at last 8 messages
        all_text += " " + msg.get("content", "")

    # Extract station IDs
    station_ids = _extract_station_ids(all_text)
    if len(station_ids) < 2:
        return None

    # Extract schedule ID (e.g. NR_SCH01)
    schedule_match = re.search(r'(NR_SCH\d+|MS_SCH\d+)', all_text)
    schedule_id = schedule_match.group(1) if schedule_match else None

    # Extract date
    travel_date = _extract_date(all_text)

    # Extract fare class
    fare_class = _extract_fare_class(all_text)

    # Extract ticket type
    ticket_type = _extract_ticket_type(all_text)

    # We need at least origin, destination, and schedule_id
    if not schedule_id:
        return None

    # Determine origin and destination from the station IDs
    # Use the first two unique IDs found
    origin_id = station_ids[0]
    destination_id = station_ids[1] if len(station_ids) > 1 else None

    if not destination_id:
        return None

    return {
        "schedule_id": schedule_id,
        "origin_station_id": origin_id,
        "destination_station_id": destination_id,
        "travel_date": travel_date or date.today().isoformat(),
        "fare_class": fare_class,
        "seat_id": "any",
        "ticket_type": ticket_type,
    }


# ── System prompt ─────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are TransitFlow, a friendly and patient transit assistant for a dual-network system.

Networks: City Metro MS01-MS20 (lines M1-M4) | National Rail NR01-NR10 (lines NR1-NR2)
Interchanges: Central=MS01/NR01 | Old Town=MS07/NR03 | Ferndale=MS15/NR07
Today: {today}

PERSONALITY:
- Be warm, helpful, and patient like a real customer service agent.
- Never show raw error codes or technical messages to the user.
- When data is not found, apologise and suggest next steps.
- Always end with an offer to help further.

RESPONSE FORMAT:
- Use emojis to make responses easier to read (🚂 for trains, 🚇 for metro, 💰 for fares, 💺 for seats, 🗺️ for routes, 📋 for policies).
- For schedules, use clear sections with labels.
- For routes, list every station in order and highlight interchange points.
- Keep responses concise but complete.

ERROR HANDLING:
- If no schedule found: "很抱歉，找不到相關班次。請確認站點代碼是否正確，或嘗試其他日期。"
- If no route found: "很抱歉，找不到這兩站之間的路線。建議您查看是否需要換乘。"
- If not logged in for booking: "您需要先登入才能訂票，請點右上角的登入按鈕 😊"

BOOKING CONFIRMATION RULE (CRITICAL):
- When showing booking details to the user, ALWAYS format like this:
  📋 訂票摘要：
  🚂 路線：[origin_name] ([origin_id]) → [dest_name] ([dest_id])
  📅 日期：[travel_date]
  🎫 票種：[ticket_type] (single/return)
  💺 座位等級：[fare_class] (standard/first)
  💰 票價：$[fare]
  🪑 座位：[seat_id]

  請回覆「確認」以完成訂票，或告訴我需要修改的地方。
- NEVER call make_booking unless the user has explicitly said "confirm", "yes", "確認", "好", or "ok".
- Include the schedule_id, origin_id, destination_id in your confirmation message so the system can recover them.

MULTI-SCHEDULE RULE:
- When multiple schedules are found, list ALL of them with numbers:
  1️⃣ NR_SCH01 - NR1 線 北行 (06:00-22:30, 每30分鐘)
  2️⃣ NR_SCH04 - NR2 線 西行 (07:00-22:45, 每45分鐘)
  請告訴我您要搭哪一班？

LOGIN RULE: Routes, fares, schedules, and policies work WITHOUT login. Only make_booking and cancel_booking need login.

When DATA FROM TRANSITFLOW DATABASE is provided, use it as the only source of truth.
For route results: list every station name in order, note any line changes, and give the total travel time.
Always reply in the same language as the user.
""".format(today=date.today().isoformat())


# ── Tool definitions ──────────────────────────────────────────────────────────

TOOLS = [
    {
        "name": "check_national_rail_availability",
        "description": "Check available national rail trains between two NR stations.",
        "parameters": {
            "origin_id":      {"type": "string", "description": "NR station ID e.g. NR01"},
            "destination_id": {"type": "string", "description": "NR station ID e.g. NR05"},
            "travel_date":    {"type": "string", "description": "YYYY-MM-DD (optional)"},
        },
        "required": ["origin_id", "destination_id"],
    },
    {
        "name": "get_national_rail_fare",
        "description": "Calculate fare for a national rail journey.",
        "parameters": {
            "schedule_id":     {"type": "string", "description": "e.g. NR_SCH01"},
            "fare_class":      {"type": "string", "description": "standard or first"},
            "stops_travelled": {"type": "integer", "description": "Number of stops"},
        },
        "required": ["schedule_id", "fare_class", "stops_travelled"],
    },
    {
        "name": "check_metro_availability",
        "description": "Check available metro services between two MS stations.",
        "parameters": {
            "origin_id":      {"type": "string", "description": "MS station ID e.g. MS01"},
            "destination_id": {"type": "string", "description": "MS station ID e.g. MS09"},
        },
        "required": ["origin_id", "destination_id"],
    },
    {
        "name": "calculate_metro_fare",
        "description": "Calculate metro fare for a journey.",
        "parameters": {
            "schedule_id":     {"type": "string", "description": "e.g. MS_SCH01"},
            "stops_travelled": {"type": "integer", "description": "Number of stops"},
        },
        "required": ["schedule_id", "stops_travelled"],
    },
    {
        "name": "get_metro_fare",
        "description": "Get metro ticket price between two stations.",
        "parameters": {
            "origin_id":      {"type": "string", "description": "MS station ID e.g. MS01"},
            "destination_id": {"type": "string", "description": "MS station ID e.g. MS09"},
        },
        "required": ["origin_id", "destination_id"],
    },
    {
        "name": "get_user_bookings",
        "description": "Get logged-in user's booking history. Requires login.",
        "parameters": {},
        "required": [],
    },
    {
        "name": "get_user_profile",
        "description": "Get logged-in user's profile info. Requires login.",
        "parameters": {},
        "required": [],
    },
    {
        "name": "get_payment_info",
        "description": "Get payment details for a booking. Requires login.",
        "parameters": {
            "booking_id": {"type": "string", "description": "e.g. BK-A1B2C3"},
        },
        "required": ["booking_id"],
    },
    {
        "name": "get_available_seats",
        "description": "Show available seats for a national rail service.",
        "parameters": {
            "schedule_id":  {"type": "string", "description": "e.g. NR_SCH01"},
            "travel_date":  {"type": "string", "description": "YYYY-MM-DD"},
            "fare_class":   {"type": "string", "description": "standard or first"},
        },
        "required": ["schedule_id", "travel_date", "fare_class"],
    },
    {
        "name": "make_booking",
        "description": "Create a booking. REQUIRES LOGIN and explicit user confirmation.",
        "parameters": {
            "schedule_id":            {"type": "string", "description": "e.g. NR_SCH01"},
            "origin_station_id":      {"type": "string", "description": "e.g. NR01"},
            "destination_station_id": {"type": "string", "description": "e.g. NR05"},
            "travel_date":            {"type": "string", "description": "YYYY-MM-DD"},
            "fare_class":             {"type": "string", "description": "standard or first"},
            "seat_id":                {"type": "string", "description": "e.g. B05 or 'any'"},
            "ticket_type":            {"type": "string", "description": "single or return"},
        },
        "required": ["schedule_id", "origin_station_id", "destination_station_id",
                      "travel_date", "fare_class", "seat_id"],
    },
    {
        "name": "cancel_booking",
        "description": "Cancel a booking. REQUIRES LOGIN and confirmation.",
        "parameters": {
            "booking_id": {"type": "string", "description": "e.g. BK-A1B2C3"},
        },
        "required": ["booking_id"],
    },
    {
        "name": "search_policy",
        "description": "Search policy documents (refunds, compensation, luggage, etc.).",
        "parameters": {
            "query": {"type": "string", "description": "Question about policy"},
        },
        "required": ["query"],
    },
    {
        "name": "find_route",
        "description": "Find best route between two stations. Works across networks.",
        "parameters": {
            "origin_id":      {"type": "string", "description": "Station ID e.g. MS01 or NR01"},
            "destination_id": {"type": "string", "description": "Station ID e.g. MS09 or NR05"},
            "network":        {"type": "string", "description": "metro, rail, or auto"},
            "optimise_by":    {"type": "string", "description": "time or cost"},
        },
        "required": ["origin_id", "destination_id"],
    },
    {
        "name": "find_alternative_routes",
        "description": "Find routes avoiding a specific station.",
        "parameters": {
            "origin_id":        {"type": "string", "description": "e.g. NR01"},
            "destination_id":   {"type": "string", "description": "e.g. NR05"},
            "avoid_station_id": {"type": "string", "description": "e.g. NR03"},
            "network":          {"type": "string", "description": "metro, rail, or auto"},
        },
        "required": ["origin_id", "destination_id", "avoid_station_id"],
    },
    {
        "name": "get_delay_ripple",
        "description": "Show stations affected by a delay or disruption.",
        "parameters": {
            "station_id": {"type": "string", "description": "e.g. NR03 or MS07"},
            "hops":       {"type": "integer", "description": "Connections to check (default 2)"},
        },
        "required": ["station_id"],
    },
]

TOOLS_SCHEMA = """\
find_route(origin_id, destination_id, optimise_by?)
check_national_rail_availability(origin_id, destination_id, travel_date?)
get_national_rail_fare(schedule_id, fare_class, stops_travelled)
check_metro_availability(origin_id, destination_id)
calculate_metro_fare(schedule_id, stops_travelled)
get_available_seats(schedule_id, travel_date, fare_class)
make_booking(schedule_id, origin_station_id, destination_station_id, travel_date, fare_class, seat_id, ticket_type?)
cancel_booking(booking_id)
get_user_bookings()
get_user_profile()
get_payment_info(booking_id)
search_policy(query)
find_alternative_routes(origin_id, destination_id, avoid_station_id, network?)
get_delay_ripple(station_id, hops?)"""


# ── Tool execution ────────────────────────────────────────────────────────────

def _execute_tool(tool_name: str, params: dict,
                  current_user_email: Optional[str] = None) -> str:
    """Execute a tool call and return JSON string."""
    try:
        if tool_name == "check_national_rail_availability":
            result = query_national_rail_availability(**params)

        elif tool_name == "get_national_rail_fare":
            result = query_national_rail_fare(**params)

        elif tool_name == "check_metro_availability":
            result = query_metro_schedules(
                origin_id=params["origin_id"],
                destination_id=params["destination_id"],
            )

        elif tool_name == "calculate_metro_fare":
            result = query_metro_fare(**params)

        elif tool_name == "get_metro_fare":
            schedules = query_metro_schedules(
                origin_id=params["origin_id"],
                destination_id=params["destination_id"],
            )
            if not schedules:
                result = {"error": "很抱歉，找不到這兩站之間的捷運服務。"}
            else:
                sched = schedules[0]
                stops = sched.get("stops_in_order") or []
                if isinstance(stops, str):
                    stops = json.loads(stops)
                try:
                    n_stops = stops.index(params["destination_id"]) - stops.index(params["origin_id"])
                except ValueError:
                    n_stops = 1
                fare = query_metro_fare(sched["schedule_id"], n_stops)
                result = {
                    "origin": sched.get("origin_name", params["origin_id"]),
                    "destination": sched.get("destination_name", params["destination_id"]),
                    "line": sched.get("line"),
                    "schedule_id": sched["schedule_id"],
                    "stops": n_stops,
                    **(fare or {"error": "票價查詢失敗"}),
                }

        elif tool_name == "get_user_bookings":
            if not current_user_email:
                return json.dumps({"error": "您尚未登入。請點右上角的登入按鈕後再試 😊"})
            result = query_user_bookings(current_user_email)

        elif tool_name == "get_user_profile":
            if not current_user_email:
                return json.dumps({"error": "您尚未登入。請點右上角的登入按鈕後再試 😊"})
            result = query_user_profile(current_user_email)
            if result is None:
                return json.dumps({"error": "找不到使用者資料，請重新登入。"})

        elif tool_name == "get_payment_info":
            if not current_user_email:
                return json.dumps({"error": "您尚未登入。請點右上角的登入按鈕後再試 😊"})
            result = query_payment_info(params["booking_id"])
            if result is None:
                return json.dumps({"error": f"找不到訂單 {params['booking_id']} 的付款紀錄。"})

        elif tool_name == "get_available_seats":
            result = query_available_seats(**params)

        elif tool_name == "make_booking":
            if not current_user_email:
                return json.dumps({"error": "您尚未登入。請點右上角的登入按鈕後再試 😊"})
            profile = query_user_profile(current_user_email)
            if not profile:
                return json.dumps({"error": "找不到使用者資料，請重新登入。"})
            ok, data = execute_booking(
                user_id=profile["user_id"],
                schedule_id=params["schedule_id"],
                origin_station_id=params["origin_station_id"],
                destination_station_id=params["destination_station_id"],
                travel_date=params["travel_date"],
                fare_class=params["fare_class"],
                seat_id=params["seat_id"],
                ticket_type=params.get("ticket_type", "single"),
            )
            result = data if ok else {"error": f"訂票失敗：{data}"}

        elif tool_name == "cancel_booking":
            if not current_user_email:
                return json.dumps({"error": "您尚未登入。請點右上角的登入按鈕後再試 😊"})
            profile = query_user_profile(current_user_email)
            if not profile:
                return json.dumps({"error": "找不到使用者資料，請重新登入。"})
            ok, data = execute_cancellation(
                booking_id=params["booking_id"],
                user_id=profile["user_id"],
            )
            result = data if ok else {"error": f"取消失敗：{data}"}

        elif tool_name == "search_policy":
            raw_query = params["query"]
            search_query = _translate_policy_query(raw_query)
            embedding = llm.embed(search_query)
            docs = query_policy_vector_search(embedding)
            if not docs and search_query != raw_query:
                embedding = llm.embed(raw_query)
                docs = query_policy_vector_search(embedding)
            if not docs:
                return json.dumps({"error": "很抱歉，找不到相關政策資訊。請嘗試用不同的關鍵字搜尋。"})
            result = [
                {"title": d["title"], "category": d["category"],
                 "content": d["content"][:800], "similarity": round(d["similarity"], 3)}
                for d in docs
            ]

        elif tool_name == "find_route":
            origin_id = params["origin_id"]
            destination_id = params["destination_id"]
            network = params.get("network", "auto")
            optimise_by = params.get("optimise_by", "time")
            is_cross = (
                (origin_id.upper().startswith("MS") and destination_id.upper().startswith("NR")) or
                (origin_id.upper().startswith("NR") and destination_id.upper().startswith("MS"))
            )
            if is_cross:
                result = query_interchange_path(origin_id, destination_id)
            elif optimise_by == "cost":
                result = query_cheapest_route(origin_id, destination_id, network)
            else:
                result = query_shortest_route(origin_id, destination_id, network)

        elif tool_name == "find_alternative_routes":
            routes = query_alternative_routes(
                origin_id=params["origin_id"],
                destination_id=params["destination_id"],
                avoid_station_id=params["avoid_station_id"],
                network=params.get("network", "auto"),
            )
            result = [{"route_number": i + 1, "legs": r} for i, r in enumerate(routes)]

        elif tool_name == "get_delay_ripple":
            result = query_delay_ripple(
                delayed_station_id=params["station_id"],
                hops=params.get("hops", 2),
            )

        else:
            result = {"error": f"未知工具：{tool_name}"}

        return json.dumps(result, default=str)

    except Exception as e:
        return json.dumps({"error": f"很抱歉，系統發生錯誤：{str(e)}。請稍後再試。"})


# ── Helper functions ──────────────────────────────────────────────────────────

def _flatten_to_text(obj, depth: int = 0) -> str:
    pad = "  " * depth
    if isinstance(obj, dict):
        if not obj:
            return f"{pad}(empty)"
        lines = []
        for k, v in obj.items():
            if v is None:
                continue
            if isinstance(v, (dict, list)):
                inner = _flatten_to_text(v, depth + 1)
                if inner.strip():
                    lines.append(f"{pad}{k}:\n{inner}")
            else:
                lines.append(f"{pad}{k}: {v}")
        return "\n".join(lines) or f"{pad}(empty)"
    elif isinstance(obj, list):
        if not obj:
            return f"{pad}(no records)"
        parts = []
        for i, item in enumerate(obj, 1):
            if isinstance(item, (dict, list)):
                parts.append(f"{pad}[{i}]")
                parts.append(_flatten_to_text(item, depth + 1))
            else:
                parts.append(f"{pad}- {item}")
        return "\n".join(parts)
    else:
        return f"{pad}{obj}"


def _normalise_result(tool_name: str, result_json: str) -> str:
    try:
        data = json.loads(result_json)
    except json.JSONDecodeError:
        return result_json
    if isinstance(data, dict) and "error" in data:
        return f"Error: {data['error']}"
    return _flatten_to_text(data)


def _summarise_result(tool_name: str, result_json: str) -> str:
    return result_json


def _parse_tool_calls(llm_response: str) -> list[dict] | None:
    text = llm_response.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    decoder = json.JSONDecoder()
    for m in re.finditer(r'\{', text):
        try:
            data, _ = decoder.raw_decode(text, m.start())
            if "tool_calls" in data:
                return data["tool_calls"]
        except (json.JSONDecodeError, KeyError, ValueError):
            continue
    return None


# ── Multi-step booking chain ──────────────────────────────────────────────────

def _chain_booking_query(
    origin_id: str, destination_id: str,
    travel_date: Optional[str], fare_class: str,
    seat_preference: Optional[str],
    current_user_email: Optional[str],
    debug_info: list, debug: bool,
) -> list[dict]:
    """Auto-chain: availability → fare → seats for booking queries."""
    results = []

    # Step 1: Check availability
    avail_params = {"origin_id": origin_id, "destination_id": destination_id}
    if travel_date:
        avail_params["travel_date"] = travel_date
    if debug:
        debug_info.append(f"**Chain step 1:** check_national_rail_availability({avail_params})")
    avail_json = _execute_tool("check_national_rail_availability", avail_params, current_user_email)
    results.append({"tool": "check_national_rail_availability", "params": avail_params,
                     "result": avail_json, "summary": avail_json})

    # Parse to get schedule_id and stops
    try:
        avail_data = json.loads(avail_json)
        if isinstance(avail_data, list) and len(avail_data) > 0:
            # OPTIMIZATION v4.16: Process ALL schedules, not just first
            for sched in avail_data:
                schedule_id = sched.get("schedule_id")
                stops = sched.get("stops_travelled")

                if schedule_id and stops:
                    # Step 2: Get fare for each schedule
                    fare_params = {
                        "schedule_id": schedule_id,
                        "fare_class": fare_class,
                        "stops_travelled": stops,
                    }
                    if debug:
                        debug_info.append(f"**Chain step 2:** get_national_rail_fare({fare_params})")
                    fare_json = _execute_tool("get_national_rail_fare", fare_params, current_user_email)
                    results.append({"tool": "get_national_rail_fare", "params": fare_params,
                                     "result": fare_json, "summary": fare_json})

                # Step 3: Get seats (only for first schedule to save time)
                if schedule_id and travel_date and sched == avail_data[0]:
                    seat_params = {
                        "schedule_id": schedule_id,
                        "travel_date": travel_date,
                        "fare_class": fare_class,
                    }
                    if debug:
                        debug_info.append(f"**Chain step 3:** get_available_seats({seat_params})")
                    seat_json = _execute_tool("get_available_seats", seat_params, current_user_email)
                    results.append({"tool": "get_available_seats", "params": seat_params,
                                     "result": seat_json, "summary": seat_json})
    except (json.JSONDecodeError, KeyError, IndexError):
        pass

    # Add seat preference info for the LLM
    if seat_preference:
        results.append({
            "tool": "seat_preference",
            "params": {"preference": seat_preference},
            "result": json.dumps({"user_seat_preference": seat_preference}),
            "summary": json.dumps({"user_seat_preference": seat_preference}),
        })

    return results


# ── Main agent loop ───────────────────────────────────────────────────────────

def run_agent(
    user_message: str,
    history: list[dict],
    debug: bool = False,
    current_user_email: Optional[str] = None,
) -> tuple:
    debug_info = []

    # ── Step 0: Greeting protection ───────────────────────────────────
    if _is_greeting(user_message):
        if debug:
            debug_info.append("**Greeting detected** — skipping all tool calls.")
        answer = llm.chat(
            messages=history + [{"role": "user", "content": user_message}],
            system_prompt=SYSTEM_PROMPT,
        )
        updated_history = history + [
            {"role": "user", "content": user_message},
            {"role": "assistant", "content": answer},
        ]
        if debug:
            return answer, updated_history, "\n\n".join(debug_info)
        return answer, updated_history

    # ── Step 1: Pre-processing ────────────────────────────────────────
    _augmented_message = _inject_station_ids(user_message)
    _station_ids = _extract_station_ids(_augmented_message)
    _travel_date = _extract_date(user_message)
    _fare_class = _extract_fare_class(user_message)
    _ticket_type = _extract_ticket_type(user_message)
    _seat_pref = _extract_seat_preference(user_message)
    _lower = _augmented_message.lower()

    # ── Step 2: Pre-classify ──────────────────────────────────────────
    category = _pre_classify_query(
        _augmented_message, _station_ids, _travel_date is not None, current_user_email
    )
    if debug:
        debug_info.append(f"**Pre-classification:** {category}")

    # ── Step 3: Build context prompt ──────────────────────────────────
    if current_user_email:
        profile = query_user_profile(current_user_email)
        if profile:
            user_display = f"{profile['full_name']} (email: {current_user_email}, user_id: {profile['user_id']})"
        else:
            user_display = current_user_email
        contextual_prompt = SYSTEM_PROMPT + (
            f"\n\n目前登入使用者：{user_display}。"
            "請直接回答此使用者的個人訂票問題，不需要再詢問 email 或 ID。"
        )
    else:
        contextual_prompt = SYSTEM_PROMPT + (
            "\n\n目前沒有使用者登入。"
            "如果使用者詢問個人訂票或想要訂票、取消訂票，"
            "請友善地告知他們需要先登入：「您好！需要先登入您的帳號，請點右上角的登入按鈕 😊」"
        )

    # ── Step 4: Handle based on category ──────────────────────────────
    tool_results = []

    # ── CONFIRM: User confirmed a booking ─────────────────────────────
    if category == "confirm":
        if debug:
            debug_info.append("**Confirmation detected** — recovering booking context from history.")

        if not current_user_email:
            # Not logged in — can't book
            if debug:
                debug_info.append("**Booking gate:** not logged in.")
            tool_results.append({
                "tool": "login_required",
                "params": {},
                "result": json.dumps({"error": "您尚未登入。請點右上角的登入按鈕後再試 😊"}),
                "summary": json.dumps({"error": "您尚未登入。請點右上角的登入按鈕後再試 😊"}),
            })
        else:
            # Recover booking context from history
            booking_ctx = _recover_booking_context(history)
            if booking_ctx:
                if debug:
                    debug_info.append(f"**Recovered booking context:** {booking_ctx}")
                result_json = _execute_tool("make_booking", booking_ctx, current_user_email)
                tool_results.append({
                    "tool": "make_booking",
                    "params": booking_ctx,
                    "result": result_json,
                    "summary": result_json,
                })
            else:
                if debug:
                    debug_info.append("**Failed to recover booking context** — not enough info in history.")
                tool_results.append({
                    "tool": "context_missing",
                    "params": {},
                    "result": json.dumps({"error": "很抱歉，找不到之前的訂票資訊。請重新提供訂票細節（出發站、目的站、日期、座位等級）。"}),
                    "summary": json.dumps({"error": "找不到訂票資訊，請重新提供。"}),
                })

    # ── BOOKING: Pre-login check + multi-step chain ───────────────────
    elif category == "booking" and len(_station_ids) >= 2:
        # OPTIMIZATION v4.13: Check login BEFORE running the chain
        if not current_user_email:
            if debug:
                debug_info.append("**Pre-login check:** not logged in, prompting login before booking chain.")
            # Still run the chain to show info, but add login reminder
            tool_results = _chain_booking_query(
                origin_id=_station_ids[0], destination_id=_station_ids[1],
                travel_date=_travel_date, fare_class=_fare_class,
                seat_preference=_seat_pref,
                current_user_email=current_user_email,
                debug_info=debug_info, debug=debug,
            )
            tool_results.append({
                "tool": "login_reminder",
                "params": {},
                "result": json.dumps({"reminder": "使用者尚未登入。查詢資料已顯示，但訂票需要先登入。請提醒使用者點右上角的登入按鈕。"}),
                "summary": json.dumps({"reminder": "需要登入才能訂票"}),
            })
        else:
            tool_results = _chain_booking_query(
                origin_id=_station_ids[0], destination_id=_station_ids[1],
                travel_date=_travel_date, fare_class=_fare_class,
                seat_preference=_seat_pref,
                current_user_email=current_user_email,
                debug_info=debug_info, debug=debug,
            )

        # Add ticket type info
        if _ticket_type != "single":
            tool_results.append({
                "tool": "ticket_type_info",
                "params": {"ticket_type": _ticket_type},
                "result": json.dumps({"requested_ticket_type": _ticket_type}),
                "summary": json.dumps({"requested_ticket_type": _ticket_type}),
            })

    # ── ROUTE ─────────────────────────────────────────────────────────
    elif category == "route" and len(_station_ids) >= 2:
        _opt = "cost" if any(kw in _lower for kw in ["cheap", "cheapest", "最便宜"]) else "time"
        params = {"origin_id": _station_ids[0], "destination_id": _station_ids[1], "optimise_by": _opt}
        if debug:
            debug_info.append(f"**Direct call:** find_route({params})")
        result_json = _execute_tool("find_route", params, current_user_email)
        tool_results.append({"tool": "find_route", "params": params,
                              "result": result_json, "summary": result_json})

    # ── AVAILABILITY ──────────────────────────────────────────────────
    elif category == "availability" and len(_station_ids) >= 2:
        o, d = _station_ids[0], _station_ids[1]
        tool_name = "check_national_rail_availability" if o.startswith("NR") else "check_metro_availability"
        params = {"origin_id": o, "destination_id": d}
        if _travel_date:
            params["travel_date"] = _travel_date
        if debug:
            debug_info.append(f"**Direct call:** {tool_name}({params})")
        result_json = _execute_tool(tool_name, params, current_user_email)
        tool_results.append({"tool": tool_name, "params": params,
                              "result": result_json, "summary": result_json})

    # ── FARE ──────────────────────────────────────────────────────────
    elif category == "fare" and len(_station_ids) >= 2:
        o, d = _station_ids[0], _station_ids[1]
        if o.startswith("NR"):
            params = {"origin_id": o, "destination_id": d}
            if _travel_date:
                params["travel_date"] = _travel_date
            result_json = _execute_tool("check_national_rail_availability", params, current_user_email)
            tool_results.append({"tool": "check_national_rail_availability", "params": params,
                                  "result": result_json, "summary": result_json})
            try:
                data = json.loads(result_json)
                if isinstance(data, list) and data:
                    sched = data[0]
                    fare_params = {
                        "schedule_id": sched["schedule_id"],
                        "fare_class": _fare_class,
                        "stops_travelled": sched["stops_travelled"],
                    }
                    fare_json = _execute_tool("get_national_rail_fare", fare_params, current_user_email)
                    tool_results.append({"tool": "get_national_rail_fare", "params": fare_params,
                                          "result": fare_json, "summary": fare_json})
            except (json.JSONDecodeError, KeyError):
                pass
        else:
            params = {"origin_id": o, "destination_id": d}
            result_json = _execute_tool("get_metro_fare", params, current_user_email)
            tool_results.append({"tool": "get_metro_fare", "params": params,
                                  "result": result_json, "summary": result_json})

    # ── POLICY ────────────────────────────────────────────────────────
    elif category == "policy":
        params = {"query": user_message}
        if debug:
            debug_info.append(f"**Direct call:** search_policy({params})")
        result_json = _execute_tool("search_policy", params, current_user_email)
        tool_results.append({"tool": "search_policy", "params": params,
                              "result": result_json, "summary": result_json})

    # ── PERSONAL ──────────────────────────────────────────────────────
    elif category == "personal":
        filtered_tools = _filter_tools(TOOLS, category)
        if llm.get_chat_provider() == "ollama":
            tool_calls = llm.ollama_tool_call(
                history[-4:] if len(history) > 4 else history,
                filtered_tools, _augmented_message,
                system_prompt=(
                    "You are a tool router. "
                    f"Logged-in user: {current_user_email or 'none'}. "
                    "My bookings/tickets → get_user_bookings. "
                    "My account/profile → get_user_profile. "
                    "Payment for booking → get_payment_info(booking_id)."
                ),
            )
        else:
            tool_calls = [{"name": "get_user_bookings", "params": {}}]
        if debug:
            debug_info.append(f"**Tool selection (filtered {len(filtered_tools)} tools):** {tool_calls}")
        for call in tool_calls:
            name = call.get("name", "")
            params = call.get("params") or {}
            if any(v == "" for v in params.values()):
                continue
            result_json = _execute_tool(name, params, current_user_email)
            tool_results.append({"tool": name, "params": params,
                                  "result": result_json, "summary": result_json})

    # ── CANCEL ────────────────────────────────────────────────────────
    elif category == "cancel":
        # Extract booking ID from message
        bk_match = re.search(r'(BK-[A-Z0-9]+)', user_message, re.IGNORECASE)
        if bk_match:
            params = {"booking_id": bk_match.group(1)}
            if debug:
                debug_info.append(f"**Direct call:** cancel_booking({params})")
            result_json = _execute_tool("cancel_booking", params, current_user_email)
            tool_results.append({"tool": "cancel_booking", "params": params,
                                  "result": result_json, "summary": result_json})
        else:
            # No booking ID found — let LLM handle with filtered tools
            filtered_tools = _filter_tools(TOOLS, category)
            if llm.get_chat_provider() == "ollama":
                tool_calls = llm.ollama_tool_call(
                    history[-4:] if len(history) > 4 else history,
                    filtered_tools, _augmented_message,
                    system_prompt="Extract the booking ID and call cancel_booking.",
                )
            else:
                tool_calls = []
            if debug:
                debug_info.append(f"**Tool selection (filtered):** {tool_calls}")
            for call in tool_calls:
                name = call.get("name", "")
                params = call.get("params") or {}
                result_json = _execute_tool(name, params, current_user_email)
                tool_results.append({"tool": name, "params": params,
                                      "result": result_json, "summary": result_json})

    # ── DELAY ─────────────────────────────────────────────────────────
    elif category == "delay":
        if _station_ids:
            params = {"station_id": _station_ids[0], "hops": 2}
            if debug:
                debug_info.append(f"**Direct call:** get_delay_ripple({params})")
            result_json = _execute_tool("get_delay_ripple", params, current_user_email)
            tool_results.append({"tool": "get_delay_ripple", "params": params,
                                  "result": result_json, "summary": result_json})

    # category == "general" → no tools, just chat

    # ── Step 5: Compose the final answer ──────────────────────────────
    _DB_KEYWORDS = {
        "booking", "ticket", "schedule", "fare", "route", "seat",
        "train", "metro", "journey", "trip", "history", "reservation",
        "訂票", "班次", "票價", "路線", "座位", "捷運", "列車",
    }

    if tool_results:
        data_block = "\n\n".join(
            f"[{tr['tool']}]\n{_normalise_result(tr['tool'], tr['result'])}"
            for tr in tool_results
        )
        if debug:
            debug_info.append(f"**Data (normalised):**\n{data_block}")
        content = (
            f"DATA FROM TRANSITFLOW DATABASE:\n{data_block}"
            f"\n\nUser asks: {user_message}"
            f"\n\nAnswer using only the data above. Use emojis and clear formatting."
            f"\nIf this is a booking query, show ALL available schedules and ask which one the user wants."
            f"\nAlways include schedule_id, station IDs, and date in your response so the system can recover them later."
        )
    elif any(kw in user_message.lower() for kw in _DB_KEYWORDS):
        content = (
            f"User asks: {user_message}\n\n"
            "IMPORTANT: No data was retrieved. Do NOT invent any data. "
            "Apologise and suggest what the user can try."
        )
    else:
        content = user_message

    final_messages = history + [{"role": "user", "content": content}]
    answer = llm.chat(messages=final_messages, system_prompt=contextual_prompt)

    updated_history = history + [
        {"role": "user", "content": user_message},
        {"role": "assistant", "content": answer},
    ]

    if debug:
        return answer, updated_history, "\n\n".join(debug_info)
    return answer, updated_history
