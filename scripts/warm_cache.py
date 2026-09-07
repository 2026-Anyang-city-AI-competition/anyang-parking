#!/usr/bin/env python3
"""
Cache warming script for walking and routing caches.
Pre-fills the walk and route caches with common destination-parking pairs.
"""
import sys
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.serve.walking import walk_time
from src.serve.routing import multi_eta, future_eta
from src.serve.candidates import find_candidates

ROOT = Path(__file__).resolve().parents[2]
KST = timezone(timedelta(hours=9))

def warm_walk_cache():
    """Walk through a set of destinations and parking lots to fill the walk cache."""
    print("[warm] Warming walk cache...")
    # Load parking info for 안양
    con = sqlite3.connect(ROOT / "data/raw/parking.db")
    cur = con.execute(
        "SELECT parking_id, latitude, longitude, name FROM parking_info WHERE city='안양'"
    )
    parking_lots = [{"pid": row[0], "lat": row[1], "lng": row[2], "name": row[3]} for row in cur.fetchall()]
    con.close()

    # Select a few representative destinations (from rank sensitivity)
    destinations = [
        ("안양시청", 37.394259, 126.956861),
        ("석수역", 37.435093, 126.902321),
        ("범계역", 37.389784, 126.950783),
        ("안양역", 37.401800, 126.922600),
        ("평촌역", 37.394400, 126.963900),
        ("인덕원역", 37.401600, 126.976000),
    ]

    total = len(parking_lots) * len(destinations)
    count = 0
    for dest_name, dlat, dlon in destinations:
        for lot in parking_lots:
            # Use walking.walk_time which will use cache and TMAP (or fallback)
            walk_time(
                parking_id=lot["pid"],
                start=(lot["lat"], lot["lng"]),  # start from parking lot
                end=(dlat, dlon),                # end at destination
                start_name=lot["name"],
                end_name=dest_name,
                use_cache=True,                  # we want to fill the cache
                key=None                         # will try to get key from env (likely invalid, so will fallback)
            )
            count += 1
            if count % 100 == 0:
                print(f"[warm] Processed {count}/{total} walk pairs")
    print(f"[warm] Walk cache warming complete. Processed {total} pairs.")

def warm_route_cache():
    """Fill the route cache with common origin-destination pairs."""
    print("[warm] Warming route cache...")
    # Load parking info for 안양
    con = sqlite3.connect(ROOT / "data/raw/parking.db")
    cur = con.execute(
        "SELECT parking_id, latitude, longitude, name FROM parking_info WHERE city='안양'"
    )
    parking_lots = [{"pid": row[0], "lat": row[1], "lng": row[2], "name": row[3]} for row in cur.fetchall()]
    con.close()

    # Use a few origins (e.g., major stations)
    origins = [
        ("안양역", 37.401800, 126.922600),
        ("범계역", 37.389784, 126.950783),
        ("평촌역", 37.394400, 126.963900),
    ]

    # For each origin, compute routes to all parking lots
    total = len(origins) * len(parking_lots)
    count = 0
    for orig_name, olat, olng in origins:
        # Build dests dict for multi_eta
        dests = {lot["pid"]: (lot["lat"], lot["lng"]) for lot in parking_lots}
        # Call multi_eta (this will use the route cache internally)
        multi_eta(
            origin=(olat, olng),
            dests=dests,
            radius=20000,  # large radius to cover all
            key=None,      # will try to get key from env (likely invalid, so will fallback)
            use_cache=True
        )
        count += len(parking_lots)
        print(f"[warm] Processed {count}/{total} route pairs for origin {orig_name}")
    print(f"[warm] Route cache warming complete. Processed {total} pairs.")

def warm_future_route_cache():
    """Fill the route cache with future departure times."""
    print("[warm] Warming future route cache...")
    # Load parking info for 안양
    con = sqlite3.connect(ROOT / "data/raw/parking.db")
    cur = con.execute(
        "SELECT parking_id, latitude, longitude, name FROM parking_info WHERE city='안양'"
    )
    parking_lots = [{"pid": row[0], "lat": row[1], "lng": row[2], "name": row[3]} for row in cur.fetchall()]
    con.close()

    # Use a few origins and destinations
    origins = [("안양역", 37.401800, 126.922600)]
    destinations = [("범계역", 37.389784, 126.950783), ("평촌역", 37.394400, 126.963900)]

    total = len(origins) * len(destinations) * len(parking_lots)  # actually we do origins x destinations for each origin? Let's simplify.
    # We'll just do a few future_eta calls.
    count = 0
    for orig_name, olat, olng in origins:
        for dest_name, dlat, dlon in destinations:
            # Future departure 30 minutes from now
            future_eta(
                origin=(olat, olng),
                dest=(dlat, dlon),
                minutes_ahead=30,
                key=None
            )
            count += 1
            print(f"[warm] Future eta {count}: {orig_name} -> {dest_name}")
    print(f"[warm] Future route cache warming complete. Processed {count} pairs.")

if __name__ == "__main__":
    warm_walk_cache()
    warm_route_cache()
    warm_future_route_cache()
    print("[warm] All caches warmed.")