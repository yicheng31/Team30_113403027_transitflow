"""
TransitFlow - Intelligent Agent

This is the brain of the system.

HOW IT WORKS:
1. User asks a natural language question.
2. The LLM decides which database tools to query.
3. Each database query runs and returns structured data.
4. The LLM reads the data and writes a helpful answer.
5. The answer is returned to the Gradio UI.
"""

from __future__ import annotations

import json
import re
from datetime import date
from typing import Optional

from databases.graph.queries import (
    query_alternative_routes,
    query_cheapest_route,
    query_delay_ripple,
    query_interchange_path,
    query_shortest_route,
    query_station_connections,
)
from databases.relational.queries import (
    execute_booking,
    execute_cancellation,
    query_available_seats,
    query_metro_fare,
    query_metro_schedules,
    query_national_rail_availability,
    query_national_rail_fare,
    query_national_rail_schedule_fares,
    query_payment_info,
    query_policy_vector_search,
    query_user_bookings,
    query_user_profile,
)
from skeleton.llm_provider import llm


_STATION_INDEX: dict[str, str] = {
    # Metro - English
    "central square": "MS01",
    "riverside": "MS02",
    "northgate": "MS03",
    "elm park": "MS04",
    "westfield": "MS05",
    "harbour view": "MS06",
    "old town": "MS07",
    "university": "MS08",
    "queensbridge": "MS09",
    "parkside": "MS10",
    "greenhill": "MS11",
    "lakeshore": "MS12",
    "clifton": "MS13",
    "eastwick": "MS14",
    "ferndale": "MS15",
    "hilltop": "MS16",
    "broadmoor": "MS17",
    "sunnyvale": "MS18",
    "redwood": "MS19",
    "thornton": "MS20",
    # Metro - Chinese
    "中央廣場": "MS01",
    "河濱站": "MS02",
    "北門站": "MS03",
    "榆樹公園站": "MS04",
    "西田站": "MS05",
    "海港景站": "MS06",
    "舊城站": "MS07",
    "大學站": "MS08",
    "皇后橋站": "MS09",
    "公園側站": "MS10",
    "綠丘站": "MS11",
    "湖岸站": "MS12",
    "克利夫頓站": "MS13",
    "東威克站": "MS14",
    "芬戴爾站": "MS15",
    "山頂站": "MS16",
    "寬地站": "MS17",
    "陽光谷站": "MS18",
    "紅木站": "MS19",
    "桑頓站": "MS20",
    # National Rail - English
    "central station": "NR01",
    "maplewood": "NR02",
    "old town junction": "NR03",
    "ashford": "NR04",
    "stonehaven": "NR05",
    "bridgeport": "NR06",
    "ferndale halt": "NR07",
    "coalport": "NR08",
    "dunmore": "NR09",
    "langford end": "NR10",
    # National Rail - Chinese
    "中央站": "NR01",
    "楓木站": "NR02",
    "舊城交匯站": "NR03",
    "阿什福德站": "NR04",
    "石港站": "NR05",
    "橋港站": "NR06",
    "芬戴爾停靠站": "NR07",
    "煤港站": "NR08",
    "丹摩站": "NR09",
    "蘭福德終點站": "NR10",
}


def _inject_station_ids(text: str) -> str:
    """
    Replace station names in text with 'name (ID)' so the LLM reads the ID
    right next to the name and uses it as the parameter value.
    """
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


def _unique_preserve_order(values: list[str]) -> list[str]:
    seen = set()
    unique = []
    for value in values:
        upper_value = value.upper()
        if upper_value in seen:
            continue
        seen.add(upper_value)
        unique.append(upper_value)
    return unique


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
- Use emojis to make responses easier to read.
- For schedules, use clear sections with labels.
- For routes, list every station in order and highlight interchange points.
- Keep responses concise but complete.

BOOKING CONFIRMATION RULE:
- Before calling make_booking, ALWAYS summarise the booking details and ask the user to confirm.
- Only call make_booking after the user explicitly says "confirm", "yes", "確認", "好", or "ok".

LOGIN RULE: Routes, fares, schedules, and policies work WITHOUT login.
Only make_booking and cancel_booking need login.

