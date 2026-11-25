#!/usr/bin/env python3
"""In My Home: local-first API + static UI. Standard library only."""
import base64
import datetime
import json
import os
import re
import sqlite3
import socket
import sys
import urllib.error
import urllib.request
import uuid

import gdrive
import linkinfo
import report as report_mod
import receipt_module as rm
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
DATA_DIR = os.environ.get("INVENTORY_DATA_DIR", os.path.join(BASE_DIR, "data"))

ITEM_TYPES = ["Appliance", "Furniture", "Electronics", "Memorabilia",
              "Media", "Collectible", "Clothing"]
RECEIPT_CATEGORIES = ITEM_TYPES + ["Consumable", "Other"]  # receipt lines are sorted into these
SOURCES = ["manual", "template", "vision"]
CONDITIONS = ["New", "Refurbished", "Used - like new", "Used - good", "Used - fair", "Used - poor"]
DESCRIPTION_MAX = 250   # characters
MODEL_MAX = 60

US_STATES = [
    ("AL", "Alabama"), ("AK", "Alaska"), ("AZ", "Arizona"), ("AR", "Arkansas"),
    ("CA", "California"), ("CO", "Colorado"), ("CT", "Connecticut"), ("DE", "Delaware"),
    ("DC", "District of Columbia"), ("FL", "Florida"), ("GA", "Georgia"), ("HI", "Hawaii"),
    ("ID", "Idaho"), ("IL", "Illinois"), ("IN", "Indiana"), ("IA", "Iowa"),
    ("KS", "Kansas"), ("KY", "Kentucky"), ("LA", "Louisiana"), ("ME", "Maine"),
    ("MD", "Maryland"), ("MA", "Massachusetts"), ("MI", "Michigan"), ("MN", "Minnesota"),
    ("MS", "Mississippi"), ("MO", "Missouri"), ("MT", "Montana"), ("NE", "Nebraska"),
    ("NV", "Nevada"), ("NH", "New Hampshire"), ("NJ", "New Jersey"), ("NM", "New Mexico"),
    ("NY", "New York"), ("NC", "North Carolina"), ("ND", "North Dakota"), ("OH", "Ohio"),
    ("OK", "Oklahoma"), ("OR", "Oregon"), ("PA", "Pennsylvania"), ("RI", "Rhode Island"),
    ("SC", "South Carolina"), ("SD", "South Dakota"), ("TN", "Tennessee"), ("TX", "Texas"),
    ("UT", "Utah"), ("VT", "Vermont"), ("VA", "Virginia"), ("WA", "Washington"),
    ("WV", "West Virginia"), ("WI", "Wisconsin"), ("WY", "Wyoming"),
    ("AS", "American Samoa"), ("GU", "Guam"), ("MP", "Northern Mariana Islands"),
    ("PR", "Puerto Rico"), ("VI", "U.S. Virgin Islands"),
    ("AA", "Armed Forces Americas"), ("AE", "Armed Forces Europe"), ("AP", "Armed Forces Pacific"),
]
STATE_CODES = {c for c, _ in US_STATES}
ADDRESS_FIELDS = ("street", "street2", "city", "state", "zip")

# Room name -> [(item name, item_type)]
ROOM_TEMPLATES = {
    "Kitchen": [("Toaster", "Appliance"), ("Microwave", "Appliance"),
                ("Refrigerator", "Appliance"), ("Blender", "Appliance"),
                ("Crockpot", "Appliance")],
    "Master Bedroom": [("Bed", "Furniture"), ("Dresser", "Furniture"),
                       ("Nightstand", "Furniture"), ("Nightstand", "Furniture")],
    "Living Room": [("Sofa", "Furniture"), ("Television", "Electronics"),
                    ("Bookcase", "Furniture"), ("Coffee Table", "Furniture")],
    "Dining Room": [("Dining Table", "Furniture"), ("Dining Chair", "Furniture"),
                    ("China Cabinet", "Furniture")],
    "Guest Bedroom": [("Bed", "Furniture"), ("Dresser", "Furniture"),
                      ("Nightstand", "Furniture")],
    "Master Bathroom": [("Hair Dryer", "Appliance"), ("Scale", "Appliance")],
    "Guest Bathroom": [("Hair Dryer", "Appliance")],
    "Garage": [("Lawn Mower", "Appliance"), ("Workbench", "Furniture"),
               ("Tool Set", "Appliance")],
}

DEFAULT_OLLAMA_MODEL = "qwen2.5vl:7b"
DEFAULT_CLAUDE_MODEL = "claude-haiku-5-5"
ANTHROPIC_BASE_URL = os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
MAX_CLAUDE_IMAGE = 5 * 1024 * 1024
ALLOW_PRIVATE_FETCH = False  # link price lookups refuse private/local addresses (tests flip this)

ALLOWED_HOSTS = re.compile(r"^(localhost|127\.0\.0\.1|\[::1\])(:\d+)?$", re.I)

IMAGE_TYPES = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp",
               "image/gif": "gif"}
MAX_BODY = 20 * 1024 * 1024

SCHEMA = """
CREATE TABLE IF NOT EXISTS homes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  address TEXT,
  street TEXT NOT NULL DEFAULT '',
  street2 TEXT NOT NULL DEFAULT '',
  city TEXT NOT NULL DEFAULT '',
  state TEXT NOT NULL DEFAULT '',
  zip TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS rooms (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  home_id INTEGER NOT NULL REFERENCES homes(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  inv_no TEXT UNIQUE,
  room_id INTEGER NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  brand TEXT NOT NULL DEFAULT '',
  model_number TEXT NOT NULL DEFAULT '',
  purchased_date TEXT NOT NULL DEFAULT '',
  purchased_condition TEXT NOT NULL DEFAULT '',
  purchased_value REAL NOT NULL DEFAULT 0,
  replacement_value REAL NOT NULL DEFAULT 0,
  similar_url TEXT NOT NULL DEFAULT '',
  item_type TEXT,
  image TEXT,
  similar_image TEXT,
  price_updated_at TEXT,
  scan_state TEXT NOT NULL DEFAULT '',
  scan_note TEXT NOT NULL DEFAULT '',
  source TEXT NOT NULL DEFAULT 'manual'
);
CREATE TABLE IF NOT EXISTS categories (
  name TEXT PRIMARY KEY COLLATE NOCASE
);
CREATE TABLE IF NOT EXISTS home_insurance (
  home_id INTEGER PRIMARY KEY REFERENCES homes(id) ON DELETE CASCADE,
  insurer TEXT NOT NULL DEFAULT '',
  website TEXT NOT NULL DEFAULT '',
  policy_number TEXT NOT NULL DEFAULT '',
  dwelling_coverage REAL,
  contents_coverage REAL,
  liability_coverage REAL,
  riders TEXT NOT NULL DEFAULT '[]',
  deductible REAL,
  annual_premium REAL,
  renewal_date TEXT NOT NULL DEFAULT '',
  contact TEXT NOT NULL DEFAULT '',
  notes TEXT NOT NULL DEFAULT '',
  updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_rooms_home ON rooms(home_id);
CREATE INDEX IF NOT EXISTS idx_items_room ON items(room_id);
"""


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


# ---------- storage helpers ----------

def images_dir():
    return os.path.join(DATA_DIR, "images")


