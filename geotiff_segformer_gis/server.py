#!/usr/bin/env python3
"""
GeoTIFF SegFormer GIS - Real Georeferenced 3D ULPIN Backend API
================================================================
Serves real-world geographic coordinates (WGS84 lon/lat EPSG:4326):
  1. GET /query/              -> List/search buildings as GeoJSON FeatureCollection
  2. GET /query/:ulpin/       -> One building with its floors list OR one specific floor
  3. GET /conflicts/          -> List of cadastral overlap/encroachment conflicts
  4. GET /health              -> Backend service health check
"""

import json
import os
import re
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PORT = int(os.environ.get("PORT", 3000))

# Real-world coordinate cluster: Smart City IT & Commercial Corridor (Kochi/Ernakulam, Kerala)
# Base coordinates ~ 76.354° E, 10.012° N (WGS84 EPSG:4326)
BASE_LON = 76.3540
BASE_LAT = 10.0120

# Palette matching backend pipeline --color-by scheme
PALETTE = {
    "basement": "#64748B",
    "f1": "#3E8ED0",
    "f2": "#2ECC71",
    "f3": "#F39C12",
    "f4": "#E74C3C",
    "f5": "#9B59B6",
    "f6": "#1ABC9C",
    "f7": "#D35400",
    "f8": "#34495E"
}

# Helper to offset lon/lat by local delta meters
# 1 deg lat ~ 111,320m; 1 deg lon ~ 111,320m * cos(lat)
LAT_M = 111320.0
LON_M = 111320.0 * 0.9848  # cos(10 deg) ~ 0.9848

def make_polygon(min_xm, min_ym, max_xm, max_ym):
    coords = [
        [round(BASE_LON + min_xm / LON_M, 7), round(BASE_LAT + min_ym / LAT_M, 7)],
        [round(BASE_LON + max_xm / LON_M, 7), round(BASE_LAT + min_ym / LAT_M, 7)],
        [round(BASE_LON + max_xm / LON_M, 7), round(BASE_LAT + max_ym / LAT_M, 7)],
        [round(BASE_LON + min_xm / LON_M, 7), round(BASE_LAT + max_ym / LAT_M, 7)],
        [round(BASE_LON + min_xm / LON_M, 7), round(BASE_LAT + min_ym / LAT_M, 7)]
    ]
    return {"type": "Polygon", "coordinates": [coords]}