When DATA FROM TRANSITFLOW DATABASE is provided, use it as the only source of truth.
For route results: list every station name in order, note any line changes, and give the total travel time.
Always reply in the same language as the user.
""".format(today=date.today().isoformat())


TOOLS = [
    {
        "name": "check_national_rail_availability",
        "description": (
            "Check available national rail trains and services between two stations. "
            "Use for schedules, timetables, or availability."
        ),
        "parameters": {
            "origin_id": {"type": "string", "description": "National rail station ID e.g. NR01"},
            "destination_id": {"type": "string", "description": "National rail station ID e.g. NR05"},
            "travel_date": {"type": "string", "description": "YYYY-MM-DD, optional"},
        },
        "required": ["origin_id", "destination_id"],
    },
    {
        "name": "get_national_rail_fare",
        "description": "Calculate the fare for a national rail journey on a specific schedule.",
        "parameters": {
            "schedule_id": {"type": "string", "description": "e.g. NR_SCH01"},
            "fare_class": {"type": "string", "description": "standard or first"},
            "stops_travelled": {"type": "integer", "description": "Number of stops"},
        },
        "required": ["schedule_id", "fare_class", "stops_travelled"],
    },
    {
        "name": "get_national_rail_schedule_fares",
        "description": (
            "Get all ticket prices for a national rail schedule id when the user asks "
            "for the price of a service such as NR_SCH04."
        ),
        "parameters": {
            "schedule_id": {"type": "string", "description": "e.g. NR_SCH04"},
        },
        "required": ["schedule_id"],
    },
    {
        "name": "get_national_rail_journey_fares",
        "description": (
            "Get national rail fares between two national rail stations. "
            "Use when the user asks the price, cost, or fare from one NR station to another."
        ),
        "parameters": {
            "origin_id": {"type": "string", "description": "National rail station ID e.g. NR01"},
            "destination_id": {"type": "string", "description": "National rail station ID e.g. NR05"},
            "travel_date": {"type": "string", "description": "YYYY-MM-DD, optional"},
            "fare_class": {"type": "string", "description": "standard, first, or all"},
        },
        "required": ["origin_id", "destination_id"],
    },
    {
        "name": "check_metro_availability",
        "description": "Check available metro services between two metro stations.",
        "parameters": {
            "origin_id": {"type": "string", "description": "Metro station ID e.g. MS01"},
            "destination_id": {"type": "string", "description": "Metro station ID e.g. MS09"},
        },
        "required": ["origin_id", "destination_id"],
    },
    {
        "name": "calculate_metro_fare",
        "description": "Calculate the metro single-ticket fare for a journey.",
        "parameters": {
            "schedule_id": {"type": "string", "description": "e.g. MS_SCH01"},
            "stops_travelled": {"type": "integer", "description": "Number of stops"},
        },
        "required": ["schedule_id", "stops_travelled"],
    },
    {
        "name": "get_metro_fare",
        "description": (
            "Get the metro ticket price between two stations. "
            "Use for fare/price/cost questions, not route questions."
        ),
        "parameters": {
            "origin_id": {"type": "string", "description": "Metro station ID e.g. MS01"},
            "destination_id": {"type": "string", "description": "Metro station ID e.g. MS09"},
        },
        "required": ["origin_id", "destination_id"],
    },
    {
        "name": "get_user_bookings",
        "description": "Retrieve the logged-in user's full booking history.",
        "parameters": {},
        "required": [],
    },
    {
        "name": "get_user_profile",
        "description": "Retrieve the logged-in user's profile information.",
        "parameters": {},
        "required": [],
    },
    {
        "name": "get_payment_info",
        "description": "Retrieve payment details for a specific booking or metro trip.",
        "parameters": {
            "booking_id": {"type": "string", "description": "Booking or trip ID e.g. BK-A1B2C3"},
        },
        "required": ["booking_id"],
    },
    {
        "name": "get_available_seats",
        "description": "Show available seats on a national rail service.",
        "parameters": {
            "schedule_id": {"type": "string", "description": "e.g. NR_SCH01"},
            "travel_date": {"type": "string", "description": "YYYY-MM-DD"},
            "fare_class": {"type": "string", "description": "standard or first"},
        },
        "required": ["schedule_id", "travel_date", "fare_class"],
    },
    {
        "name": "booking_preflight",
        "description": (
            "Check national rail availability, fare, and seats before booking. "
            "Use for booking requests before the user explicitly confirms."
        ),
        "parameters": {
            "schedule_id": {"type": "string", "description": "e.g. NR_SCH01, optional"},
            "origin_station_id": {"type": "string", "description": "e.g. NR01"},
            "destination_station_id": {"type": "string", "description": "e.g. NR05"},
            "travel_date": {"type": "string", "description": "YYYY-MM-DD"},
            "fare_class": {"type": "string", "description": "standard or first"},
            "seat_id": {"type": "string", "description": "Seat ID or any, optional"},
            "ticket_type": {"type": "string", "description": "single or return"},
        },
        "required": ["origin_station_id", "destination_station_id", "travel_date", "fare_class"],
    },
    {
        "name": "make_booking",
        "description": "Create a national rail booking for the logged-in user after confirmation.",
        "parameters": {
            "schedule_id": {"type": "string", "description": "e.g. NR_SCH01"},
            "origin_station_id": {"type": "string", "description": "e.g. NR01"},
            "destination_station_id": {"type": "string", "description": "e.g. NR05"},
            "travel_date": {"type": "string", "description": "YYYY-MM-DD"},
            "fare_class": {"type": "string", "description": "standard or first"},
            "seat_id": {"type": "string", "description": "Seat ID or any"},
            "ticket_type": {"type": "string", "description": "single or return"},
        },
        "required": [
            "schedule_id",
            "origin_station_id",
            "destination_station_id",
            "travel_date",
            "fare_class",
            "seat_id",
        ],
    },
    {
        "name": "cancel_booking",
        "description": "Cancel a national rail booking for the logged-in user.",
        "parameters": {
            "booking_id": {"type": "string", "description": "Booking reference e.g. BK-A1B2C3"},
        },
        "required": ["booking_id"],
    },
    {
        "name": "search_policy",
        "description": "Search company policy documents.",
        "parameters": {
            "query": {"type": "string", "description": "Natural language policy question"},
        },
        "required": ["query"],
    },
    {
        "name": "find_route",
        "description": "Find the best route or path between two stations.",
        "parameters": {
            "origin_id": {"type": "string", "description": "Station ID e.g. MS01 or NR01"},
            "destination_id": {"type": "string", "description": "Station ID e.g. MS09 or NR05"},
            "network": {"type": "string", "description": "metro, rail, or auto"},
            "optimise_by": {"type": "string", "description": "time or cost"},
        },
        "required": ["origin_id", "destination_id"],
    },
    {
        "name": "find_alternative_routes",
        "description": "Find routes that avoid a specific delayed or closed station.",
        "parameters": {
            "origin_id": {"type": "string", "description": "e.g. NR01"},
            "destination_id": {"type": "string", "description": "e.g. NR05"},
            "avoid_station_id": {"type": "string", "description": "e.g. NR03"},
            "network": {"type": "string", "description": "metro, rail, or auto"},
        },
        "required": ["origin_id", "destination_id", "avoid_station_id"],
    },
    {
        "name": "get_delay_ripple",
        "description": "Show affected stations and lines from a disruption.",
        "parameters": {
            "station_id": {"type": "string", "description": "Station ID e.g. NR03 or MS07"},
            "hops": {"type": "integer", "description": "Number of hops"},
        },
        "required": ["station_id"],
    },
    {
        "name": "get_station_connections",
        "description": "List direct outbound graph connections from one station.",
        "parameters": {
            "station_id": {"type": "string", "description": "Station ID e.g. MS01 or NR01"},
        },
        "required": ["station_id"],
    },
]

TOOLS_SCHEMA = """\
find_route(origin_id, destination_id, optimise_by?)
check_national_rail_availability(origin_id, destination_id, travel_date?)
get_national_rail_fare(schedule_id, fare_class, stops_travelled)
get_national_rail_schedule_fares(schedule_id)
get_national_rail_journey_fares(origin_id, destination_id, travel_date?, fare_class?)
check_metro_availability(origin_id, destination_id)
calculate_metro_fare(schedule_id, stops_travelled)
get_metro_fare(origin_id, destination_id)
get_available_seats(schedule_id, travel_date, fare_class)
booking_preflight(schedule_id?, origin_station_id, destination_station_id, travel_date, fare_class, seat_id?, ticket_type?)
make_booking(schedule_id, origin_station_id, destination_station_id, travel_date, fare_class, seat_id, ticket_type?)
cancel_booking(booking_id)
get_user_bookings()
get_user_profile()
get_payment_info(booking_id)
search_policy(query)
find_alternative_routes(origin_id, destination_id, avoid_station_id, network?)
get_delay_ripple(station_id, hops?)
get_station_connections(station_id)"""


def _execute_tool(
    tool_name: str,
    params: dict,
    current_user_email: Optional[str] = None,
) -> str:
    """Execute a tool call and return the result as a JSON string."""
    try:
        if tool_name == "check_national_rail_availability":
            result = query_national_rail_availability(**params)

        elif tool_name == "get_national_rail_fare":
            result = query_national_rail_fare(**params)

        elif tool_name == "get_national_rail_schedule_fares":
            result = query_national_rail_schedule_fares(params["schedule_id"])
            if not result:
                return json.dumps({"error": f"找不到班次 {params['schedule_id']} 的票價資料。"})

        elif tool_name == "get_national_rail_journey_fares":
            origin_id = params["origin_id"]
            destination_id = params["destination_id"]
            travel_date = params.get("travel_date")
            requested_class = params.get("fare_class", "all")
            classes = ["standard", "first"] if requested_class in ("", "all", None) else [requested_class]
            schedules = query_national_rail_availability(
                origin_id=origin_id,
                destination_id=destination_id,
                travel_date=travel_date,
            )
            if not schedules:
                result = {
                    "error": "很抱歉，找不到這兩個國鐵站之間的服務。請確認站點代碼或方向是否正確。"
                }
            else:
                priced_services = []
                for schedule in schedules:
                    fares = []
                    for fare_class in classes:
                        fare = query_national_rail_fare(
                            schedule_id=schedule["schedule_id"],
                            fare_class=fare_class,
                            stops_travelled=schedule["stops_travelled"],
                        )
                        if fare:
                            fares.append(fare)
                    priced_services.append({
                        **schedule,
                        "fares": fares,
                    })
                result = priced_services

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
                route = query_shortest_route(
                    origin_id=params["origin_id"],
                    destination_id=params["destination_id"],
                    network="metro",
                )
                if not route.get("found"):
                    result = {
                        "error": "很抱歉，找不到這兩站之間的捷運服務。請確認站點代碼是否正確。"
                    }
                else:
                    result = {
                        "origin": route["path"][0]["name"],
                        "destination": route["path"][-1]["name"],
                        "fare_source": "graph_route_estimate",
                        "note": (
                            "No direct metro schedule was found in PostgreSQL; "
                            "this fare is estimated from the Neo4j route legs."
                        ),
                        "total_fare_usd": route["total_fare_usd"],
                        "total_time_min": route["total_time_min"],
                        "stations": route["stations"],
                        "legs": route["legs"],
                    }
            else:
                sched = schedules[0]
                stops_travelled = sched.get("stops_travelled", 1)
                fare = query_metro_fare(sched["schedule_id"], stops_travelled)
                result = {
                    "origin": sched.get("origin_name", params["origin_id"]),
                    "destination": sched.get("destination_name", params["destination_id"]),
                    "fare_source": "postgres_direct_schedule",
                    "line": sched.get("line"),
                    "schedule_id": sched["schedule_id"],
                    "stops_travelled": stops_travelled,
                    **(fare or {"error": "很抱歉，票價查詢失敗，請稍後再試。"}),
                }

        elif tool_name == "get_user_bookings":
            if not current_user_email:
                return json.dumps({"error": "您尚未登入。請點右上角的登入按鈕後再試。"})
            result = query_user_bookings(current_user_email)

        elif tool_name == "get_user_profile":
            if not current_user_email:
                return json.dumps({"error": "您尚未登入。請點右上角的登入按鈕後再試。"})
            result = query_user_profile(current_user_email)
            if result is None:
                return json.dumps({"error": "找不到使用者資料，請重新登入。"})

        elif tool_name == "get_payment_info":
            if not current_user_email:
                return json.dumps({"error": "您尚未登入。請點右上角的登入按鈕後再試。"})
            result = query_payment_info(params["booking_id"], current_user_email)
            if result is None:
                return json.dumps({"error": f"找不到訂單 {params['booking_id']} 的付款紀錄。"})

        elif tool_name == "get_available_seats":
            result = query_available_seats(**params)

        elif tool_name == "make_booking":
            if not current_user_email:
                return json.dumps({"error": "您需要先登入才能訂票。"})
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
                return json.dumps({"error": "您需要先登入才能取消訂票。"})
            profile = query_user_profile(current_user_email)
            if not profile:
                return json.dumps({"error": "找不到使用者資料，請重新登入。"})
            ok, data = execute_cancellation(
                booking_id=params["booking_id"],
                user_id=profile["user_id"],
            )
            result = data if ok else {"error": f"取消失敗：{data}"}

        elif tool_name == "search_policy":
            embedding = llm.embed(params["query"])
            docs = query_policy_vector_search(embedding)
            if not docs:
                return json.dumps({"error": "很抱歉，找不到相關政策資訊。請嘗試用不同的關鍵字搜尋。"})
            result = [
                {
                    "title": d["title"],
                    "category": d["category"],
                    "content": d["content"][:800],
                    "similarity": round(d["similarity"], 3),
                }
                for d in docs
            ]

        elif tool_name == "find_route":
            origin_id = params["origin_id"]
            destination_id = params["destination_id"]
            network = params.get("network", "auto")
            optimise_by = params.get("optimise_by", "time")

            is_cross = (
                origin_id.upper().startswith("MS")
                and destination_id.upper().startswith("NR")
            ) or (
                origin_id.upper().startswith("NR")
                and destination_id.upper().startswith("MS")
            )
            if optimise_by == "cost":
                result = query_cheapest_route(
                    origin_id=origin_id,
                    destination_id=destination_id,
                    network=network,
                )
            elif is_cross:
                result = query_interchange_path(origin_id, destination_id)
            else:
                result = query_shortest_route(
                    origin_id=origin_id,
                    destination_id=destination_id,
                    network=network,
                )

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

        elif tool_name == "get_station_connections":
            result = query_station_connections(params["station_id"])

        else:
            result = {"error": f"很抱歉，發生未知錯誤（{tool_name}）。請稍後再試。"}

        return json.dumps(result, default=str)

    except Exception as e:
        return json.dumps({"error": f"很抱歉，系統發生錯誤：{str(e)}。請稍後再試。"})


def _flatten_to_text(obj, depth: int = 0) -> str:
    """Recursively convert any JSON value to indented key-value text."""
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
    if isinstance(obj, list):
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
    """Extract tool calls from Gemini text, including slightly nonstandard JSON."""
    text = llm_response.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()

    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", text):
        try:
            data, _ = decoder.raw_decode(text, match.start())
        except (json.JSONDecodeError, ValueError):
            continue
        if "tool_calls" in data:
            return data["tool_calls"]
        if "tools" in data:
            return data["tools"]
        if "calls" in data:
            return data["calls"]
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    if isinstance(data, list):
        return data
    return None


def _normalise_tool_params(tool_name: str, params: dict) -> dict:
    """Clean Gemini router output before passing values to query functions."""
    cleaned = dict(params)

    for key in (
        "origin_id",
        "destination_id",
        "station_id",
        "avoid_station_id",
        "origin_station_id",
        "destination_station_id",
        "schedule_id",
        "booking_id",
        "seat_id",
    ):
        if isinstance(cleaned.get(key), str):
            cleaned[key] = cleaned[key].strip().upper()

    if isinstance(cleaned.get("fare_class"), str):
        fare_class = cleaned["fare_class"].strip().lower().replace("-", " ")
        if fare_class in {"first class", "1st", "first"}:
            cleaned["fare_class"] = "first"
        elif fare_class in {"standard class", "standard", "economy"}:
            cleaned["fare_class"] = "standard"
        elif fare_class in {"all", "both", "any"}:
            cleaned["fare_class"] = "all"

    if isinstance(cleaned.get("ticket_type"), str):
        ticket_type = cleaned["ticket_type"].strip().lower().replace("-", " ")
        if "return" in ticket_type:
            cleaned["ticket_type"] = "return"
        else:
            cleaned["ticket_type"] = "single"

    if isinstance(cleaned.get("optimise_by"), str):
        optimise_by = cleaned["optimise_by"].strip().lower()
        if optimise_by in {"cost", "fare", "price", "cheap", "cheapest", "lowest cost"}:
            cleaned["optimise_by"] = "cost"
        else:
            cleaned["optimise_by"] = "time"

    if isinstance(cleaned.get("network"), str):
        network = cleaned["network"].strip().lower().replace("-", "_")
        if network in {"national_rail", "national rail", "nr", "train"}:
            cleaned["network"] = "rail"
        elif network in {"city_metro", "city metro", "ms", "metro"}:
            cleaned["network"] = "metro"
        else:
            cleaned["network"] = "auto"

    if tool_name == "get_national_rail_journey_fares":
        cleaned.setdefault("fare_class", "all")

    return cleaned


def _user_confirmed(history: list[dict]) -> bool:
    """Check if the most recent user message contains an explicit confirmation."""
    if not history:
        return False
    last_user = next(
        (m["content"].lower() for m in reversed(history) if m["role"] == "user"),
        "",
    )
    confirm_words = {
        "confirm",
        "yes",
        "ok",
        "確認",
        "確定",
        "確定訂票",
        "好",
        "好的",
        "沒問題",
        "訂吧",
        "訂了",
        "幫我訂",
        "完成訂票",
    }
    return any(word in last_user for word in confirm_words)


def _llm_confirms_booking(user_message: str, history: list[dict]) -> bool:
    """
    Ask Gemini whether the latest message confirms the pending booking.

    The classifier is advisory only: make_booking still requires complete
    booking parameters before the write operation can run.
    """
    if not history or llm.get_chat_provider() != "gemini":
        return False

    context = _history_text(history, limit=6)
    prompt = f"""Recent conversation:
{context}