def connect():
    conn = sqlite3.connect(os.path.join(DATA_DIR, "inventory.db"))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_storage():
    os.makedirs(images_dir(), exist_ok=True)
    conn = connect()
    conn.executescript(SCHEMA)
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(items)")]
    if "price_updated_at" not in cols:  # databases created before this column existed
        conn.execute("ALTER TABLE items ADD COLUMN price_updated_at TEXT")
    if "model_number" not in cols:  # items saved before the Model # field existed
        conn.execute("ALTER TABLE items ADD COLUMN model_number TEXT NOT NULL DEFAULT ''")
    for col in ("scan_state", "scan_note"):  # photo-first capture: a photo waits to be read by the AI
        if col not in cols:
            conn.execute("ALTER TABLE items ADD COLUMN %s TEXT NOT NULL DEFAULT ''" % col)
    for col in ("purchased_date", "purchased_condition"):  # added later
        if col not in cols:
            conn.execute("ALTER TABLE items ADD COLUMN %s TEXT NOT NULL DEFAULT ''" % col)
    icols = [r["name"] for r in conn.execute("PRAGMA table_info(home_insurance)")]
    for col, ddl in (("website", "TEXT NOT NULL DEFAULT ''"), ("liability_coverage", "REAL"),
                     ("riders", "TEXT NOT NULL DEFAULT '[]'")):  # insurance tables from earlier versions
        if col not in icols:
            conn.execute("ALTER TABLE home_insurance ADD COLUMN %s %s" % (col, ddl))
    hcols = [r["name"] for r in conn.execute("PRAGMA table_info(homes)")]
    for col in ADDRESS_FIELDS:  # homes created before structured US addresses
        if col not in hcols:
            conn.execute("ALTER TABLE homes ADD COLUMN %s TEXT NOT NULL DEFAULT ''" % col)
    conn.commit()
    conn.close()


def now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def rows(cur):
    return [dict(r) for r in cur.fetchall()]


def one(conn, sql, args=()):
    r = conn.execute(sql, args).fetchone()
    return dict(r) if r else None


def remove_image_files(names):
    for n in names:
        if n:
            try:
                os.remove(os.path.join(images_dir(), os.path.basename(n)))
            except OSError:
                pass


def item_images_for(conn, where, args):
    out = []
    for r in conn.execute(
            "SELECT image, similar_image FROM items WHERE " + where, args):
        out += [r["image"], r["similar_image"]]
    return out


# ---------- validation ----------

def text(body, key, required=False, default=""):
    v = body.get(key, None)
    if v is None:
        if required:
            raise ApiError(400, "%s is required" % key)
        return default
    if not isinstance(v, str):
        raise ApiError(400, "%s must be a string" % key)
    v = v.strip()
    if required and not v:
        raise ApiError(400, "%s is required" % key)
    return v


def description_field(body, current=None):
    """Description, at most DESCRIPTION_MAX characters. An older, longer description may be kept as it is."""
    v = text(body, "description")
    if len(v) > DESCRIPTION_MAX and v != current:
        raise ApiError(400, "description must be %d characters or fewer (it is %d)" % (DESCRIPTION_MAX, len(v)))
    return v


def date_field(body, key):
    """YYYY-MM-DD, not in the future; '' when empty."""
    v = text(body, key)
    if not v:
        return ""
    try:
        d = datetime.datetime.strptime(v, "%Y-%m-%d").date()
    except ValueError:
        raise ApiError(400, "%s must be a date like 2026-03-01" % key)
    if d > datetime.date.today():
        raise ApiError(400, "%s cannot be in the future" % key)
    return v


def condition_field(body):
    v = text(body, "purchased_condition")
    if v and v not in CONDITIONS:
        raise ApiError(400, "purchased_condition must be one of: " + ", ".join(CONDITIONS))
    return v


def item_out(r):
    """replacement_date is the day the replacement cost was last updated (the date part of price_updated_at)."""
    r["replacement_date"] = (r.get("price_updated_at") or "")[:10]
    return r


def model_field(body):
    v = text(body, "model_number")
    if len(v) > MODEL_MAX:
        raise ApiError(400, "model_number must be %d characters or fewer" % MODEL_MAX)
    return v


def money(body, key):
    v = body.get(key, None)
    if v is None or v == "":
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float, str)):
        raise ApiError(400, "%s must be a number" % key)
    try:
        f = float(v)
    except ValueError:
        raise ApiError(400, "%s must be a number" % key)
    if f != f or f < 0 or f == float("inf"):
        raise ApiError(400, "%s must be a non-negative number" % key)
    return round(f, 2)


def url_field(body, key):
    v = text(body, key)
    if v and not re.match(r"^https?://\S+$", v, re.I):
        raise ApiError(400, "%s must start with http:// or https://" % key)
    return v


CATEGORY_MAX = 40
MAX_CUSTOM_CATEGORIES = 50


def custom_types(conn):
    return [r["name"] for r in conn.execute("SELECT name FROM categories ORDER BY name")]


def all_types(conn):
    return ITEM_TYPES + custom_types(conn)


def check_type(conn, v):
    if v in (None, ""):
        return None
    types = all_types(conn)
    match = next((t for t in types if t.lower() == str(v).lower()), None)
    if not match:
        raise ApiError(400, "item_type must be one of: " + ", ".join(types))
    return match


def create_category(ctx):
    name = " ".join(text(ctx.body, "name", True).split())
    if len(name) > CATEGORY_MAX:
        raise ApiError(400, "Category name is limited to %d characters." % CATEGORY_MAX)
    if name.lower() in [t.lower() for t in all_types(ctx.db)] + ["other", "consumable"]:
        raise ApiError(409, "A category named '%s' already exists." % name)
    if len(custom_types(ctx.db)) >= MAX_CUSTOM_CATEGORIES:
        raise ApiError(400, "You can add up to %d custom categories." % MAX_CUSTOM_CATEGORIES)
    ctx.db.execute("INSERT INTO categories (name) VALUES (?)", (name,))
    ctx.db.commit()
    return 201, {"name": name, "item_types": all_types(ctx.db)}


def delete_category(ctx, name):
    name = unquote(str(name))
    row = one(ctx.db, "SELECT name FROM categories WHERE name = ?", (name,))
    if not row:
        raise ApiError(404, "category not found (built-in categories cannot be removed)")
    used = one(ctx.db, "SELECT COUNT(*) AS n FROM items WHERE item_type = ? COLLATE NOCASE", (row["name"],))["n"]
    if used:
        raise ApiError(409, "%d item%s still use '%s'. Change them first." %
                       (used, "" if used == 1 else "s", row["name"]))
    ctx.db.execute("DELETE FROM categories WHERE name = ?", (row["name"],))
    ctx.db.commit()
    return 200, {"deleted": row["name"], "item_types": all_types(ctx.db)}


def get_or_404(conn, table, id_):
    r = one(conn, "SELECT * FROM %s WHERE id = ?" % table, (id_,))
    if not r:
        raise ApiError(404, "%s not found" % table[:-1])
    return item_out(r) if table == "items" else r


# ---------- handlers ----------

def meta(ctx):
    return 200, {"item_types": all_types(ctx.db), "builtin_types": ITEM_TYPES,
                 "conditions": CONDITIONS, "sources": SOURCES,
                 "us_states": [{"code": c, "name": n} for c, n in US_STATES],
                 "room_templates": {k: [{"name": n, "item_type": t} for n, t in v]
                                    for k, v in ROOM_TEMPLATES.items()}}


HOME_SUMMARY = """
SELECT h.*,
  (SELECT COUNT(*) FROM rooms r WHERE r.home_id = h.id) AS room_count,
  (SELECT COUNT(*) FROM items i JOIN rooms r ON r.id = i.room_id
     WHERE r.home_id = h.id) AS item_count,
  (SELECT COALESCE(SUM(i.purchased_value), 0) FROM items i JOIN rooms r
     ON r.id = i.room_id WHERE r.home_id = h.id) AS total_purchased,
  (SELECT COALESCE(SUM(i.replacement_value), 0) FROM items i JOIN rooms r
     ON r.id = i.room_id WHERE r.home_id = h.id) AS total_replacement,
  (SELECT COUNT(*) FROM items i JOIN rooms r ON r.id = i.room_id
     WHERE r.home_id = h.id AND i.scan_state != '') AS scan_count
FROM homes h
"""