# Building catalog with real georeferenced footprints
BUILDINGS_DATA = [
    {
        "ulpin": "KL-14-0231-9F72",
        "building_id": 7,
        "name": "Infopark Horizon Tower 7",
        "floor_count": 4,
        "building_height": 14.4,
        "class_name": "commercial_office",
        "has_conflict": True,
        "min_m": (20, 20),
        "max_m": (65, 75),
        "floors_spec": [
            {"index": -1, "name": "B1 - Sub-surface Parking & Power", "z_min": -3.5, "z_max": 0.0, "color": PALETTE["basement"]},
            {"index": 1,  "name": "F1 - Grand Atrium & Commercial Banking", "z_min": 0.0, "z_max": 3.6, "color": PALETTE["f1"]},
            {"index": 2,  "name": "F2 - Tech Innovation Hub (Floor 2)", "z_min": 3.6, "z_max": 7.2, "color": PALETTE["f2"]},
            {"index": 3,  "name": "F3 - Cloud Data Systems (Floor 3)", "z_min": 7.2, "z_max": 10.8, "color": PALETTE["f3"]},
            {"index": 4,  "name": "F4 - Executive Suites & Boardrooms", "z_min": 10.8, "z_max": 14.4, "color": PALETTE["f4"]}
        ]
    },
    {
        "ulpin": "KL-14-0231-9F73",
        "building_id": 8,
        "name": "Cyber Hub Commercial Annex",
        "floor_count": 3,
        "building_height": 10.8,
        "class_name": "commercial_office",
        "has_conflict": True,
        "min_m": (60, 40),  # Intentionally overlaps building 7 by 5 meters between x=60 and 65
        "max_m": (110, 85),
        "floors_spec": [
            {"index": 1, "name": "F1 - Ground Commercial Showroom", "z_min": 0.0, "z_max": 3.6, "color": PALETTE["f1"]},
            {"index": 2, "name": "F2 - Corporate Coworking Space", "z_min": 3.6, "z_max": 7.2, "color": PALETTE["f2"]},
            {"index": 3, "name": "F3 - Design Studio & Media Lab", "z_min": 7.2, "z_max": 10.8, "color": PALETTE["f3"]}
        ]
    },
    {
        "ulpin": "KL-14-0231-8A14",
        "building_id": 12,
        "name": "Gateway Trade Center",
        "floor_count": 6,
        "building_height": 21.6,
        "class_name": "commercial_mixed",
        "has_conflict": True,
        "min_m": (130, 20),
        "max_m": (185, 80),
        "floors_spec": [
            {"index": -1, "name": "B1 - Basement Parking Level 1", "z_min": -3.5, "z_max": 0.0, "color": PALETTE["basement"]},
            {"index": 1,  "name": "F1 - Retail Promenade & Cafeteria", "z_min": 0.0, "z_max": 3.6, "color": PALETTE["f1"]},
            {"index": 2,  "name": "F2 - Multi-tenant Corporate Floor", "z_min": 3.6, "z_max": 7.2, "color": PALETTE["f2"]},
            {"index": 3,  "name": "F3 - FinTech Operation Centers", "z_min": 7.2, "z_max": 10.8, "color": PALETTE["f3"]},
            {"index": 4,  "name": "F4 - Legal & Compliance Chambers", "z_min": 10.8, "z_max": 14.4, "color": PALETTE["f4"]},
            {"index": 5,  "name": "F5 - R&D Engineering Wing", "z_min": 14.4, "z_max": 18.0, "color": PALETTE["f5"]},
            {"index": 6,  "name": "F6 - Penthouse Executive Suites", "z_min": 18.0, "z_max": 21.6, "color": PALETTE["f6"]}
        ]
    },
    {
        "ulpin": "KL-14-0231-7B21",
        "building_id": 15,
        "name": "Central Cadastral & Revenue Bhawan",
        "floor_count": 5,
        "building_height": 18.0,
        "class_name": "institutional",
        "has_conflict": False,
        "min_m": (20, 110),
        "max_m": (75, 175),
        "floors_spec": [
            {"index": 1, "name": "F1 - Public Registry & Helpdesk", "z_min": 0.0, "z_max": 3.6, "color": PALETTE["f1"]},
            {"index": 2, "name": "F2 - Land Survey & GIS Directorate", "z_min": 3.6, "z_max": 7.2, "color": PALETTE["f2"]},
            {"index": 3, "name": "F3 - Title Verification Division", "z_min": 7.2, "z_max": 10.8, "color": PALETTE["f3"]},
            {"index": 4, "name": "F4 - Digital Records Data Center", "z_min": 10.8, "z_max": 14.4, "color": PALETTE["f4"]},
            {"index": 5, "name": "F5 - Commissioner Conference Hall", "z_min": 14.4, "z_max": 18.0, "color": PALETTE["f5"]}
        ]
    },
    {
        "ulpin": "KL-14-0231-6C33",
        "building_id": 19,
        "name": "Palm Residency Tower A",
        "floor_count": 8,
        "building_height": 28.8,
        "class_name": "residential_highrise",
        "has_conflict": False,
        "min_m": (100, 110),
        "max_m": (155, 170),
        "floors_spec": [
            {"index": -2, "name": "B2 - Deep Mechanical Services", "z_min": -7.0, "z_max": -3.5, "color": "#475569"},
            {"index": -1, "name": "B1 - Resident Underground Parking", "z_min": -3.5, "z_max": 0.0, "color": PALETTE["basement"]},
            {"index": 1,  "name": "F1 - Entrance Lobby & Community Hall", "z_min": 0.0, "z_max": 3.6, "color": PALETTE["f1"]},
            {"index": 2,  "name": "F2 - Residential Apartments 201-204", "z_min": 3.6, "z_max": 7.2, "color": PALETTE["f2"]},
            {"index": 3,  "name": "F3 - Residential Apartments 301-304", "z_min": 7.2, "z_max": 10.8, "color": PALETTE["f3"]},
            {"index": 4,  "name": "F4 - Residential Apartments 401-404", "z_min": 10.8, "z_max": 14.4, "color": PALETTE["f4"]},
            {"index": 5,  "name": "F5 - Residential Apartments 501-504", "z_min": 14.4, "z_max": 18.0, "color": PALETTE["f5"]},
            {"index": 6,  "name": "F6 - Residential Apartments 601-604", "z_min": 18.0, "z_max": 21.6, "color": PALETTE["f6"]},
            {"index": 7,  "name": "F7 - Premium Sky Flats 701-702", "z_min": 21.6, "z_max": 25.2, "color": PALETTE["f7"]},
            {"index": 8,  "name": "F8 - Luxury Penthouse & Terrace Garden", "z_min": 25.2, "z_max": 28.8, "color": PALETTE["f8"]}
        ]
    },
    {
        "ulpin": "KL-14-0231-5D49",
        "building_id": 24,
        "name": "Smart Transit Terminal",
        "floor_count": 2,
        "building_height": 7.2,
        "class_name": "public_infrastructure",
        "has_conflict": False,
        "min_m": (180, 115),
        "max_m": (250, 160),
        "floors_spec": [
            {"index": 1, "name": "F1 - Bus & Metro Interchange concourse", "z_min": 0.0, "z_max": 3.6, "color": PALETTE["f1"]},
            {"index": 2, "name": "F2 - Transit Control Operations & Security", "z_min": 3.6, "z_max": 7.2, "color": PALETTE["f2"]}
        ]
    },
    {
        "ulpin": "KL-14-0231-4E55",
        "building_id": 28,
        "name": "District Multi-Specialty Clinic",
        "floor_count": 4,
        "building_height": 14.4,
        "class_name": "healthcare",
        "has_conflict": False,
        "min_m": (30, 200),
        "max_m": (85, 260),
        "floors_spec": [
            {"index": 1, "name": "F1 - Emergency & Outpatient Department", "z_min": 0.0, "z_max": 3.6, "color": PALETTE["f1"]},
            {"index": 2, "name": "F2 - Radiology & Diagnostic Labs", "z_min": 3.6, "z_max": 7.2, "color": PALETTE["f2"]},
            {"index": 3, "name": "F3 - Inpatient Wards & Nursing Station", "z_min": 7.2, "z_max": 10.8, "color": PALETTE["f3"]},
            {"index": 4, "name": "F4 - Operation Theatres & Intensive Care", "z_min": 10.8, "z_max": 14.4, "color": PALETTE["f4"]}
        ]
    },
    {
        "ulpin": "KL-14-0231-3F68",
        "building_id": 31,
        "name": "Greenfield Academic Library",
        "floor_count": 3,
        "building_height": 10.8,
        "class_name": "educational",
        "has_conflict": False,
        "min_m": (110, 205),
        "max_m": (165, 255),
        "floors_spec": [
            {"index": 1, "name": "F1 - Digital Catalog & Reference Hall", "z_min": 0.0, "z_max": 3.6, "color": PALETTE["f1"]},
            {"index": 2, "name": "F2 - Science & Technology Stacks", "z_min": 3.6, "z_max": 7.2, "color": PALETTE["f2"]},
            {"index": 3, "name": "F3 - Quiet Study & Multimedia Archive", "z_min": 7.2, "z_max": 10.8, "color": PALETTE["f3"]}
        ]
    }
]