Latest user message:
{user_message}

Question: Does the latest user message explicitly confirm the pending booking?
Return only JSON: {{"confirmed": true}} or {{"confirmed": false}}."""
    try:
        response = llm.chat(
            messages=[{"role": "user", "content": prompt}],
            system_prompt=(
                "You are a strict booking confirmation classifier. "
                "Return only JSON. Treat new booking requests, questions, changes, "
                "or uncertainty as confirmed=false."
            ),
        )
    except Exception:
        return False

    try:
        data = json.loads(response.strip())
    except json.JSONDecodeError:
        return response.strip().lower().startswith("true")
    return bool(data.get("confirmed"))


def _booking_confirmed(user_message: str, history: list[dict]) -> tuple[bool, str]:
    """Combine keyword confirmation with Gemini semantic confirmation."""
    if _user_confirmed(history + [{"role": "user", "content": user_message}]):
        return True, "keyword"

    # Only ask Gemini if there is prior booking context to confirm. This keeps a
    # first-time "I want to book..." request from being treated as confirmation.
    pending_params = _extract_booking_params(_history_text(history))
    if not pending_params:
        return False, "none"

    if _llm_confirms_booking(user_message, history):
        return True, "gemini"
    return False, "none"


def _tool_result(tool_name: str, params: dict, result_json: str) -> dict:
    """Keep raw JSON and final summary together for debug output and final prompting."""
    return {
        "tool": tool_name,
        "params": params,
        "result": result_json,
        "summary": _summarise_result(tool_name, result_json),
    }


def _extract_booking_params(text: str) -> dict:
    """Recover booking fields from a natural-language request or recent chat history."""
    station_ids = _unique_preserve_order(re.findall(r"(NR\d{2})", text, re.IGNORECASE))
    schedule_ids = _unique_preserve_order(re.findall(r"(NR_SCH\d{2})", text, re.IGNORECASE))
    date_match = re.search(r"\b\d{4}-\d{2}-\d{2}\b", text)
    seat_match = re.search(r"\b[A-Z]\d{2}\b", text, re.IGNORECASE)

    params: dict = {}
    if schedule_ids:
        params["schedule_id"] = schedule_ids[-1]
    if len(station_ids) >= 2:
        params["origin_station_id"] = station_ids[0]
        params["destination_station_id"] = station_ids[1]
    if date_match:
        params["travel_date"] = date_match.group(0)

    lowered = text.lower()
    if "first" in lowered or "頭等" in lowered:
        params["fare_class"] = "first"
    elif "standard" in lowered or "標準" in lowered:
        params["fare_class"] = "standard"

    if seat_match:
        params["seat_id"] = seat_match.group(0).upper()
    elif "any" in lowered or "任意" in lowered:
        params["seat_id"] = "any"

    params["ticket_type"] = "return" if "return" in lowered or "來回" in lowered else "single"
    return params


def _history_text(history: list[dict], limit: int = 8) -> str:
    return "\n".join(str(item.get("content", "")) for item in history[-limit:])


def _redirect_unconfirmed_booking_calls(tool_calls: list[dict], confirmed: bool) -> list[dict]:
    """Never let an unconfirmed booking request reach the write-operation tool."""
    if confirmed:
        return tool_calls
    redirected = []
    for call in tool_calls:
        if call.get("name") == "make_booking":
            redirected.append({
                "name": "booking_preflight",
                "params": call.get("params") or call.get("parameters", {}),
            })
        else:
            redirected.append(call)
    return redirected


def _booking_preflight_results(
    booking_params: dict,
    current_user_email: Optional[str],
) -> list[dict]:
    """
    Run the read-only checks needed before booking.

    This intentionally uses availability, fare, and seat lookups instead of
    make_booking until the user explicitly confirms the exact service and seat.
    """
    booking_params = _normalise_tool_params("make_booking", booking_params)
    origin_id = booking_params.get("origin_station_id")
    destination_id = booking_params.get("destination_station_id")
    travel_date = booking_params.get("travel_date")
    fare_class = booking_params.get("fare_class", "standard")

    missing = [
        label
        for label, value in (
            ("origin_station_id", origin_id),
            ("destination_station_id", destination_id),
            ("travel_date", travel_date),
            ("fare_class", fare_class),
        )
        if not value
    ]
    if missing:
        result_json = json.dumps({
            "error": (
                "訂票前需要先補齊資料："
                + ", ".join(missing)
                + "。請提供出發站、目的地、日期與艙等。"
            )
        })
        return [_tool_result("booking_preflight", booking_params, result_json)]

    results = []
    availability_params = {
        "origin_id": origin_id,
        "destination_id": destination_id,
        "travel_date": travel_date,
    }
    availability_json = _execute_tool(
        "check_national_rail_availability",
        availability_params,
        current_user_email,
    )
    results.append(
        _tool_result(
            "check_national_rail_availability",
            availability_params,
            availability_json,
        )
    )

    try:
        schedules = json.loads(availability_json)
    except json.JSONDecodeError:
        schedules = []
    if not isinstance(schedules, list):
        schedules = []

    bookable_schedules = [
        schedule for schedule in schedules
        if schedule.get("availability_status") == "available"
        and int(schedule.get("available_seats") or 0) > 0
    ]

    schedule_hint = booking_params.get("schedule_id")
    if schedule_hint:
        matching = [
            schedule for schedule in bookable_schedules
            if schedule.get("schedule_id") == schedule_hint
        ]
        if matching:
            bookable_schedules = matching

    for schedule in bookable_schedules[:3]:
        fare_params = {
            "schedule_id": schedule["schedule_id"],
            "fare_class": fare_class,
            "stops_travelled": schedule["stops_travelled"],
        }
        fare_json = _execute_tool(
            "get_national_rail_fare",
            fare_params,
            current_user_email,
        )
        results.append(_tool_result("get_national_rail_fare", fare_params, fare_json))

        seats_params = {
            "schedule_id": schedule["schedule_id"],
            "travel_date": travel_date,
            "fare_class": fare_class,
        }
        seats_json = _execute_tool(
            "get_available_seats",
            seats_params,
            current_user_email,
        )
        results.append(_tool_result("get_available_seats", seats_params, seats_json))

    return results


def run_agent(
    user_message: str,
    history: list[dict],
    debug: bool = False,
    current_user_email: Optional[str] = None,
) -> tuple:
    """Main agent loop."""
    debug_info = []

    if current_user_email:
        profile = query_user_profile(current_user_email)
        if profile:
            user_display = f"{profile['full_name']} (email: {current_user_email}, user_id: {profile['user_id']})"
        else:
            user_display = current_user_email
        contextual_prompt = SYSTEM_PROMPT + (
            f"\n\n目前登入使用者：{user_display}。"
            "請直接回答此使用者的個人訂票問題，不需要再詢問 email 或 ID。"
            "查詢訂票紀錄請使用 get_user_bookings()。"
            "訂票和取消請使用 make_booking / cancel_booking。"
        )
    else:
        contextual_prompt = SYSTEM_PROMPT + (
            "\n\n目前沒有使用者登入。"
            "如果使用者詢問個人訂票、歷史紀錄，或想要訂票、取消訂票，"
            "請友善地告知他們需要先登入。"
        )

    recent_history = history[-4:] if len(history) > 4 else history
    augmented_message = _inject_station_ids(user_message)

    # Gemini is stronger than Ollama, but we still ask for a tiny JSON routing
    # decision so database access remains explicit and inspectable in debug mode.
    tool_selection_prompt = f"""Output only this JSON (no other text):
{{"tool_calls": [{{"name": "TOOL", "params": {{"KEY": "VALUE"}}}}]}}
Or if no tool needed: {{"tool_calls": []}}

