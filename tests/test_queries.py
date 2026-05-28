import sys
import types
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch


def _install_optional_dependency_stubs():
    if "dotenv" not in sys.modules:
        dotenv = types.ModuleType("dotenv")
        dotenv.load_dotenv = lambda *args, **kwargs: None
        sys.modules["dotenv"] = dotenv

    if "psycopg2" not in sys.modules:
        psycopg2 = types.ModuleType("psycopg2")
        extras = types.ModuleType("psycopg2.extras")

        class IntegrityError(Exception):
            diag = types.SimpleNamespace(message_primary="integrity error")

        psycopg2.connect = lambda *args, **kwargs: None
        psycopg2.IntegrityError = IntegrityError
        psycopg2.extras = extras
        extras.RealDictCursor = object
        sys.modules["psycopg2"] = psycopg2
        sys.modules["psycopg2.extras"] = extras

    if "argon2" not in sys.modules:
        argon2 = types.ModuleType("argon2")
        exceptions = types.ModuleType("argon2.exceptions")

        class PasswordHasher:
            def hash(self, value):
                return value

            def verify(self, hashed, value):
                return hashed == value

        class VerifyMismatchError(Exception):
            pass

        class VerificationError(Exception):
            pass

        argon2.PasswordHasher = PasswordHasher
        exceptions.VerifyMismatchError = VerifyMismatchError
        exceptions.VerificationError = VerificationError
        sys.modules["argon2"] = argon2
        sys.modules["argon2.exceptions"] = exceptions

    if "neo4j" not in sys.modules:
        neo4j = types.ModuleType("neo4j")
        neo4j.GraphDatabase = types.SimpleNamespace(driver=lambda *args, **kwargs: None)
        sys.modules["neo4j"] = neo4j


_install_optional_dependency_stubs()

from databases.graph import queries as graph_queries
from databases.relational import queries as relational_queries


class FakeCursor:
    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.executions = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params=None):
        self.executions.append((sql, params))

    def fetchone(self):
        return self.rows.pop(0) if self.rows else None

    def fetchall(self):
        rows = self.rows
        self.rows = []
        return rows


class FakeConnection:
    def __init__(self, cursor):
        self._cursor = cursor
        self.autocommit = None
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def cursor(self, *args, **kwargs):
        return self._cursor

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


class RelationalQueryTests(unittest.TestCase):
    def test_national_rail_fare_rejects_invalid_stop_count_without_db(self):
        with patch.object(relational_queries, "_connect") as connect:
            self.assertIsNone(
                relational_queries.query_national_rail_fare(
                    schedule_id="NR_SCH01",
                    fare_class="standard",
                    stops_travelled="any",
                )
            )
        connect.assert_not_called()

    def test_national_rail_fare_accepts_int_like_stop_count(self):
        cursor = FakeCursor(
            [
                {
                    "fare_class": "standard",
                    "base_fare_usd": Decimal("2.50"),
                    "per_stop_rate_usd": Decimal("1.50"),
                    "total_fare_usd": Decimal("8.50"),
                }
            ]
        )
        conn = FakeConnection(cursor)

        with patch.object(relational_queries, "_connect", return_value=conn):
            fare = relational_queries.query_national_rail_fare(
                schedule_id="NR_SCH01",
                fare_class="standard",
                stops_travelled="4",
            )

        self.assertEqual(fare["total_fare_usd"], Decimal("8.50"))
        self.assertEqual(cursor.executions[0][1], (4, "NR_SCH01", "standard"))

    def test_execute_booking_snapshots_fare_to_booking_and_payment(self):
        cursor = FakeCursor(
            [
                {"id": 101},
                {
                    "schedule_pk": 201,
                    "departure_time": "06:00",
                    "origin_sequence": 1,
                    "destination_sequence": 5,
                    "stops_travelled": 4,
                    "origin_pk": 301,
                    "destination_pk": 305,
                },
                {
                    "fare_class": "standard",
                    "base_fare_usd": Decimal("2.50"),
                    "per_stop_rate_usd": Decimal("1.50"),
                    "total_fare_usd": Decimal("8.50"),
                },
                {
                    "seat_pk": 401,
                    "seat_id": "B01",
                    "coach": "B",
                    "row_number": 1,
                    "seat_column": "A",
                },
                {
                    "id": 501,
                    "booking_id": "BK-TEST01",
                    "booked_at": "2026-05-28T10:00:00Z",
                },
                {"payment_id": "PM-TEST01", "paid_at": "2026-05-28T10:00:01Z"},
            ]
        )
        conn = FakeConnection(cursor)

        with patch.object(relational_queries.psycopg2, "connect", return_value=conn):
            with patch.object(relational_queries, "_gen_booking_id", return_value="BK-TEST01"):
                with patch.object(relational_queries, "_gen_payment_id", return_value="PM-TEST01"):
                    ok, booking = relational_queries.execute_booking(
                        user_id="RU01",
                        schedule_id="NR_SCH01",
                        origin_station_id="NR01",
                        destination_station_id="NR05",
                        travel_date="2026-06-01",
                        fare_class="standard",
                        seat_id=" Any ",
                    )

        self.assertTrue(ok)
        self.assertTrue(conn.committed)
        self.assertEqual(booking["fare_usd"], Decimal("8.50"))
        self.assertEqual(booking["amount_usd"], Decimal("8.50"))

        booking_insert_params = cursor.executions[4][1]
        payment_insert_params = cursor.executions[5][1]
        self.assertEqual(booking_insert_params[-2:], (Decimal("8.50"), Decimal("8.50")))
        self.assertEqual(payment_insert_params, ("PM-TEST01", 501, Decimal("8.50")))

    def test_schema_allows_reusing_email_after_soft_delete(self):
        schema = Path("databases/relational/schema.sql").read_text(encoding="utf-8")

        self.assertIn("email          VARCHAR(150) NOT NULL,", schema)
        self.assertIn("DROP CONSTRAINT IF EXISTS registered_users_email_key", schema)
        self.assertIn("CREATE UNIQUE INDEX IF NOT EXISTS uniq_active_registered_users_email", schema)
        self.assertIn("WHERE is_active = TRUE", schema)


