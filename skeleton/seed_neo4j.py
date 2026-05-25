"""
TransitFlow — Neo4j Seeder
Run once after starting Docker:
    python skeleton/seed_neo4j.py

Loads station and network data from train-mock-data/:
  - metro_stations.json         — city metro stations and adjacencies
  - national_rail_stations.json — national rail stations and adjacencies

Design your graph schema (node labels, relationship types, properties)
based on the data in these files, then implement the seed() function below.
"""

import json
import os
import sys

sys.path.insert(0, ".")

from neo4j import GraphDatabase
from skeleton.config import NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD

_DATA_DIR = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "train-mock-data")
)


def _load(filename):
    with open(os.path.join(_DATA_DIR, filename), encoding="utf-8") as f:
        return json.load(f)


def seed():
    metro_stations = _load("metro_stations.json")
    rail_stations  = _load("national_rail_stations.json")

    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    with driver.session() as session:

        session.run("MATCH (n) DETACH DELETE n")
        print("  Cleared existing graph data")

        session.run(
            "CREATE CONSTRAINT station_id_unique IF NOT EXISTS "
            "FOR (s:Station) REQUIRE s.station_id IS UNIQUE"
        )

        for station in metro_stations:
            session.run(
                """
                MERGE (s:Station:MetroStation {station_id: $station_id})
                SET s.name = $name,
                    s.network = 'metro',
                    s.lines = $lines,
                    s.is_interchange_metro = $is_interchange_metro,
                    s.interchange_metro_lines = $interchange_metro_lines,
                    s.is_interchange_national_rail = $is_interchange_national_rail,
                    s.interchange_national_rail_station_id = $interchange_national_rail_station_id
                """,
                station_id=station["station_id"],
                name=station["name"],
                lines=station.get("lines", []),
                is_interchange_metro=station.get("is_interchange_metro", False),
                interchange_metro_lines=station.get("interchange_metro_lines", []),
                is_interchange_national_rail=station.get("is_interchange_national_rail", False),
                interchange_national_rail_station_id=station.get(
                    "interchange_national_rail_station_id"
                ),
            )
        print(f"  Created {len(metro_stations)} MetroStation nodes")

        for station in rail_stations:
            session.run(
                """
                MERGE (s:Station:NationalRailStation {station_id: $station_id})
                SET s.name = $name,
                    s.network = 'rail',
                    s.lines = $lines,
                    s.is_interchange_national_rail = $is_interchange_national_rail,
                    s.interchange_national_rail_lines = $interchange_national_rail_lines,
                    s.is_interchange_metro = $is_interchange_metro,
                    s.interchange_metro_station_id = $interchange_metro_station_id
                """,
                station_id=station["station_id"],
                name=station["name"],
                lines=station.get("lines", []),
                is_interchange_national_rail=station.get(
                    "is_interchange_national_rail", False
                ),
                interchange_national_rail_lines=station.get(
                    "interchange_national_rail_lines", []
                ),
                is_interchange_metro=station.get("is_interchange_metro", False),
                interchange_metro_station_id=station.get("interchange_metro_station_id"),
            )
        print(f"  Created {len(rail_stations)} NationalRailStation nodes")

        metro_links = 0
        for station in metro_stations:
            for adjacent in station.get("adjacent_stations", []):
                session.run(
                    """
                    MATCH (a:MetroStation {station_id: $from_id})
                    MATCH (b:MetroStation {station_id: $to_id})
                    MERGE (a)-[r:METRO_LINK {line: $line}]->(b)
                    SET r.travel_time_min = $travel_time_min,
                        r.fare_standard_usd = 0.30,
                        r.fare_first_usd = 0.30
                    """,
                    from_id=station["station_id"],
                    to_id=adjacent["station_id"],
                    line=adjacent["line"],
                    travel_time_min=adjacent["travel_time_min"],
                )
                metro_links += 1
        print(f"  Created {metro_links} METRO_LINK relationships")

        rail_links = 0
        for station in rail_stations:
            for adjacent in station.get("adjacent_stations", []):
                session.run(
                    """
                    MATCH (a:NationalRailStation {station_id: $from_id})
                    MATCH (b:NationalRailStation {station_id: $to_id})
                    MERGE (a)-[r:RAIL_LINK {line: $line}]->(b)
                    SET r.travel_time_min = $travel_time_min,
                        r.fare_standard_usd = 1.50,
                        r.fare_first_usd = 2.50
                    """,
                    from_id=station["station_id"],
                    to_id=adjacent["station_id"],
                    line=adjacent["line"],
                    travel_time_min=adjacent["travel_time_min"],
                )
                rail_links += 1
        print(f"  Created {rail_links} RAIL_LINK relationships")

        interchanges = 0
        for station in metro_stations:
            rail_station_id = station.get("interchange_national_rail_station_id")
            if not rail_station_id:
                continue
            session.run(
                """
                MATCH (m:MetroStation {station_id: $metro_station_id})
                MATCH (r:NationalRailStation {station_id: $rail_station_id})
                MERGE (m)-[to_rail:INTERCHANGE_TO]->(r)
                SET to_rail.line = 'interchange',
                    to_rail.travel_time_min = 5,
                    to_rail.fare_standard_usd = 0.00,
                    to_rail.fare_first_usd = 0.00
                MERGE (r)-[to_metro:INTERCHANGE_TO]->(m)
                SET to_metro.line = 'interchange',
                    to_metro.travel_time_min = 5,
                    to_metro.fare_standard_usd = 0.00,
                    to_metro.fare_first_usd = 0.00
                """,
                metro_station_id=station["station_id"],
                rail_station_id=rail_station_id,
            )
            interchanges += 2
        print(f"  Created {interchanges} INTERCHANGE_TO relationships")

    driver.close()
    print("\nNeo4j graph seeded successfully.")
    print("   Open http://localhost:7475 to explore the graph.")


if __name__ == "__main__":
    print("Connecting to Neo4j...")
    seed()