STATIONS: Metro=MS01-MS20, Rail=NR01-NR10
USER: {current_user_email or "not logged in"}
Use relational tools for schedules, availability, fares, seats, bookings, payments, and policies.
Use graph tools only for route/path/interchange/disruption questions.
get_user_bookings: call when logged-in user asks about their bookings, tickets, or travel history.
get_user_profile: call when logged-in user asks about their account or profile.
get_payment_info: call with booking_id when user asks about payment for a specific booking.
Booking requests before confirmation: do not call make_booking; call booking_preflight.
make_booking: only if user is logged in AND the latest user message explicitly confirms the booking.
cancel_booking: only if user is logged in and the user asks to cancel.
Route/path/journey/怎麼去/如何前往/路線 questions: use find_route.
Metro fare/price/cost/票價/多少錢 between two MS stations: use get_metro_fare.
National rail fare/price/cost/票價/多少錢 between two NR stations: use get_national_rail_journey_fares.
National rail fare/price/cost for a schedule id like NR_SCH04: use get_national_rail_schedule_fares.
Schedule/timetable/班次/時刻表 between two stations: use check_metro_availability or check_national_rail_availability.
Direct neighbours/adjacent stations/相鄰/直接連到: use get_station_connections.
Avoid/避開 route questions: use find_alternative_routes.
Delay/延誤/affected stations/hops: use get_delay_ripple.
Policy/rules/退款/補償/行李/寵物 questions: use search_policy.
Never use "" as a param value. Omit optional params if unknown.