def _demo_graph():
    stations = {
        "MS01": {
            "station_id": "MS01",
            "name": "Central Square",
            "network": "metro",
            "lines": ["M1"],
        },
        "MS02": {
            "station_id": "MS02",
            "name": "Museum",
            "network": "metro",
            "lines": ["M1"],
        },
        "MS03": {
            "station_id": "MS03",
            "name": "Harbour",
            "network": "metro",
            "lines": ["M1"],
        },
        "NR01": {
            "station_id": "NR01",
            "name": "Central Rail",
            "network": "rail",
            "lines": ["NR1"],
        },
    }
    edges = {
        "MS01": [
            {
                "to": "MS02",
                "relationship": "METRO_LINK",
                "line": "M1",
                "travel_time_min": 10,
                "fare_standard_usd": 0.30,
                "fare_first_usd": 0.30,
            },
            {
                "to": "MS03",
                "relationship": "METRO_LINK",
                "line": "M1",
                "travel_time_min": 2,
                "fare_standard_usd": 0.30,
                "fare_first_usd": 0.30,
            },
        ],
        "MS03": [
            {
                "to": "MS02",
                "relationship": "METRO_LINK",
                "line": "M1",
                "travel_time_min": 2,
                "fare_standard_usd": 0.30,
                "fare_first_usd": 0.30,
            },
            {
                "to": "NR01",
                "relationship": "INTERCHANGE_TO",
                "line": "interchange",
                "travel_time_min": 5,
                "fare_standard_usd": 0,
                "fare_first_usd": 0,
            },
        ],
        "MS02": [],
        "NR01": [],
    }
    return stations, edges


class GraphQueryTests(unittest.TestCase):
    def test_shortest_route_uses_fastest_path(self):
        with patch.object(graph_queries, "_load_graph", return_value=_demo_graph()):
            route = graph_queries.query_shortest_route("MS01", "MS02", network="metro")

        self.assertTrue(route["found"])
        self.assertEqual(route["total_time_min"], 4)
        self.assertEqual([station["station_id"] for station in route["path"]], ["MS01", "MS03", "MS02"])

    def test_interchange_path_reports_interchange_points(self):
        with patch.object(graph_queries, "_load_graph", return_value=_demo_graph()):
            route = graph_queries.query_interchange_path("MS01", "NR01")

        self.assertTrue(route["found"])
        self.assertEqual(route["total_time_min"], 7)
        self.assertEqual(
            route["interchange_points"],
            [
                {
                    "from_station_id": "MS03",
                    "from_name": "Harbour",
                    "to_station_id": "NR01",
                    "to_name": "Central Rail",
                }
            ],
        )

    def test_delay_ripple_returns_nearby_stations(self):
        with patch.object(graph_queries, "_load_graph", return_value=_demo_graph()):
            affected = graph_queries.query_delay_ripple("MS01", hops=1)

        self.assertEqual(
            [station["station_id"] for station in affected],
            ["MS02", "MS03"],
        )


if __name__ == "__main__":
    unittest.main()