ROOM_SUMMARY = """
SELECT r.*,
  (SELECT COUNT(*) FROM items i WHERE i.room_id = r.id) AS item_count,
  (SELECT COALESCE(SUM(i.purchased_value), 0) FROM items i
     WHERE i.room_id = r.id) AS total_purchased,
  (SELECT COALESCE(SUM(i.replacement_value), 0) FROM items i
     WHERE i.room_id = r.id) AS total_replacement
FROM rooms r
"""


def format_address(h):
    """US mailing style on one line: '123 Main St, Apt 4, Springfield, IL 62704'."""
    line1 = ", ".join(x for x in (h.get("street"), h.get("street2")) if x)
    state_zip = " ".join(x for x in (h.get("state"), h.get("zip")) if x)
    line2 = ", ".join(x for x in (h.get("city"), state_zip) if x)
    return ", ".join(x for x in (line1, line2) if x)


def home_out(h):
    structured = format_address(h)
    legacy = h.get("address") or ""
    h["legacy_address"] = "" if structured else legacy  # free-text address from before US fields
    h["address"] = structured or legacy
    return h


def address_values(b, cur):
    out = {k: cur.get(k) or "" for k in ADDRESS_FIELDS}
    for k in ("street", "street2", "city"):
        if k in b:
            v = text(b, k)
            if len(v) > 120:
                raise ApiError(400, "%s is too long" % k)
            out[k] = v
    if "state" in b:
        v = text(b, "state").upper()
        if v and v not in STATE_CODES:
            raise ApiError(400, "state must be a 2-letter US state or territory code")
        out["state"] = v
    if "zip" in b:
        v = text(b, "zip")
        if v and not re.match(r"^\d{5}(-?\d{4})?$", v):
            raise ApiError(400, "zip must be 12345 or 12345-6789")
        out["zip"] = v[:5] + "-" + v[5:] if re.match(r"^\d{9}$", v) else v
    return out


def list_homes(ctx):
    homes = [home_out(r) for r in rows(ctx.db.execute(HOME_SUMMARY + " ORDER BY h.name COLLATE NOCASE"))]
    return 200, attach_insurance(ctx.db, homes)


def create_home(ctx):
    b = ctx.body
    name = text(b, "name", True)
    a = address_values(b, {})
    legacy = "" if format_address(a) else text(b, "address")
    cur = ctx.db.execute(
        "INSERT INTO homes (name, address, street, street2, city, state, zip) VALUES (?,?,?,?,?,?,?)",
        (name, legacy, a["street"], a["street2"], a["city"], a["state"], a["zip"]))
    ctx.db.commit()
    return 201, attach_insurance(ctx.db, [home_out(one(ctx.db, HOME_SUMMARY + " WHERE h.id = ?", (cur.lastrowid,)))])[0]


class Raw:
    """A non-JSON response (a file download)."""
    def __init__(self, body, ctype, filename=None):
        self.body, self.ctype, self.filename = body, ctype, filename


def report_data(ctx, home_id):
    st, home = get_home(ctx, home_id)
    return report_mod.gather(ctx.db, home_id, home)


def report_json(ctx, home_id):
    return 200, report_data(ctx, home_id)


def build_report(ctx, home_id):
    """(pdf bytes, file name) for a home. ?photos=1 adds the item photos; ?hide_purchase_price=1 leaves out what was paid."""
    data = report_data(ctx, home_id)
    photos = ctx.query.get("photos", ["0"])[0] in ("1", "true", "yes")
    show_price = ctx.query.get("hide_purchase_price", ["0"])[0] not in ("1", "true", "yes")
    pdf = report_mod.build_pdf(data, photos, images_dir(), show_price=show_price)
    slug = re.sub(r"[^A-Za-z0-9]+", "-", data["home"]["name"]).strip("-").lower() or "home"
    name = "%s-inventory%s%s-%s.pdf" % (slug, "-photos" if photos else "", "" if show_price else "-no-purchase-price",
                                      datetime.date.today().isoformat())
    return pdf, name


def report_pdf(ctx, home_id):
    pdf, name = build_report(ctx, home_id)
    return 200, Raw(pdf, "application/pdf", name)


def get_home(ctx, id_):
    r = one(ctx.db, HOME_SUMMARY + " WHERE h.id = ?", (id_,))
    if not r:
        raise ApiError(404, "home not found")
    return 200, attach_insurance(ctx.db, [home_out(r)])[0]


def update_home(ctx, id_):
    cur = get_or_404(ctx.db, "homes", id_)
    b = ctx.body
    name = text(b, "name", True) if "name" in b else cur["name"]
    a = address_values(b, cur)
    legacy = text(b, "address") if "address" in b else (cur["address"] or "")
    if format_address(a):
        legacy = ""  # a structured address replaces the old free-text one
    ctx.db.execute(
        "UPDATE homes SET name=?, address=?, street=?, street2=?, city=?, state=?, zip=? WHERE id=?",
        (name, legacy, a["street"], a["street2"], a["city"], a["state"], a["zip"], id_))
    ctx.db.commit()
    return get_home(ctx, id_)


def delete_home(ctx, id_):
    get_or_404(ctx.db, "homes", id_)
    files = item_images_for(
        ctx.db, "room_id IN (SELECT id FROM rooms WHERE home_id = ?)", (id_,))
    ctx.db.execute("DELETE FROM homes WHERE id = ?", (id_,))
    ctx.db.commit()
    remove_image_files(files)
    return 200, {"deleted": id_}


def list_rooms(ctx, home_id):
    get_or_404(ctx.db, "homes", home_id)
    return 200, rows(ctx.db.execute(
        ROOM_SUMMARY + " WHERE r.home_id = ? ORDER BY r.name COLLATE NOCASE",
        (home_id,)))


def create_room(ctx, home_id):
    get_or_404(ctx.db, "homes", home_id)
    b = ctx.body
    name = text(b, "name", True)
    cur = ctx.db.execute(
        "INSERT INTO rooms (home_id, name, description) VALUES (?, ?, ?)",
        (home_id, name, text(b, "description")))
    room_id = cur.lastrowid
    if b.get("prefill"):
        key = next((k for k in ROOM_TEMPLATES if k.lower() == name.lower()), None)
        tmpl = text(b, "template") if b.get("template") else key
        tmpl = next((k for k in ROOM_TEMPLATES
                     if tmpl and k.lower() == tmpl.lower()), None)
        if not tmpl:
            ctx.db.rollback()
            raise ApiError(400, "no room template for '%s'" % name)
        for item_name, item_type in ROOM_TEMPLATES[tmpl]:
            insert_item(ctx.db, room_id, {"name": item_name, "item_type": item_type},
                        source="template")
    ctx.db.commit()
    return 201, one(ctx.db, ROOM_SUMMARY + " WHERE r.id = ?", (room_id,))


def get_room(ctx, id_):
    r = one(ctx.db, ROOM_SUMMARY + " WHERE r.id = ?", (id_,))
    if not r:
        raise ApiError(404, "room not found")
    return 200, r


def update_room(ctx, id_):
    cur = get_or_404(ctx.db, "rooms", id_)
    b = ctx.body
    name = text(b, "name", True) if "name" in b else cur["name"]
    desc = text(b, "description") if "description" in b else cur["description"]
    home_id = cur["home_id"]
    if "home_id" in b:
        if not isinstance(b["home_id"], int) or isinstance(b["home_id"], bool):
            raise ApiError(400, "home_id must be an integer")
        get_or_404(ctx.db, "homes", b["home_id"])
        home_id = b["home_id"]
    ctx.db.execute("UPDATE rooms SET name=?, description=?, home_id=? WHERE id=?",
                   (name, desc, home_id, id_))
    ctx.db.commit()
    return get_room(ctx, id_)