TOOLS:
{TOOLS_SCHEMA}

HISTORY:
{json.dumps(recent_history, indent=None)}

USER: "{augmented_message}"

JSON:"""

    if llm.get_chat_provider() == "ollama":
        try:
            tool_calls = llm.ollama_tool_call(
                recent_history,
                TOOLS,
                augmented_message,
                system_prompt=(
                    "You are a tool router. Call the right tool based on the user message. "
                    f"Logged-in user: {current_user_email or 'none'}. "
                    "My bookings/tickets/travel history/我的訂票 -> get_user_bookings. "
                    "My account/profile/我的帳號 -> get_user_profile. "
                    "Payment info for booking -> get_payment_info. "
                    "Fare/price/cost for a rail schedule id like NR_SCH04 -> get_national_rail_schedule_fares. "
                    "Book a ticket before confirmation -> booking_preflight, not make_booking. "
                    "Only explicit confirmation -> make_booking. "
                    "Cancel a booking -> cancel_booking. "
                    "Policy/rules/refund/luggage/bicycle/退款/補償/行李/寵物 -> search_policy. "
                    "Route/directions/fastest/how-to-get/怎麼去/路線 -> find_route. "
                    "Adjacent/direct station connections/相鄰/直接連到 -> get_station_connections. "
                    "Delay/延誤/hops -> get_delay_ripple. "
                    "Metro fare/price/cost/票價/多少錢 -> get_metro_fare. "
                    "Schedule/timetable/trains/services/班次/時刻表 -> availability tools. "
                    "Only call a tool when needed."
                ),
            )
        except ConnectionError as exc:
            tool_calls = []
            if debug:
                debug_info.append(f"**Tool selection unavailable:** {exc}")
        raw_tool_selection = tool_calls
    else:
        selection_response = ""
        try:
            selection_response = llm.chat(
                messages=[{"role": "user", "content": tool_selection_prompt}],
                system_prompt="JSON only. You are a router. Output valid JSON. No empty string param values.",
            )
            tool_calls = _parse_tool_calls(selection_response) or []
        except Exception as exc:
            tool_calls = []
            if debug:
                debug_info.append(f"**Tool selection unavailable:** {exc}")
        raw_tool_selection = selection_response

    lower = augmented_message.lower()
    station_ids = _unique_preserve_order(
        re.findall(r"(MS\d{2}|NR\d{2})", augmented_message, re.IGNORECASE)
    )
    schedule_ids = _unique_preserve_order(
        re.findall(r"(NR_SCH\d{2}|MS_SCH\d{2})", augmented_message, re.IGNORECASE)
    )
    booking_ids = re.findall(r"((?:BK|MT)[A-Z0-9-]*\d+[A-Z0-9-]*)", augmented_message, re.IGNORECASE)
    two_stations = len(station_ids) >= 2
    stops_match = re.search(r"\b(\d+)\s*(?:stops?|站)\b", lower)
    stops_travelled = int(stops_match.group(1)) if stops_match else None
    fare_class = None
    if "first" in lower or "頭等" in lower:
        fare_class = "first"
    elif "standard" in lower or "標準" in lower:
        fare_class = "standard"

    # Safety rule: booking requests are read-only until the latest user message
    # confirms. If Gemini jumps to make_booking too early, rewrite it to
    # booking_preflight before any tool can execute.
    confirmed_now, confirmation_source = _booking_confirmed(user_message, history)
    if debug and confirmed_now:
        debug_info.append(f"**Booking confirmation:** {confirmation_source}")
    original_tool_calls = tool_calls
    tool_calls = _redirect_unconfirmed_booking_calls(tool_calls, confirmed_now)
    if debug:
        if tool_calls != original_tool_calls:
            debug_info.append(f"**Raw tool selection:** {raw_tool_selection}")
            debug_info.append(f"**Tool selection:** {tool_calls}")
        elif raw_tool_selection:
            debug_info.append(f"**Tool selection:** {raw_tool_selection}")

    def _tool_selected(name: str, *required_params) -> bool:
        call = next((c for c in tool_calls if c.get("name") == name), None)
        if not call:
            return False
        params = call.get("params") or call.get("parameters") or {}
        return all(params.get(k) for k in required_params)

    def _fallback(name: str, params: dict, reason: str) -> None:
        nonlocal tool_calls
        tool_calls = [{"name": name, "params": params}]
        if debug:
            debug_info.append(f"**Fallback:** {reason} -> {name}({params})")

    # Rule-based fallbacks cover the fixed demo questions and short Chinese
    # prompts where even Gemini may choose an overly broad or write-oriented tool.
    route_triggers = {
        "fastest route",
        "quickest route",
        "shortest route",
        "cheapest route",
        "best route",
        "how to get",
        "directions from",
        "route from",
        "route to",
        "get from",
        "travel from",
        "way from",
        "path from",
        "最快路線",
        "最短路線",
        "最便宜路線",
        "最便宜",
        "最快",
        "怎麼去",
        "如何前往",
        "路線規劃",
        "路線查詢",
        "怎麼走",
        "如何去",
        "如何搭",
        "怎麼搭",
        "搭到",
        "轉乘",
        "換乘",
        "怎麼轉",
        "如何轉乘",
    }
    fare_triggers = {
        "fare",
        "price",
        "cost",
        "ticket price",
        "多少錢",
        "票價",
        "價格",
        "費用",
    }
    date_match = re.search(r"\b\d{4}-\d{2}-\d{2}\b", lower)
    travel_date = date_match.group(0) if date_match else None

    # Booking/payment/cancellation references are deterministic enough to route
    # with regex, so prefer direct routing over another LLM guess.
    if booking_ids and not tool_calls:
        booking_id = booking_ids[0].upper()
        if any(kw in lower for kw in ["cancel", "取消"]):
            _fallback("cancel_booking", {"booking_id": booking_id}, "cancel booking query")
        elif any(kw in lower for kw in ["payment", "付款", "付款方式", "金額"]):
            _fallback("get_payment_info", {"booking_id": booking_id}, "payment info query")

    if not tool_calls and any(kw in lower for kw in ["cancel", "取消"]):
        previous_ids = re.findall(
            r"((?:BK|MT)[A-Z0-9-]*\d+[A-Z0-9-]*)",
            _history_text(history),
            re.IGNORECASE,
        )
        if previous_ids:
            _fallback(
                "cancel_booking",
                {"booking_id": previous_ids[-1].upper()},
                "cancel previous booking query",
            )

    if schedule_ids and not tool_calls:
        schedule_id = schedule_ids[0].upper()
        if any(kw in lower for kw in ["seat", "seats", "座位"]):
            if travel_date and fare_class:
                _fallback(
                    "get_available_seats",
                    {
                        "schedule_id": schedule_id,
                        "travel_date": travel_date,
                        "fare_class": fare_class,
                    },
                    "available seats query",
                )
        elif schedule_id.startswith("NR_SCH") and stops_travelled and fare_class:
            _fallback(
                "get_national_rail_fare",
                {
                    "schedule_id": schedule_id,
                    "fare_class": fare_class,
                    "stops_travelled": stops_travelled,
                },
                "national rail fare with stops query",
            )
        elif schedule_id.startswith("MS_SCH") and stops_travelled:
            _fallback(
                "calculate_metro_fare",
                {
                    "schedule_id": schedule_id,
                    "stops_travelled": stops_travelled,
                },
                "metro fare with stops query",
            )

    if (
        schedule_ids
        and any(kw in lower for kw in fare_triggers)
        and not _tool_selected("get_national_rail_schedule_fares", "schedule_id")
        and not tool_calls
    ):
        schedule_id = schedule_ids[0].upper()
        if schedule_id.startswith("NR_SCH"):
            _fallback(
                "get_national_rail_schedule_fares",
                {"schedule_id": schedule_id},
                "national rail schedule fare query",
            )

    if not tool_calls and len(station_ids) >= 3 and any(
        kw in lower for kw in ["avoid", "避開", "alternative"]
    ):
        _fallback(
            "find_alternative_routes",
            {
                "origin_id": station_ids[0].upper(),
                "destination_id": station_ids[1].upper(),
                "avoid_station_id": station_ids[2].upper(),
                "network": "auto",
            },
            "alternative route query",
        )

    if not tool_calls and station_ids and any(
        kw in lower for kw in ["delay", "delayed", "延誤", "影響", "hops", "hop"]
    ):
        hops_match = re.search(r"\b(\d+)\s*hops?\b", lower)
        if not hops_match:
            hops_match = re.search(r"(\d+)\s*hops?", lower)
        hops = int(hops_match.group(1)) if hops_match else 2
        _fallback(
            "get_delay_ripple",
            {"station_id": station_ids[0].upper(), "hops": hops},
            "delay ripple query",
        )

    if not tool_calls and station_ids and any(
        kw in lower for kw in ["相鄰", "直接連", "直接相鄰", "direct", "adjacent", "connections"]
    ):
        _fallback(
            "get_station_connections",
            {"station_id": station_ids[0].upper()},
            "station connections query",
        )

    if not tool_calls and any(kw in lower for kw in ["我想訂", "我要訂", "訂票", "book", "booking"]):
        booking_params = _extract_booking_params(augmented_message)
        if booking_params:
            tool_calls = [{"name": "booking_preflight", "params": booking_params}]
            if debug:
                debug_info.append(f"**Fallback:** booking request -> booking_preflight({booking_params})")

    if (
        not tool_calls
        and
        two_stations
        and any(kw in lower for kw in fare_triggers)
        and not any(
            _tool_selected(name, "origin_id", "destination_id")
            for name in ("get_metro_fare", "get_national_rail_journey_fares", "find_route")
        )
    ):
        origin_id = station_ids[0].upper()
        destination_id = station_ids[1].upper()
        same_metro = origin_id.startswith("MS") and destination_id.startswith("MS")
        same_rail = origin_id.startswith("NR") and destination_id.startswith("NR")
        if same_metro:
            _fallback(
                "get_metro_fare",
                {"origin_id": origin_id, "destination_id": destination_id},
                "metro station fare query",
            )
        elif same_rail:
            params = {"origin_id": origin_id, "destination_id": destination_id}
            if travel_date:
                params["travel_date"] = travel_date
            if "first" in lower or "頭等" in lower or "first class" in lower:
                params["fare_class"] = "first"
            elif "standard" in lower or "標準" in lower:
                params["fare_class"] = "standard"
            _fallback(
                "get_national_rail_journey_fares",
                params,
                "national rail station fare query",
            )
        else:
            _fallback(
                "find_route",
                {
                    "origin_id": origin_id,
                    "destination_id": destination_id,
                    "optimise_by": "cost",
                },
                "cross-network fare query",
            )

    is_route = (
        any(kw in lower for kw in route_triggers)
        or (two_stations and "route" in lower)
        or (two_stations and "路線" in lower)
    )
    if (
        not tool_calls
        and is_route
        and two_stations
        and not _tool_selected("find_route", "origin_id", "destination_id")
    ):
        optimise_by = "cost" if any(
            kw in lower for kw in ["cheap", "cheapest", "lowest cost", "最便宜", "最低票價"]
        ) else "time"
        _fallback(
            "find_route",
            {
                "origin_id": station_ids[0].upper(),
                "destination_id": station_ids[1].upper(),
                "optimise_by": optimise_by,
            },
            "route query",
        )

    elif not tool_calls and two_stations:
        availability_triggers = {
            "train",
            "trains",
            "service",
            "services",
            "run from",
            "runs from",
            "schedule",
            "timetable",
            "available",
            "availability",
            "班次",
            "時刻表",
            "列車",
            "服務",
            "有沒有車",
            "幾點有車",
            "查車",
        }
        if any(kw in lower for kw in availability_triggers):
            origin_id = station_ids[0].upper()
            destination_id = station_ids[1].upper()
            params = {"origin_id": origin_id, "destination_id": destination_id}
            if travel_date:
                params["travel_date"] = travel_date
            tool = "check_national_rail_availability" if origin_id.startswith("NR") else "check_metro_availability"
            _fallback(tool, params, "availability query")

    if current_user_email and not tool_calls:
        personal_triggers = {
            "my booking",
            "my ticket",
            "my trip",
            "my journey",
            "my history",
            "my reservation",
            "show booking",
            "view booking",
            "check booking",
            "list booking",
            "show my",
            "view my",
            "我的訂票",
            "我的票",
            "我的行程",
            "訂票紀錄",
            "查詢訂票",
            "我訂的",
            "我的車票",
        }
        if any(kw in lower for kw in personal_triggers):
            _fallback("get_user_bookings", {}, "personal booking query")

    if current_user_email and not tool_calls:
        profile_triggers = {
            "my account",
            "my profile",
            "my info",
            "account details",
            "我的帳號",
            "我的資料",
            "帳號資訊",
            "個人資料",
        }
        if any(kw in lower for kw in profile_triggers):
            _fallback("get_user_profile", {}, "profile query")

    if not tool_calls:
        policy_triggers = {
            "refund",
            "policy",
            "compensation",
            "luggage",
            "bicycle",
            "pet",
            "退款",
            "補償",
            "政策",
            "行李",
            "寵物",
            "腳踏車",
            "規定",
        }
        if any(kw in lower for kw in policy_triggers):
            _fallback("search_policy", {"query": user_message}, "policy query")

    if not tool_calls and confirmed_now:
        # On "confirm" turns, recover the pending booking details from recent
        # conversation text so make_booking can run with concrete parameters.
        recovered = _extract_booking_params(_history_text(history) + "\n" + augmented_message)
        if recovered:
            tool_calls = [{"name": "make_booking", "params": recovered}]
            if debug:
                debug_info.append(f"**Fallback:** booking confirmation -> make_booking({recovered})")

    tool_results = []
    if any(c.get("name") in {"make_booking", "booking_preflight"} for c in tool_calls):
        if not confirmed_now:
            # Unconfirmed booking intent becomes a read-only preflight sequence:
            # availability -> fare -> seats.
            preflight_results = []
            for call in tool_calls:
                if call.get("name") not in {"make_booking", "booking_preflight"}:
                    continue
                preflight_results.extend(_booking_preflight_results(
                    call.get("params") or call.get("parameters", {}),
                    current_user_email,
                ))

            tool_calls = []
            tool_results.extend(preflight_results)
            if debug:
                debug_info.append(
                    "**Effective tools:** check_national_rail_availability -> "
                    "get_national_rail_fare -> get_available_seats"
                )
                debug_info.append(
                    "**Booking preflight:** ran availability, fare, and seat checks before confirmation."
                )
        else:
            gated_calls = []
            for call in tool_calls:
                if call.get("name") != "make_booking":
                    gated_calls.append(call)
                    continue
                booking_params = _normalise_tool_params(
                    "make_booking",
                    call.get("params") or call.get("parameters", {}),
                )
                missing = [
                    label
                    for label, value in (
                        ("schedule_id", booking_params.get("schedule_id")),
                        ("origin_station_id", booking_params.get("origin_station_id")),
                        ("destination_station_id", booking_params.get("destination_station_id")),
                        ("travel_date", booking_params.get("travel_date")),
                        ("fare_class", booking_params.get("fare_class")),
                        ("seat_id", booking_params.get("seat_id")),
                    )
                    if not value
                ]
                if missing:
                    result_json = json.dumps({
                        "error": (
                            "完成訂票前需要先補齊資料："
                            + ", ".join(missing)
                            + "。請提供完整訂票資訊。"
                        )
                    })
                    tool_results.append(_tool_result("make_booking", booking_params, result_json))
                    continue
                gated_calls.append({"name": "make_booking", "params": booking_params})
            tool_calls = gated_calls

    for call in tool_calls:
        tool_name = call.get("name", "")
        params = _normalise_tool_params(
            tool_name,
            call.get("params") or call.get("parameters", {}),
        )

        if any(v == "" for v in params.values()):
            if debug:
                debug_info.append(f"**Skipped** `{tool_name}` - empty params: {params}")
            continue

        if debug:
            debug_info.append(f"**Calling:** `{tool_name}({params})`")

        result_json = _execute_tool(tool_name, params, current_user_email)
        summary = _summarise_result(tool_name, result_json)

        if debug:
            debug_info.append(
                f"**Result (raw):** ```json\n{result_json[:300]}\n```\n"
                f"**Summary sent to LLM:** {summary}"
            )

        tool_results.append(_tool_result(tool_name, params, result_json))

    db_keywords = {
        "booking",
        "ticket",
        "schedule",
        "fare",
        "route",
        "seat",
        "train",
        "metro",
        "journey",
        "trip",
        "history",
        "reservation",
        "訂票",
        "班次",
        "票價",
        "路線",
        "座位",
        "捷運",
        "列車",
    }
    data_block = ""
    if tool_results:
        # The final LLM call only receives database results, not free-form
        # instructions to invent data. This keeps schedules, fares, seats, and
        # booking status grounded in PostgreSQL/Neo4j.
        data_block = "\n\n".join(
            f"[{tr['tool']}]\n{_normalise_result(tr['tool'], tr['result'])}"
            for tr in tool_results
        )
        if debug:
            debug_info.append(f"**Data (normalised):**\n{data_block}")
        content = (
            f"DATA FROM TRANSITFLOW DATABASE:\n{data_block}"
            f"\n\nUser asks: {user_message}"
            "\n\nAnswer using only the data above. Use emojis and clear formatting."
            "\nIf the user is trying to book and the data includes availability, fares, or seats, "
            "do NOT say the booking is complete. Summarise the available service options, "
            "show the fare and a few available seat IDs, then ask the user to choose/confirm "
            "the exact schedule and seat before booking:"
        )
    elif any(kw in user_message.lower() for kw in db_keywords):
        content = (
            f"User asks: {user_message}\n\n"
            "IMPORTANT: No data was retrieved from the TransitFlow database for this query. "
            "Apologise politely in the user's language and suggest what they can try instead. "
            "Do NOT invent any bookings, fares, schedules, seat numbers, or travel times."
        )
    else:
        content = user_message

    final_messages = history + [{"role": "user", "content": content}]
    try:
        answer = llm.chat(messages=final_messages, system_prompt=contextual_prompt)
    except Exception as exc:
        if tool_results:
            answer = f"目前 LLM 回應失敗；先直接提供資料庫查詢結果：\n\n{data_block}"
        else:
            answer = (
                "目前 LLM 回應失敗，所以我無法產生 AI 回覆。"
                "請確認目前選用的模型服務可用後再試一次。"
            )
        if debug:
            debug_info.append(f"**LLM unavailable:** {exc}")

    updated_history = history + [
        {"role": "user", "content": user_message},
        {"role": "assistant", "content": answer},
    ]

    if debug:
        return answer, updated_history, "\n\n".join(debug_info)
    return answer, updated_history
