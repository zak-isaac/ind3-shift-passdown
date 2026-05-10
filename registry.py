"""registry.py — Central catalogue of Walmart facilities + active-facility tracking.

Each facility gets its own SQLite database under dbs/{slug}.db.
The registry itself lives in registry.db at the project root.
"""
from __future__ import annotations
import math
import sqlite3
from datetime import datetime
from pathlib import Path

REGISTRY_PATH = Path(__file__).parent / "registry.db"
DBS_DIR       = Path(__file__).parent / "dbs"
DBS_DIR.mkdir(exist_ok=True)

# ── connection ────────────────────────────────────────────────────────────────

def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(REGISTRY_PATH)
    c.row_factory = sqlite3.Row
    return c

# ── seed data ─────────────────────────────────────────────────────────────────
# (slug, display_name, address, lat, lng, radius_miles)
_SEED: list[tuple] = [
    # ── Indiana ────────────────────────────────────────────────────────────
    ("IND1", "IND1 – Gas City, IN",       "2600 W Airport Rd, Gas City, IN 46933",            40.4884, -85.6169, 25.0),
    ("IND2", "IND2 – Shelbyville, IN",    "2700 Intelliplex Dr, Shelbyville, IN 46176",        39.5167, -85.7833, 25.0),
    ("IND3", "IND3 – Plainfield, IN",     "2800 Reeves Rd, Plainfield, IN 46168",              39.7045, -86.3977, 25.0),
    # ── Arkansas ──────────────────────────────────────────────────────────
    ("ARK1", "ARK1 – Clarksville, AR",    "I-40 & AR-103, Clarksville, AR 72830",              35.4737, -93.4662, 25.0),
    ("ARK2", "ARK2 – Bentonville, AR",    "702 SW 8th St, Bentonville, AR 72712",              36.3729, -94.2088, 25.0),
    ("ARK3", "ARK3 – Searcy, AR",         "2000 E Race Ave, Searcy, AR 72143",                 35.2509, -91.7326, 25.0),
    # ── Ohio ──────────────────────────────────────────────────────────────
    ("OHO1", "OHO1 – Washington CH, OH",  "1150 Cincinnati Ave, Washington Court House, OH",   39.5368, -83.4288, 25.0),
    # ── Virginia ──────────────────────────────────────────────────────────
    ("VIR1", "VIR1 – Mount Crawford, VA", "305 Kite Rd, Mount Crawford, VA 22841",             38.3543, -78.9357, 25.0),
    # ── Tennessee ─────────────────────────────────────────────────────────
    ("TEN1", "TEN1 – Shelbyville, TN",    "1 Walmart Way, Shelbyville, TN 37160",              35.4787, -86.4603, 25.0),
    # ── Texas ─────────────────────────────────────────────────────────────
    ("TEX1", "TEX1 – New Braunfels, TX",  "1130 N Walnut Ave, New Braunfels, TX 78130",        29.7155, -98.1244, 25.0),
    ("TEX2", "TEX2 – Fort Worth, TX",     "3400 Dunbar St, Fort Worth, TX 76119",              32.6963, -97.2592, 25.0),
    ("TEX3", "TEX3 – Carrollton, TX",     "2505 Avenue K, Carrollton, TX 75006",               32.9726, -96.9002, 25.0),
    ("TEX4", "TEX4 – Sanger, TX",         "900 Walmart Way, Sanger, TX 76266",                 33.3762, -97.1714, 25.0),
    # ── Kansas ────────────────────────────────────────────────────────────
    ("KAN1", "KAN1 – Edgerton (Gardner), KS", "32900 W 192nd St, Edgerton, KS 66021",         38.7578, -94.8883, 25.0),
    # ── California ────────────────────────────────────────────────────────
    ("CAL1", "CAL1 – Chino, CA",          "14650 Ramona Ave, Chino, CA 91710",                 34.0122,-117.6889, 25.0),
    ("CAL2", "CAL2 – Patterson, CA",      "2800 Westley Rd, Patterson, CA 95363",              37.4524,-121.1713, 25.0),
    ("CAL3", "CAL3 – Perris, CA",         "3550 Ethanac Rd, Perris, CA 92571",                 33.7959,-117.2172, 25.0),
    # ── Pennsylvania ──────────────────────────────────────────────────────
    ("PEN1", "PEN1 – Bethlehem/Allentown, PA", "1100 Postal Rd, Allentown, PA 18109",         40.6543, -75.4260, 25.0),
    # ── New Jersey ────────────────────────────────────────────────────────
    ("NJE1", "NJE1 – Pedricktown, NJ",    "250 Center Square Rd, Pedricktown, NJ 08067",       39.7465, -75.3985, 25.0),
    # ── Washington ────────────────────────────────────────────────────────
    ("WAS1", "WAS1 – Lacey, WA",          "4740 Pacific Ave SE, Lacey, WA 98503",              47.0379,-122.8177, 25.0),
    # ── South Dakota ──────────────────────────────────────────────────────
    ("SDK1", "SDK1 – Brookings, SD",      "22469 US-14, Brookings, SD 57006",                  44.3114, -96.7984, 25.0),
    # ── Utah ──────────────────────────────────────────────────────────────
    ("UTH1", "UTH1 – Hurricane, UT",      "1085 W SR-9, Hurricane, UT 84737",                  37.1753,-113.3158, 25.0),
    # ── Wisconsin ─────────────────────────────────────────────────────────
    ("WIS1", "WIS1 – Menomonie, WI",      "1500 Fairfax St, Menomonie, WI 54751",              44.8758, -91.9204, 25.0),
    # ── Illinois ──────────────────────────────────────────────────────────
    ("ILL1", "ILL1 – Brookport, IL",      "1 Distribution Blvd, Brookport, IL 62910",          37.1267, -88.6285, 25.0),
    # ── Georgia ───────────────────────────────────────────────────────────
    ("GEO1", "GEO1 – Douglasville, GA",   "9400 Westside Pkwy, Douglasville, GA 30135",        33.7291, -84.8068, 25.0),
    # ── North Carolina ────────────────────────────────────────────────────
    ("NCA1", "NCA1 – Mebane, NC",         "500 Mebane Oaks Rd, Mebane, NC 27302",              36.0957, -79.2839, 25.0),
    # ── Florida ───────────────────────────────────────────────────────────
    ("FLA1", "FLA1 – Davenport, FL",      "2500 State Road 60, Davenport, FL 33837",           28.1597, -81.5942, 25.0),
    ("FLA2", "FLA2 – Brooksville, FL",    "1 Walmart Way, Brooksville, FL 34601",              28.5200, -82.3949, 25.0),
    # ── Colorado ──────────────────────────────────────────────────────────
    ("COL1", "COL1 – Loveland, CO",       "5990 Crossroads Blvd, Loveland, CO 80538",          40.4233,-104.9719, 25.0),
    # ── Missouri ──────────────────────────────────────────────────────────
    ("MIS1", "MIS1 – Moberly, MO",        "US-24, Moberly, MO 65270",                          39.4186, -92.4349, 25.0),
]