def delete_room(ctx, id_):
    get_or_404(ctx.db, "rooms", id_)
    files = item_images_for(ctx.db, "room_id = ?", (id_,))
    ctx.db.execute("DELETE FROM rooms WHERE id = ?", (id_,))
    ctx.db.commit()
    remove_image_files(files)
    return 200, {"deleted": id_}


def insert_item(conn, room_id, b, source="manual"):
    purchased = money(b, "purchased_value")
    replacement = money(b, "replacement_value")
    # An explicitly entered replacement price counts as a price update.
    rdate = date_field(b, "replacement_date")      # an explicit date wins over "now"
    stamp = (rdate + "T00:00:00Z") if rdate else (now() if (replacement is not None or b.get("price_confirmed") is True) else None)
    purchased = purchased if purchased is not None else 0
    replacement = replacement if replacement is not None else purchased
    src = b.get("source", source)
    if src not in SOURCES:
        raise ApiError(400, "source must be one of: " + ", ".join(SOURCES))
    scan_state = text(b, "scan_state")
    if scan_state not in ("", "pending"):
        raise ApiError(400, "scan_state must be empty or pending")
    cur = conn.execute(
        """INSERT INTO items (room_id, name, description, brand, model_number, purchased_date,
           purchased_condition, purchased_value, replacement_value, similar_url, item_type, source,
           price_updated_at, scan_state) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (room_id, text(b, "name", True), description_field(b), text(b, "brand"), model_field(b),
         date_field(b, "purchased_date"), condition_field(b), purchased, replacement, url_field(b, "similar_url"),
         check_type(conn, b.get("item_type")), src, stamp, scan_state))
    conn.execute("UPDATE items SET inv_no = ? WHERE id = ?",
                 ("INV-%06d" % cur.lastrowid, cur.lastrowid))
    return cur.lastrowid


def list_items(ctx, room_id):
    get_or_404(ctx.db, "rooms", room_id)
    return 200, [item_out(r) for r in rows(ctx.db.execute(
        "SELECT * FROM items WHERE room_id = ? ORDER BY name COLLATE NOCASE, id",
        (room_id,)))]


def create_item(ctx, room_id):
    get_or_404(ctx.db, "rooms", room_id)
    new_id = insert_item(ctx.db, room_id, ctx.body)
    ctx.db.commit()
    return 201, get_or_404(ctx.db, "items", new_id)


def get_item(ctx, id_):
    return 200, get_or_404(ctx.db, "items", id_)


def update_item(ctx, id_):
    cur = get_or_404(ctx.db, "items", id_)
    b = ctx.body
    f = dict(cur)
    for k in ("name",):
        if k in b:
            f[k] = text(b, k, True)
    if "brand" in b:
        f["brand"] = text(b, "brand")
    if "description" in b:
        f["description"] = description_field(b, cur["description"])
    if "model_number" in b:
        f["model_number"] = model_field(b)
    if "purchased_date" in b:
        f["purchased_date"] = date_field(b, "purchased_date")
    if "purchased_condition" in b:
        f["purchased_condition"] = condition_field(b)
    if "similar_url" in b:
        f["similar_url"] = url_field(b, "similar_url")
    if "item_type" in b:
        f["item_type"] = check_type(ctx.db, b["item_type"])
    if "purchased_value" in b:
        v = money(b, "purchased_value")
        f["purchased_value"] = v if v is not None else 0
    if "replacement_value" in b:
        v = money(b, "replacement_value")
        new = v if v is not None else f["purchased_value"]
        if new != cur["replacement_value"]:
            f["price_updated_at"] = now()
        f["replacement_value"] = new
    if b.get("price_confirmed") is True:
        f["price_updated_at"] = now()
    if "replacement_date" in b:
        rd = date_field(b, "replacement_date")      # set by hand; empty leaves it alone
        if rd and rd != (f["price_updated_at"] or "")[:10]:
            f["price_updated_at"] = rd + "T00:00:00Z"
    if "scan_state" in b:        # "" = confirmed, "pending" = read it again
        st = text(b, "scan_state")
        if st not in ("", "pending"):
            raise ApiError(400, "scan_state must be empty or pending")
        f["scan_state"], f["scan_note"] = st, ""
    if "source" in b:
        if b["source"] not in SOURCES:
            raise ApiError(400, "invalid source")
        f["source"] = b["source"]
    if "room_id" in b:
        if not isinstance(b["room_id"], int) or isinstance(b["room_id"], bool):
            raise ApiError(400, "room_id must be an integer")
        get_or_404(ctx.db, "rooms", b["room_id"])
        f["room_id"] = b["room_id"]
    ctx.db.execute(
        """UPDATE items SET name=?, description=?, brand=?, model_number=?, purchased_date=?,
           purchased_condition=?, purchased_value=?, replacement_value=?, similar_url=?, item_type=?,
           source=?, room_id=?, price_updated_at=?, scan_state=?, scan_note=? WHERE id=?""",
        (f["name"], f["description"], f["brand"], f["model_number"], f["purchased_date"],
         f["purchased_condition"], f["purchased_value"],
         f["replacement_value"], f["similar_url"], f["item_type"], f["source"],
         f["room_id"], f["price_updated_at"], f["scan_state"], f["scan_note"], id_))
    ctx.db.commit()
    return 200, get_or_404(ctx.db, "items", id_)


def delete_item(ctx, id_):
    cur = get_or_404(ctx.db, "items", id_)
    ctx.db.execute("DELETE FROM items WHERE id = ?", (id_,))
    ctx.db.commit()
    remove_image_files([cur["image"], cur["similar_image"]])
    return 200, {"deleted": id_}


def upload_image(ctx, id_):
    cur = get_or_404(ctx.db, "items", id_)
    kind = ctx.query.get("kind", ["photo"])[0]
    if kind not in ("photo", "similar"):
        raise ApiError(400, "kind must be photo or similar")
    data = ctx.body.get("data")
    m = re.match(r"^data:([\w/+.-]+);base64,(.*)$", data or "", re.S)
    if not m or m.group(1) not in IMAGE_TYPES:
        raise ApiError(400, "data must be a base64 data URL (png, jpeg, webp, gif)")
    try:
        raw = base64.b64decode(m.group(2), validate=True)
    except ValueError:
        raise ApiError(400, "invalid base64 data")
    if not raw:
        raise ApiError(400, "empty image")
    store_item_image(ctx.db, cur, kind, raw, IMAGE_TYPES[m.group(1)])
    return 200, get_or_404(ctx.db, "items", id_)


def store_item_image(conn, cur, kind, raw, ext):
    col = "image" if kind == "photo" else "similar_image"
    fname = "%d-%s-%s.%s" % (cur["id"], kind, uuid.uuid4().hex[:8], ext)
    with open(os.path.join(images_dir(), fname), "wb") as fh:
        fh.write(raw)
    conn.execute("UPDATE items SET %s = ? WHERE id = ?" % col, (fname, cur["id"]))
    conn.commit()
    remove_image_files([cur[col]])


def link_image(ctx, id_):
    """Pull the product picture from the item's similar-item link and keep it as the equivalent-item image."""
    cur = get_or_404(ctx.db, "items", id_)
    url = text(ctx.body, "url") or cur["similar_url"]
    if not url:
        raise ApiError(400, "This item has no link to take a picture from.")
    try:
        final, doc = linkinfo.fetch_page(url, allow_private=ALLOW_PRIVATE_FETCH)
        pic = linkinfo.extract_image(doc, final)
        if not pic:
            raise ApiError(404, "That page does not name a product picture.")
        raw, mime, ext = linkinfo.fetch_image(pic, allow_private=ALLOW_PRIVATE_FETCH)
    except linkinfo.LinkError as e:
        raise ApiError(e.status, e.message)
    store_item_image(ctx.db, cur, "similar", raw, ext)
    return 200, get_or_404(ctx.db, "items", id_)


MAX_RIDERS = 5
INS_TEXT = ["insurer", "policy_number", "contact", "notes"]
INS_MONEY = ["dwelling_coverage", "contents_coverage", "liability_coverage", "deductible",
             "annual_premium"]
INS_DEFAULTS = dict({k: "" for k in INS_TEXT}, website="", renewal_date="", riders=[],
                    **{k: None for k in INS_MONEY})


def parse_riders(b):
    v = b.get("riders")
    if not isinstance(v, list):
        raise ApiError(400, "riders must be a list")
    if len(v) > MAX_RIDERS:
        raise ApiError(400, "At most %d riders are allowed" % MAX_RIDERS)
    out = []
    for r in v:
        if not isinstance(r, dict):
            raise ApiError(400, "each rider must be an object with a name and limit")
        name = text(r, "name")
        if not name:
            raise ApiError(400, "Each rider needs a name")
        if len(name) > 80:
            raise ApiError(400, "rider name is too long")
        out.append({"name": name, "limit": money(r, "limit")})
    return out


def ins_out(row, home_id):
    """Insurance as the API shows it; a home with no record gets the empty defaults."""
    if not row:
        return dict(INS_DEFAULTS, riders=[], home_id=home_id, configured=False, updated_at=None)
    r = dict(row)
    r["configured"] = True
    try:
        riders = json.loads(r.get("riders") or "[]")
    except ValueError:
        riders = []
    r["riders"] = riders if isinstance(riders, list) else []
    return r


def attach_insurance(conn, homes):
    by_home = {r["home_id"]: r for r in conn.execute("SELECT * FROM home_insurance")}
    for h in homes:
        h["insurance"] = ins_out(by_home.get(h["id"]), h["id"])
    return homes


def get_insurance(ctx, home_id):
    get_or_404(ctx.db, "homes", home_id)
    return 200, ins_out(one(ctx.db, "SELECT * FROM home_insurance WHERE home_id = ?", (home_id,)), home_id)


def put_insurance(ctx, home_id):
    get_or_404(ctx.db, "homes", home_id)
    b = ctx.body
    cur = ins_out(one(ctx.db, "SELECT * FROM home_insurance WHERE home_id = ?", (home_id,)), home_id)
    for k in INS_TEXT:
        if k in b:
            cur[k] = text(b, k)
    if "website" in b:
        cur["website"] = url_field(b, "website")
    for k in INS_MONEY:
        if k in b:
            cur[k] = money(b, k)
    if "riders" in b:
        cur["riders"] = parse_riders(b)
    if "renewal_date" in b:
        d = text(b, "renewal_date")
        if d:
            try:
                datetime.datetime.strptime(d, "%Y-%m-%d")
            except ValueError:
                raise ApiError(400, "renewal_date must be YYYY-MM-DD")
        cur["renewal_date"] = d
    ctx.db.execute(
        """INSERT OR REPLACE INTO home_insurance (home_id, insurer, website, policy_number,
           dwelling_coverage, contents_coverage, liability_coverage, riders, deductible,
           annual_premium, renewal_date, contact, notes, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (home_id, cur["insurer"], cur["website"], cur["policy_number"], cur["dwelling_coverage"],
         cur["contents_coverage"], cur["liability_coverage"], json.dumps(cur["riders"]),
         cur["deductible"], cur["annual_premium"], cur["renewal_date"], cur["contact"],
         cur["notes"], now()))
    ctx.db.commit()
    return get_insurance(ctx, home_id)


def delete_insurance(ctx, home_id):
    get_or_404(ctx.db, "homes", home_id)
    ctx.db.execute("DELETE FROM home_insurance WHERE home_id = ?", (home_id,))
    ctx.db.commit()
    return 200, {"deleted": home_id}


# ---------- settings (never returns the API key) ----------

def default_ollama_url():
    u = os.environ.get("OLLAMA_HOST", "http://localhost:11434").strip()
    return u if re.match(r"^https?://", u, re.I) else "http://" + u


def load_settings():
    try:
        with open(os.path.join(DATA_DIR, "settings.json")) as fh:
            raw = json.load(fh)
    except (OSError, ValueError):
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    key = raw.get("anthropic_key") or ""
    source = "settings" if key else None
    if not key and os.environ.get("ANTHROPIC_API_KEY"):
        key, source = os.environ["ANTHROPIC_API_KEY"].strip(), "env"
    return {"ollama_url": raw.get("ollama_url") or default_ollama_url(),
            "ollama_model": raw.get("ollama_model") or DEFAULT_OLLAMA_MODEL,
            "anthropic_model": raw.get("anthropic_model") or DEFAULT_CLAUDE_MODEL,
            "anthropic_key": key, "key_source": source, "_raw": raw}


def save_settings(raw):
    path = os.path.join(DATA_DIR, "settings.json")
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)  # key file: owner only
    with os.fdopen(fd, "w") as fh:
        json.dump(raw, fh)
    os.replace(tmp, path)


def public_settings():
    c = load_settings()
    return {"ollama": {"url": c["ollama_url"], "model": c["ollama_model"]},
            "anthropic": {"configured": bool(c["anthropic_key"]), "key_source": c["key_source"],
                          "model": c["anthropic_model"]},
            "google": google_status(c["_raw"]),
            "defaults": {"ollama_url": default_ollama_url(), "ollama_model": DEFAULT_OLLAMA_MODEL,
                         "anthropic_model": DEFAULT_CLAUDE_MODEL}}


def google_status(raw):
    return {"client_id": raw.get("google_client_id", ""), "secret_configured": bool(raw.get("google_client_secret")),
            "connected": bool(raw.get("google_refresh_token"))}


def get_settings(ctx):
    return 200, public_settings()


def put_settings(ctx):
    b = ctx.body
    raw = load_settings()["_raw"]
    if "ollama_url" in b:
        u = text(b, "ollama_url").rstrip("/")
        if u and not re.match(r"^https?://[^\s/]+$", u, re.I):
            raise ApiError(400, "ollama_url must look like http://localhost:11434")
        raw["ollama_url"] = u
    for k in ("ollama_model", "anthropic_model"):
        if k in b:
            v = text(b, k)
            if len(v) > 100 or re.search(r"\s", v):
                raise ApiError(400, "%s is not a valid model name" % k)
            raw[k] = v
    if "anthropic_key" in b:
        v = text(b, "anthropic_key")
        if v and (len(v) < 20 or re.search(r"\s", v)):
            raise ApiError(400, "That does not look like an API key")
        if v:
            raw["anthropic_key"] = v
        else:
            raw.pop("anthropic_key", None)
    changed = False
    if "google_client_id" in b:
        v = text(b, "google_client_id")
        if v and (len(v) > 200 or re.search(r"\s", v)):
            raise ApiError(400, "That does not look like a Google client id")
        changed |= v != raw.get("google_client_id", "")
        raw["google_client_id"] = v
    if "google_client_secret" in b:
        v = text(b, "google_client_secret")
        if v and (len(v) > 200 or re.search(r"\s", v)):
            raise ApiError(400, "That does not look like a Google client secret")
        if v:
            changed |= v != raw.get("google_client_secret", "")
            raw["google_client_secret"] = v
    if changed:    # a different client means the old sign-in no longer applies
        raw.pop("google_refresh_token", None)
        raw.pop("google_folder_id", None)
    save_settings(raw)
    return 200, public_settings()


def google_auth_url(ctx):
    raw = load_settings()["_raw"]
    if not raw.get("google_client_id") or not raw.get("google_client_secret"):
        raise ApiError(400, "Add your Google client id and secret first.")
    return 200, {"url": gdrive.start_auth(raw["google_client_id"], "http://%s/oauth/google" % ctx.host)}


def google_disconnect(ctx):
    raw = load_settings()["_raw"]
    raw.pop("google_refresh_token", None)
    raw.pop("google_folder_id", None)
    save_settings(raw)
    return 200, public_settings()


def google_callback_page(query, host):
    """The page Google sends the browser back to. Returns (status, html)."""
    def page(title, msg, status=200):
        return status, ("<!doctype html><meta charset=utf-8><title>%s</title><body style='font:16px system-ui;max-width:34em;margin:4em auto;padding:0 1em'>"
                        "<h2>%s</h2><p>%s</p><p><a href='/#/settings'>Back to Settings</a></p></body>" % (title, title, msg))
    if query.get("error"):
        return page("Google Drive not connected", "Google said: %s." % re.sub(r"[^\w .-]", "", query["error"][0])[:80], 400)
    pend = gdrive.take_pending((query.get("state") or [""])[0])
    code = (query.get("code") or [""])[0]
    if not pend or not code:
        return page("Google Drive not connected", "This sign-in link is not valid or has expired. Start again from Settings.", 400)
    raw = load_settings()["_raw"]
    try:
        raw["google_refresh_token"] = gdrive.exchange_code(raw.get("google_client_id", ""), raw.get("google_client_secret", ""),
                                                           code, pend[0], pend[1])
    except gdrive.DriveError as e:
        return page("Google Drive not connected", e.message.replace("<", "&lt;"), 400)
    raw.pop("google_folder_id", None)
    save_settings(raw)
    return page("Google Drive connected", "You can close this tab and save reports to your Drive from the Report page.")


def report_to_drive(ctx, home_id):
    raw = load_settings()["_raw"]
    if not raw.get("google_refresh_token"):
        raise ApiError(400, "Google Drive is not connected. Connect it in Settings.")
    pdf, name = build_report(ctx, home_id)
    try:
        token = gdrive.access_token(raw["google_client_id"], raw["google_client_secret"], raw["google_refresh_token"])
        folder = gdrive.find_or_make_folder(token)
        out = gdrive.upload(token, name, pdf, "application/pdf", folder)
    except gdrive.DriveError as e:
        raise ApiError(e.status if e.status in (400, 401, 403) else 502, e.message)
    return 200, dict(out, folder=gdrive.FOLDER_NAME)


# ---------- photo identification (Ollama local, Claude bring-your-own-key) ----------

class ProviderError(Exception):
    def __init__(self, status, detail="", timeout=False):
        super().__init__(detail)
        self.status, self.detail, self.timeout = status, detail, timeout


_LOCAL = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # never proxy local calls


def http_json(method, url, payload=None, headers=None, timeout=60, local=False):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers=dict({"Content-Type": "application/json"}, **(headers or {})))
    opener = _LOCAL if local else urllib.request.build_opener()
    try:
        with opener.open(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise ProviderError(e.code, e.read()[:2000].decode("utf-8", "replace"))
    except (socket.timeout, TimeoutError):
        raise ProviderError(None, "timed out", timeout=True)
    except (urllib.error.URLError, OSError, ValueError) as e:
        if isinstance(getattr(e, "reason", None), (socket.timeout, TimeoutError)):
            raise ProviderError(None, "timed out", timeout=True)
        raise ProviderError(None, str(e))


def item_schema(types):
    return {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "brand": {"type": "string"},
            "model_number": {"type": "string"},
            "item_type": {"type": "string", "enum": types + ["Other"]},
            "description": {"type": "string"},
            "search_query": {"type": "string"},
        },
        "required": ["name", "brand", "model_number", "item_type", "description", "search_query"],
    }


def item_prompt(types):
    return PROMPT_HEAD + "- item_type: one of " + ", ".join(types) + ", or Other.\n" + PROMPT_TAIL


PROMPT_HEAD = (
    "You are cataloging household belongings for a home inventory. Look at the photo and "
    "identify the single main item. Answer with the requested fields only.\n"
    "- name: short common name, e.g. 'Stand Mixer'.\n"
    "- brand: the manufacturer only if a logo or text shows it or you are certain; otherwise ''.\n"
    "- model_number: the model number only if it is printed on the item or its label; otherwise ''.\n"
    )

PROMPT_TAIL = (
    "- description: one factual sentence of at most %d characters (colour, material, size). "
    "Do not guess details you cannot see.\n" % DESCRIPTION_MAX +
    "- search_query: a web search query that would find a comparable replacement.")


def clean_str(v, limit):
    return v.strip()[:limit] if isinstance(v, str) else ""


def parse_item(content, types=None):
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except ValueError:
            m = re.search(r"\{.*\}", content, re.S)  # tolerate prose around the JSON
            try:
                content = json.loads(m.group(0)) if m else None
            except ValueError:
                content = None
    if not isinstance(content, dict) or not clean_str(content.get("name"), 120):
        raise ApiError(502, "The model did not return a usable item. Try again or use a clearer photo.")
    t = clean_str(content.get("item_type"), 40)
    t = next((x for x in (types or ITEM_TYPES) if x.lower() == t.lower()), None)
    return {"name": clean_str(content["name"], 120), "brand": clean_str(content.get("brand"), 80),
            "model_number": clean_str(content.get("model_number"), MODEL_MAX),
            "item_type": t, "description": clean_str(content.get("description"), DESCRIPTION_MAX),
            "search_query": clean_str(content.get("search_query"), 200)}


def make_provider(cfg, provider):
    """The reusable receipt_module providers, configured from this app's settings."""
    if provider == "anthropic":
        return rm.AnthropicProvider(cfg["anthropic_key"], cfg["anthropic_model"], ANTHROPIC_BASE_URL)
    return rm.OllamaProvider(cfg["ollama_url"], cfg["ollama_model"])


def provider_call(fn):
    try:
        return fn()
    except rm.ProviderError as e:
        msg = e.message
        if e.kind == "auth":
            msg += " Check it in Settings."
        elif e.kind == "model_missing" and "Anthropic" in msg:
            msg += " Check the model name in Settings."
        raise ApiError(502, msg)
    except rm.ReceiptError as e:
        raise ApiError(400 if e.kind == "bad_image" else 502, e.message)


def read_request_image(b):
    m = re.match(r"^data:([\w/+.-]+);base64,(.*)$", b.get("data") or "", re.S)
    if not m or m.group(1) not in IMAGE_TYPES:
        raise ApiError(400, "data must be a base64 data URL (png, jpeg, webp, gif)")
    try:
        raw = base64.b64decode(m.group(2), validate=True)
    except ValueError:
        raise ApiError(400, "invalid base64 data")
    if not raw:
        raise ApiError(400, "empty image")
    return m.group(1), m.group(2), len(raw)


def request_provider(b, raw_len):
    """Validated provider choice for a request: (name, module provider, model name)."""
    provider = b.get("provider", "ollama")
    if provider not in ("ollama", "anthropic"):
        raise ApiError(400, "provider must be ollama or anthropic")
    cfg = load_settings()
    if provider == "anthropic":
        if not cfg["anthropic_key"]:
            raise ApiError(400, "Claude is not set up. Add your API key in Settings.")
        if raw_len > MAX_CLAUDE_IMAGE:
            raise ApiError(400, "Image is over Claude's 5 MB limit. Use a smaller photo.")
    model = cfg["anthropic_model"] if provider == "anthropic" else cfg["ollama_model"]
    return provider, make_provider(cfg, provider), model


def identify(ctx):
    b = ctx.body
    provider = b.get("provider", "ollama")
    if provider not in ("ollama", "anthropic"):
        raise ApiError(400, "provider must be ollama or anthropic")
    media, b64, n = read_request_image(b)
    provider, prov, model = request_provider(b, n)
    types = all_types(ctx.db)
    result = parse_item(provider_call(lambda: prov.chat_json(item_prompt(types), item_schema(types), b64, media)), types)
    result.update(model=model, provider=provider)
    return 200, result


def classify(ctx):
    """Is this photo a receipt, an item, or something else?"""
    media, b64, n = read_request_image(ctx.body)
    provider, prov, model = request_provider(ctx.body, n)
    kind = provider_call(lambda: rm.classify_image(prov, ctx.body["data"]))
    return 200, {"kind": kind, "provider": provider, "model": model}


def receipt(ctx):
    """Read the line items of a receipt photo (see receipt_module/)."""
    media, b64, n = read_request_image(ctx.body)
    provider, prov, model = request_provider(ctx.body, n)
    out = provider_call(lambda: rm.extract_receipt(prov, ctx.body["data"], all_types(ctx.db) + ["Consumable", "Other"]))
    out.update(provider=provider, model=model)
    return 200, out


def scan_queue(ctx):
    """Every item whose photo is waiting to be read or whose reading needs a look, newest last."""
    out = rows(ctx.db.execute(
        """SELECT i.*, r.name AS room_name, r.home_id AS home_id, h.name AS home_name FROM items i
           JOIN rooms r ON r.id = i.room_id JOIN homes h ON h.id = r.home_id
           WHERE i.scan_state != '' ORDER BY i.id"""))
    return 200, [item_out(r) for r in out]


def image_data_url(name):
    ext = name.rsplit(".", 1)[-1].lower()
    mime = next((m for m, e in IMAGE_TYPES.items() if e == ext), None)
    try:
        with open(os.path.join(images_dir(), os.path.basename(name)), "rb") as fh:
            raw = fh.read()
    except OSError:
        raise ApiError(404, "The photo file is missing.")
    if not mime:
        raise ApiError(400, "Unsupported photo type.")
    return "data:%s;base64,%s" % (mime, base64.b64encode(raw).decode())


MAX_RECEIPT_COPIES = 20


def scan_item(ctx, id_):
    """Read the item's photo with the AI later on and reclassify it: an object fills in its details,
    a receipt becomes one item per purchased line, anything else is flagged."""
    cur = get_or_404(ctx.db, "items", id_)
    if not cur["image"]:
        raise ApiError(400, "This item has no photo to read.")
    provider = ctx.body.get("provider", "ollama")
    sub = Ctx()
    sub.db, sub.query, sub.host = ctx.db, {}, getattr(ctx, "host", "localhost")
    sub.body = {"data": image_data_url(cur["image"]), "provider": provider}
    kind = classify(sub)[1]["kind"]
    types = all_types(ctx.db)
    out = {"kind": kind, "item": None, "created": [], "deleted": None}
    if kind == "item":
        r = identify(sub)[1]
        ctx.db.execute(
            """UPDATE items SET name=?, brand=?, model_number=?, item_type=COALESCE(?, item_type), description=?,
               source='vision', scan_state='review', scan_note=? WHERE id=?""",
            (r["name"], r["brand"], r["model_number"], r["item_type"], r["description"],
             "Identified by %s. Check the details." % r["model"], id_))
        ctx.db.commit()
        out["item"] = get_or_404(ctx.db, "items", id_)
    elif kind == "receipt":
        rec = receipt(sub)[1]
        if not rec["items"]:
            ctx.db.execute("UPDATE items SET scan_state='failed', scan_note=? WHERE id=?",
                           ("No line items could be read from this receipt.", id_))
            ctx.db.commit()
            out["item"] = get_or_404(ctx.db, "items", id_)
            return 200, out
        bought = rec["date"] if rec["date"] and rec["date"] <= datetime.date.today().isoformat() else ""
        note = "From a receipt%s." % (" at " + rec["store"] if rec["store"] else "")
        first = True
        for ln in rec["items"]:
            unit = ln["unit_price"] if ln["unit_price"] is not None else (ln["total_price"] or 0)
            for _ in range(min(ln["quantity"], MAX_RECEIPT_COPIES)):
                nid = insert_item(ctx.db, cur["room_id"], {
                    "name": ln["description"][:120], "purchased_value": unit, "purchased_date": bought,
                    "item_type": ln["category"] if ln["category"] in types else "", "source": "vision"})
                ctx.db.execute("UPDATE items SET scan_state='review', scan_note=? WHERE id=?", (note, nid))
                if first:       # the receipt photo goes with the first line
                    ctx.db.execute("UPDATE items SET image=? WHERE id=?", (cur["image"], nid))
                    first = False
                out["created"].append(nid)
        ctx.db.execute("UPDATE items SET image = NULL WHERE id = ?", (id_,))
        ctx.db.execute("DELETE FROM items WHERE id = ?", (id_,))
        ctx.db.commit()
        out["deleted"] = id_
    else:
        ctx.db.execute("UPDATE items SET scan_state='failed', scan_note=? WHERE id=?",
                       ("This photo is not an item or a receipt.", id_))
        ctx.db.commit()
        out["item"] = get_or_404(ctx.db, "items", id_)
    return 200, out


def link_price(ctx):
    """Fetch a pasted product link and report the price the page publishes."""
    url = text(ctx.body, "url", True)
    try:
        return 200, linkinfo.link_info(url, allow_private=ALLOW_PRIVATE_FETCH)
    except linkinfo.LinkError as e:
        raise ApiError(e.status, e.message)


def ollama_models(ctx):
    """What `ollama list` shows, read from Ollama's local API (no shell command): installed models,
    their size, and whether each can read images."""
    c = load_settings()
    base = c["ollama_url"].rstrip("/")
    out = {"url": base, "reachable": False, "current": c["ollama_model"], "models": []}
    try:
        tags = http_json("GET", base + "/api/tags", timeout=3, local=True)
    except ProviderError:
        return 200, out
    out["reachable"] = True
    for m in (tags.get("models") or [])[:40]:
        name = m.get("name") or m.get("model") if isinstance(m, dict) else None
        if not name:
            continue
        d = m.get("details") or {}
        entry = {"name": name, "size": m.get("size"), "parameter_size": d.get("parameter_size") or "",
                 "quantization": d.get("quantization_level") or "", "family": d.get("family") or "", "vision": None}
        try:
            info = http_json("POST", base + "/api/show", {"model": name, "name": name}, timeout=3, local=True)
            caps = info.get("capabilities")
            if isinstance(caps, list):                       # current Ollama states it outright
                entry["vision"] = "vision" in caps
            elif (set(d.get("families") or []) & {"clip", "mllama"}
                  or any(k.startswith("clip.") or ".vision." in k for k in (info.get("model_info") or {}))):
                entry["vision"] = True                       # older Ollama: infer from the model's parts
        except ProviderError:
            pass
        out["models"].append(entry)
    out["models"].sort(key=lambda e: (e["vision"] is not True, e["name"]))
    return 200, out


def providers_status(ctx):
    c = load_settings()
    st = {"ollama": {"url": c["ollama_url"], "model": c["ollama_model"], "reachable": False,
                     "model_installed": False, "models": []},
          "anthropic": {"configured": bool(c["anthropic_key"]), "model": c["anthropic_model"]}}
    try:
        tags = http_json("GET", c["ollama_url"].rstrip("/") + "/api/tags", timeout=3, local=True)
        names = [x.get("name") for x in tags.get("models", []) if isinstance(x, dict)]
        st["ollama"].update(reachable=True, models=names,
                            model_installed=c["ollama_model"] in names or c["ollama_model"] + ":latest" in names)
    except ProviderError:
        pass
    return 200, st


ID = r"(\d+)"
ROUTES = [
    ("GET", r"/meta", meta),
    ("POST", r"/categories", create_category),
    ("DELETE", r"/categories/([^/]+)", delete_category),
    ("GET", r"/homes", list_homes),
    ("POST", r"/homes", create_home),
    ("GET", r"/homes/%s" % ID, get_home),
    ("PUT", r"/homes/%s" % ID, update_home),
    ("DELETE", r"/homes/%s" % ID, delete_home),
    ("GET", r"/homes/%s/report" % ID, report_json),
    ("GET", r"/homes/%s/report\.pdf" % ID, report_pdf),
    ("GET", r"/homes/%s/rooms" % ID, list_rooms),
    ("POST", r"/homes/%s/rooms" % ID, create_room),
    ("GET", r"/homes/%s/insurance" % ID, get_insurance),
    ("PUT", r"/homes/%s/insurance" % ID, put_insurance),
    ("DELETE", r"/homes/%s/insurance" % ID, delete_insurance),
    ("GET", r"/rooms/%s" % ID, get_room),
    ("PUT", r"/rooms/%s" % ID, update_room),
    ("DELETE", r"/rooms/%s" % ID, delete_room),
    ("GET", r"/rooms/%s/items" % ID, list_items),
    ("POST", r"/rooms/%s/items" % ID, create_item),
    ("GET", r"/items/%s" % ID, get_item),
    ("PUT", r"/items/%s" % ID, update_item),
    ("DELETE", r"/items/%s" % ID, delete_item),
    ("POST", r"/items/%s/image" % ID, upload_image),
    ("POST", r"/items/%s/link-image" % ID, link_image),
    ("POST", r"/items/%s/scan" % ID, scan_item),
    ("GET", r"/scan-queue", scan_queue),
    ("POST", r"/identify", identify),
    ("POST", r"/classify", classify),
    ("POST", r"/receipt", receipt),
    ("POST", r"/link-info", link_price),
    ("POST", r"/homes/%s/report/drive" % ID, report_to_drive),
    ("GET", r"/google/auth-url", google_auth_url),
    ("DELETE", r"/google", google_disconnect),
    ("GET", r"/settings", get_settings),
    ("PUT", r"/settings", put_settings),
    ("GET", r"/providers/status", providers_status),
    ("GET", r"/providers/ollama-models", ollama_models),
]
ROUTES = [(m, re.compile("^" + p + "$"), fn) for m, p, fn in ROUTES]


class Ctx:
    pass


class Handler(BaseHTTPRequestHandler):
    server_version = "InMyHome/1.0"

    def log_message(self, fmt, *args):
        if os.environ.get("INVENTORY_QUIET") != "1":
            sys.stderr.write("%s %s\n" % (self.command, self.path))

    def send_json(self, status, obj):
        payload = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def read_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            raise ApiError(413, "request body too large")
        if not length:
            return {}
        try:
            body = json.loads(self.rfile.read(length))
        except (ValueError, UnicodeDecodeError):
            raise ApiError(400, "invalid JSON")
        if not isinstance(body, dict):
            raise ApiError(400, "JSON body must be an object")
        return body

    def handle_api(self, method, parsed):
        # Blocks cross-site writes (a page cannot send application/json without a
        # preflight, which this server never grants).
        if method in ("POST", "PUT") and int(self.headers.get("Content-Length") or 0) > 0:
            ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if ctype != "application/json":
                raise ApiError(415, "Content-Type must be application/json")
        path = parsed.path[4:] or "/"
        if len(path) > 1:
            path = path.rstrip("/")
        matched = False
        for m, rx, fn in ROUTES:
            mt = rx.match(path)
            if not mt:
                continue
            matched = True
            if m != method:
                continue
            ctx = Ctx()
            ctx.host = self.headers.get("Host") or "localhost"
            ctx.query = parse_qs(parsed.query)
            ctx.body = self.read_body() if method in ("POST", "PUT") else {}
            ctx.db = connect()
            try:
                status, obj = fn(ctx, *[int(g) if g.isdigit() else g for g in mt.groups()]) \
                    if mt.groups() else fn(ctx)
            finally:
                ctx.db.close()
            if isinstance(obj, Raw):
                self.send_response(status)
                self.send_header("Content-Type", obj.ctype)
                if obj.filename:
                    self.send_header("Content-Disposition", 'attachment; filename="%s"' % obj.filename)
                self.send_header("Content-Length", str(len(obj.body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(obj.body)
                return
            return self.send_json(status, obj)
        raise ApiError(405 if matched else 404, "method not allowed" if matched else "not found")

    def serve_file(self, base, rel, types):
        full = os.path.realpath(os.path.join(base, rel))
        if not full.startswith(os.path.realpath(base) + os.sep) or not os.path.isfile(full):
            raise ApiError(404, "not found")
        ext = full.rsplit(".", 1)[-1].lower()
        ctype = types.get(ext, "application/octet-stream")
        with open(full, "rb") as fh:
            data = fh.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def dispatch(self, method):
        parsed = urlparse(self.path)
        try:
            extra = [h.strip().lower() for h in os.environ.get("INVENTORY_ALLOWED_HOSTS", "").split(",") if h.strip()]
            host = self.headers.get("Host") or ""
            if not ALLOWED_HOSTS.match(host) and host.rsplit(":", 1)[0].lower() not in extra:
                raise ApiError(403, "Forbidden host")  # defeats DNS-rebinding attacks
            if parsed.path == "/api" or parsed.path.startswith("/api/"):
                return self.handle_api(method, parsed)
            if method != "GET":
                raise ApiError(405, "method not allowed")
            if parsed.path == "/oauth/google":
                status, page = google_callback_page(parse_qs(parsed.query), host)
                data = page.encode()
                self.send_response(status)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)
                return
            if parsed.path.startswith("/images/"):
                return self.serve_file(images_dir(), parsed.path[8:], {
                    "png": "image/png", "jpg": "image/jpeg", "webp": "image/webp",
                    "gif": "image/gif"})
            rel = "index.html" if parsed.path == "/" else parsed.path.lstrip("/")
            return self.serve_file(STATIC_DIR, rel, {
                "html": "text/html; charset=utf-8", "js": "text/javascript",
                "css": "text/css", "json": "application/json", "svg": "image/svg+xml",
                "png": "image/png", "webmanifest": "application/manifest+json"})
        except ApiError as e:
            self.send_json(e.status, {"error": e.message})
        except Exception as e:  # keep the server alive on bugs
            sys.stderr.write("error: %r\n" % (e,))
            self.send_json(500, {"error": "internal server error"})

    def do_GET(self): self.dispatch("GET")
    def do_POST(self): self.dispatch("POST")
    def do_PUT(self): self.dispatch("PUT")
    def do_DELETE(self): self.dispatch("DELETE")


def make_server(host="127.0.0.1", port=8000, data_dir=None):
    global DATA_DIR
    if data_dir:
        DATA_DIR = data_dir
    init_storage()
    return ThreadingHTTPServer((host, port), Handler)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    srv = make_server(port=port)  # bound to localhost only; add auth before changing
    print("In My Home running at http://localhost:%d  (data: %s)" % (port, DATA_DIR))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")