# Detected Conflicts Dataset (matches conflicts.geojson)
CONFLICTS_DATA = [
    {
        "conflict_id": "CONF-2026-001",
        "conflict_type": "footprint_overlap",
        "building_ids": [7, 8],
        "ulpins": ["KL-14-0231-9F72", "KL-14-0231-9F73"],
        "severity": "high",
        "description": "Footprint boundary overlap between Infopark Tower 7 and Cyber Hub Annex (5.0m overlap on eastern boundary)",
        "remediation_action": "Requires Joint Cadastral Resurvey by Tahsildar",
        "geometry": make_polygon(60, 40, 65, 75)
    },
    {
        "conflict_id": "CONF-2026-002",
        "conflict_type": "boundary_encroachment",
        "building_ids": [12],
        "ulpins": ["KL-14-0231-8A14"],
        "severity": "medium",
        "description": "Gateway Trade Center boundary extends 1.8m into statutory urban road reserve corridor",
        "remediation_action": "Notice issued under Kerala Municipality Building Rules Section 27",
        "geometry": make_polygon(130, 20, 138, 35)
    }
]

# Build in-memory dictionaries for instant lookup
BUILDINGS_BY_ULPIN = {}
FLOORS_BY_ULPIN = {}
FEATURES_LIST = []

for b in BUILDINGS_DATA:
    geom = make_polygon(b["min_m"][0], b["min_m"][1], b["max_m"][0], b["max_m"][1])
    b_ulpin = b["ulpin"]
    
    # GeoJSON Feature for GET /query/
    feature = {
        "type": "Feature",
        "properties": {
            "ulpin": b_ulpin,
            "building_id": b["building_id"],
            "name": b["name"],
            "floor_count": b["floor_count"],
            "building_height": b["building_height"],
            "class_name": b["class_name"],
            "has_conflict": b["has_conflict"]
        },
        "geometry": geom
    }
    FEATURES_LIST.append(feature)

    # Detailed building representation with floors list
    floors_list = []
    for f in b["floors_spec"]:
        suffix = f"-F{abs(f['index']):02d}" if f["index"] > 0 else f"-B{abs(f['index']):02d}"
        f_ulpin = f"{b_ulpin}{suffix}"
        
        floor_feature = {
            "type": "Feature",
            "properties": {
                "ulpin": f_ulpin,
                "building_ulpin": b_ulpin,
                "building_id": b["building_id"],
                "floor_index": f["index"],
                "floor_name": f["name"],
                "z_min": f["z_min"],
                "z_max": f["z_max"],
                "color": f["color"],
                "area_sqm": round((b["max_m"][0] - b["min_m"][0]) * (b["max_m"][1] - b["min_m"][1]), 1),
                "legal_status": "Freehold Registered",
                "tax_clearance": "FY 2025-26 Paid"
            },
            "geometry": geom
        }
        floors_list.append(floor_feature)
        FLOORS_BY_ULPIN[f_ulpin] = floor_feature

    BUILDINGS_BY_ULPIN[b_ulpin] = {
        "type": "Feature",
        "properties": {
            "ulpin": b_ulpin,
            "building_id": b["building_id"],
            "name": b["name"],
            "floor_count": b["floor_count"],
            "building_height": b["building_height"],
            "class_name": b["class_name"],
            "has_conflict": b["has_conflict"],
            "conflict_info": next((c for c in CONFLICTS_DATA if b_ulpin in c["ulpins"]), None),
            "cadastral_survey": {
                "survey_no": f"Sy. {b['building_id'] * 12}/2",
                "village": "Kakkanad Revenue Block",
                "district": "Ernakulam",
                "state": "Kerala",
                "crs": "EPSG:4326 (WGS84)"
            }
        },
        "geometry": geom,
        "floors": floors_list
    }