def init_registry() -> None:
    """Create registry tables and seed facility data."""
    with _conn() as c:
        c.executescript("""
            CREATE TABLE IF NOT EXISTS facilities (
                slug         TEXT PRIMARY KEY,
                name         TEXT NOT NULL,
                address      TEXT NOT NULL DEFAULT '',
                lat          REAL,
                lng          REAL,
                radius_miles REAL NOT NULL DEFAULT 25.0,
                verified     INTEGER NOT NULL DEFAULT 0,
                created_at   TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS registry_settings (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
        """)
    _seed_facilities()


def _seed_facilities() -> None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with _conn() as c:
        for (slug, name, address, lat, lng, radius) in _SEED:
            c.execute(
                "INSERT OR IGNORE INTO facilities "
                "(slug, name, address, lat, lng, radius_miles, verified, created_at) "
                "VALUES (?,?,?,?,?,?,1,?)",
                (slug, name, address, lat, lng, radius, now),
            )
        c.execute(
            "INSERT OR IGNORE INTO registry_settings (key,value) VALUES ('active_facility','IND3')"
        )


# ── queries ───────────────────────────────────────────────────────────────────

def list_facilities() -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT slug, name, address, lat, lng, radius_miles, verified "
            "FROM facilities ORDER BY slug"
        ).fetchall()
    return [dict(r) for r in rows]


def get_facility(slug: str) -> dict | None:
    with _conn() as c:
        row = c.execute("SELECT * FROM facilities WHERE slug=?", (slug,)).fetchone()
    return dict(row) if row else None


def get_active_slug() -> str:
    with _conn() as c:
        row = c.execute(
            "SELECT value FROM registry_settings WHERE key='active_facility'"
        ).fetchone()
    return row["value"] if row else "IND3"


def set_active_facility(slug: str) -> None:
    with _conn() as c:
        c.execute(
            "INSERT INTO registry_settings (key,value) VALUES ('active_facility',?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (slug,),
        )


def add_facility(slug: str, name: str, address: str,
                 lat: float | None, lng: float | None,
                 radius_miles: float = 25.0) -> None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with _conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO facilities "
            "(slug, name, address, lat, lng, radius_miles, verified, created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (slug.upper().strip(), name.strip(), address.strip(),
             lat, lng, radius_miles, 1 if lat and lng else 0, now),
        )


def delete_facility(slug: str) -> None:
    if slug == get_active_slug():
        raise ValueError("Cannot delete the currently active facility.")
    with _conn() as c:
        c.execute("DELETE FROM facilities WHERE slug=?", (slug,))


def db_path_for(slug: str) -> "Path":
    return DBS_DIR / f"{slug.lower()}.db"


# ── geofencing ────────────────────────────────────────────────────────────────

def haversine_miles(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance in miles between two lat/lng points."""
    R = 3958.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a  = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return R * 2 * math.asin(math.sqrt(a))


def check_location(user_lat: float, user_lng: float, slug: str) -> dict:
    """Return distance info and whether user is within the facility's geofence."""
    fac = get_facility(slug)
    if not fac or fac["lat"] is None or fac["lng"] is None:
        return {"ok": True, "distance_mi": None, "facility": slug, "reason": "no_coords"}
    dist = haversine_miles(user_lat, user_lng, fac["lat"], fac["lng"])
    ok   = dist <= fac["radius_miles"]
    return {
        "ok":          ok,
        "distance_mi": round(dist, 1),
        "radius_mi":   fac["radius_miles"],
        "facility":    fac["name"],
        "slug":        slug,
    }