def generate_fallback_building(ulpin_str: str) -> dict:
    """Generate dynamic valid georeferenced building for any custom queried ULPIN."""
    h = sum(ord(c) for c in ulpin_str)
    dx = (h % 200) + 10
    dy = ((h * 3) % 200) + 10
    geom = make_polygon(dx, dy, dx + 40, dy + 40)
    floors = []
    f_count = max(2, (h % 6) + 1)
    height = f_count * 3.6

    for i in range(1, f_count + 1):
        f_ulpin = f"{ulpin_str}-F{i:02d}"
        color = list(PALETTE.values())[i % len(PALETTE)]
        floors.append({
            "type": "Feature",
            "properties": {
                "ulpin": f_ulpin,
                "building_ulpin": ulpin_str,
                "floor_index": i,
                "floor_name": f"Floor {i}",
                "z_min": round((i - 1) * 3.6, 1),
                "z_max": round(i * 3.6, 1),
                "color": color,
                "area_sqm": 1600.0
            },
            "geometry": geom
        })

    return {
        "type": "Feature",
        "properties": {
            "ulpin": ulpin_str,
            "building_id": h % 100,
            "name": f"Survey Parcel {ulpin_str}",
            "floor_count": f_count,
            "building_height": height,
            "class_name": "mixed_property",
            "has_conflict": False
        },
        "geometry": geom,
        "floors": floors
    }


class RealGISRequestHandler(BaseHTTPRequestHandler):
    def _send_cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-Requested-With")

    def do_OPTIONS(self):
        self.send_response(204)
        self._send_cors()
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/")
        if not path:
            path = "/"

        # 1. GET /query/ or GET /query -> list buildings as GeoJSON FeatureCollection
        if path == "/query":
            fc = {
                "type": "FeatureCollection",
                "name": "Real_Georeferenced_Buildings_ULPIN",
                "crs": {
                    "type": "name",
                    "properties": {"name": "EPSG:4326"}
                },
                "features": FEATURES_LIST
            }
            body = json.dumps(fc, indent=2).encode("utf-8")
            self.send_response(200)
            self._send_cors()
            self.send_header("Content-Type", "application/geo+json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        # 2. GET /conflicts -> list of detected boundary conflicts
        if path == "/conflicts":
            body = json.dumps(CONFLICTS_DATA, indent=2).encode("utf-8")
            self.send_response(200)
            self._send_cors()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        # 3. GET /health
        if path == "/health":
            body = json.dumps({
                "status": "ok",
                "service": "3D ULPIN Georeferenced Backend",
                "crs": "EPSG:4326 (WGS84)",
                "survey_center": {"lon": BASE_LON, "lat": BASE_LAT},
                "building_count": len(FEATURES_LIST),
                "conflict_count": len(CONFLICTS_DATA)
            }).encode("utf-8")
            self.send_response(200)
            self._send_cors()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        # 4. GET /query/:ulpin/ -> single building with floors OR single floor
        match = re.match(r"^/query/(.+)$", path)
        if match:
            raw_id = match.group(1).rstrip("/")
            ulpin = urllib.parse.unquote(raw_id).strip()

            # Check if this is a floor ULPIN (e.g. KL-14-0231-9F72-F02 or -B01)
            if ulpin in FLOORS_BY_ULPIN:
                floor_item = FLOORS_BY_ULPIN[ulpin]
                body = json.dumps(floor_item, indent=2).encode("utf-8")
                self.send_response(200)
                self._send_cors()
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

            # Check if this is a base building ULPIN (e.g. KL-14-0231-9F72)
            if ulpin in BUILDINGS_BY_ULPIN:
                building_item = BUILDINGS_BY_ULPIN[ulpin]
                body = json.dumps(building_item, indent=2).encode("utf-8")
                self.send_response(200)
                self._send_cors()
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

            # If floor suffix is requested on unknown building
            if re.search(r"-[FB]\d{2}$", ulpin):
                base = re.sub(r"-[FB]\d{2}$", "", ulpin)
                parent = generate_fallback_building(base)
                # find floor
                floor = next((f for f in parent["floors"] if f["properties"]["ulpin"] == ulpin), None)
                if floor:
                    body = json.dumps(floor, indent=2).encode("utf-8")
                else:
                    body = json.dumps({"error": f"Floor '{ulpin}' not found"}).encode("utf-8")
            else:
                fallback = generate_fallback_building(ulpin)
                body = json.dumps(fallback, indent=2).encode("utf-8")

            self.send_response(200)
            self._send_cors()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        # 5. Default root GET /
        if path == "/":
            root_info = {
                "service": "3D ULPIN Property Viewer Backend (SIH 2026 / PS 26011)",
                "endpoints": {
                    "buildings": "/query/",
                    "building_or_floor": "/query/:ulpin/",
                    "conflicts": "/conflicts/",
                    "health": "/health"
                }
            }
            body = json.dumps(root_info, indent=2).encode("utf-8")
            self.send_response(200)
            self._send_cors()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        # 404
        err_body = json.dumps({"error": f"Path '{path}' not found"}).encode("utf-8")
        self.send_response(404)
        self._send_cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(err_body)))
        self.end_headers()
        self.wfile.write(err_body)

    def log_message(self, format, *args):
        print(f"[ULPIN Backend] {self.address_string()} - {format % args}")


def main():
    server_address = ("0.0.0.0", PORT)
    httpd = ThreadingHTTPServer(server_address, RealGISRequestHandler)
    print("=" * 70)
    print("SIH 2026 / PS 26011 - Real Georeferenced 3D ULPIN Backend Server")
    print(f"Listening on http://0.0.0.0:{PORT}")
    print("  GET /query/         -> Buildings GeoJSON (WGS84 lon/lat)")
    print("  GET /query/:ulpin/  -> Single building (with floors) OR single floor")
    print("  GET /conflicts/     -> Overlap / gap / encroachment conflict records")
    print("  GET /health         -> Health status")
    print("=" * 70)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping server...")
        httpd.server_close()


if __name__ == "__main__":
    main()
