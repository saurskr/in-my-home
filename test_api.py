#!/usr/bin/env python3
"""API tests: python3 test_api.py (starts a throwaway server on a free port)."""
import base64
import http.client
import http.server
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

os.environ["INVENTORY_QUIET"] = "1"
import server

PNG = "data:image/png;base64," + base64.b64encode(
    b"\x89PNG\r\n\x1a\n" + b"0" * 20).decode()


class ApiBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.srv = server.make_server(port=0, data_dir=cls.tmp.name)
        cls.base = "http://127.0.0.1:%d" % cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        cls.tmp.cleanup()

    def call(self, method, path, body=None, expect=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req) as r:
                status, raw = r.status, r.read()
        except urllib.error.HTTPError as e:
            status, raw = e.code, e.read()
        if expect is not None:
            self.assertEqual(status, expect, raw)
        return json.loads(raw) if raw else None

    def home(self, name="Main"):
        return self.call("POST", "/api/homes", {"name": name}, 201)

    def room(self, home_id, name="Den", **kw):
        return self.call("POST", "/api/homes/%d/rooms" % home_id,
                         dict(name=name, **kw), 201)



class ApiTest(ApiBase):
    def test_custom_categories(self):
        home = self.call("POST", "/api/homes", {"name": "H"}, 201)
        room = self.call("POST", "/api/homes/%d/rooms" % home["id"], {"name": "Garage"}, 201)
        self.call("POST", "/api/rooms/%d/items" % room["id"], {"name": "Saw", "item_type": "Tools"}, 400)
        r = self.call("POST", "/api/categories", {"name": "  Tools "}, 201)
        self.assertEqual(r["name"], "Tools")
        self.assertIn("Tools", r["item_types"])
        self.call("POST", "/api/categories", {"name": "tools"}, 409)       # duplicate, any case
        self.call("POST", "/api/categories", {"name": "Appliance"}, 409)   # built-in
        self.call("POST", "/api/categories", {"name": ""}, 400)
        self.call("POST", "/api/categories", {"name": "x" * 41}, 400)
        m = self.call("GET", "/api/meta", expect=200)
        self.assertIn("Tools", m["item_types"])
        self.assertNotIn("Tools", m["builtin_types"])
        it = self.call("POST", "/api/rooms/%d/items" % room["id"], {"name": "Saw", "item_type": "tools"}, 201)
        self.assertEqual(it["item_type"], "Tools")                         # stored with the category's spelling
        self.call("DELETE", "/api/categories/Tools", expect=409)           # still in use
        self.call("DELETE", "/api/categories/Appliance", expect=404)       # built-in
        self.call("DELETE", "/api/items/%d" % it["id"], expect=200)
        self.call("DELETE", "/api/categories/Tools", expect=200)
        self.assertNotIn("Tools", self.call("GET", "/api/meta", expect=200)["item_types"])

    def test_meta_and_static(self):
        m = self.call("GET", "/api/meta", expect=200)
        self.assertIn("Appliance", m["item_types"])
        self.assertEqual(len(m["room_templates"]["Kitchen"]), 5)
        with urllib.request.urlopen(self.base + "/") as r:
            self.assertIn(b"In My Home", r.read())

    def test_home_crud_and_validation(self):
        h = self.home("Beach House")
        self.call("PUT", "/api/homes/%d" % h["id"], {"address": "1 Shore Rd"}, 200)
        got = self.call("GET", "/api/homes/%d" % h["id"], expect=200)
        self.assertEqual((got["name"], got["address"]), ("Beach House", "1 Shore Rd"))
        self.call("POST", "/api/homes", {"name": "  "}, 400)
        self.call("GET", "/api/homes/99999", expect=404)
        self.call("DELETE", "/api/homes/%d" % h["id"], expect=200)
        self.call("GET", "/api/homes/%d" % h["id"], expect=404)

    def test_template_prefill(self):
        h = self.home()
        k = self.room(h["id"], "Kitchen", prefill=True)
        items = self.call("GET", "/api/rooms/%d/items" % k["id"], expect=200)
        self.assertEqual(sorted(i["name"] for i in items),
                         ["Blender", "Crockpot", "Microwave", "Refrigerator", "Toaster"])
        self.assertTrue(all(i["source"] == "template" for i in items))
        b = self.room(h["id"], "Master Bedroom", prefill=True)
        names = [i["name"] for i in self.call("GET", "/api/rooms/%d/items" % b["id"])]
        self.assertEqual(names.count("Nightstand"), 2)
        self.call("POST", "/api/homes/%d/rooms" % h["id"],
                  {"name": "Attic", "prefill": True}, 400)
        rooms = self.call("GET", "/api/homes/%d/rooms" % h["id"])
        self.assertNotIn("Attic", [r["name"] for r in rooms])

    def test_item_crud_defaults(self):
        h = self.home()
        r = self.room(h["id"])
        base = "/api/rooms/%d/items" % r["id"]
        a = self.call("POST", base, {"name": "Lamp", "purchased_value": 40,
                                     "item_type": "Furniture"}, 201)
        self.assertRegex(a["inv_no"], r"^INV-\d{6}$")
        self.assertEqual(a["replacement_value"], 40)
        self.assertEqual(a["source"], "manual")
        b = self.call("POST", base, {"name": "TV", "purchased_value": "300",
                                     "replacement_value": 450}, 201)
        self.assertNotEqual(a["inv_no"], b["inv_no"])
        self.assertEqual(b["replacement_value"], 450)
        self.call("POST", base, {"name": "X", "item_type": "Bogus"}, 400)
        self.call("POST", base, {"name": "X", "purchased_value": -1}, 400)
        self.call("POST", base, {"purchased_value": 1}, 400)
        u = self.call("PUT", "/api/items/%d" % a["id"],
                      {"brand": "IKEA", "replacement_value": 55}, 200)
        self.assertEqual((u["brand"], u["replacement_value"], u["name"]),
                         ("IKEA", 55, "Lamp"))
        self.call("DELETE", "/api/items/%d" % a["id"], expect=200)
        self.call("GET", "/api/items/%d" % a["id"], expect=404)

    def test_transfer_room(self):
        h1, h2 = self.home("A"), self.home("B")
        r = self.room(h1["id"], "Garage", prefill=True)
        self.call("PUT", "/api/rooms/%d" % r["id"], {"home_id": h2["id"]}, 200)
        self.assertEqual(self.call("GET", "/api/homes/%d/rooms" % h1["id"]), [])
        moved = self.call("GET", "/api/homes/%d/rooms" % h2["id"])
        self.assertEqual(moved[0]["item_count"], 3)
        self.call("PUT", "/api/rooms/%d" % r["id"], {"home_id": 99999}, 404)

    def test_cascade_and_image_cleanup(self):
        h = self.home()
        r = self.room(h["id"])
        it = self.call("POST", "/api/rooms/%d/items" % r["id"], {"name": "Vase"}, 201)
        up = self.call("POST", "/api/items/%d/image?kind=photo" % it["id"],
                       {"data": PNG}, 200)
        path = os.path.join(self.tmp.name, "images", up["image"])
        self.assertTrue(os.path.exists(path))
        with urllib.request.urlopen(self.base + "/images/" + up["image"]) as resp:
            self.assertEqual(resp.headers["Content-Type"], "image/png")
        self.call("POST", "/api/items/%d/image?kind=similar" % it["id"], {"data": PNG}, 200)
        self.call("POST", "/api/items/%d/image?kind=nope" % it["id"], {"data": PNG}, 400)
        self.call("POST", "/api/items/%d/image" % it["id"], {"data": "junk"}, 400)
        self.call("DELETE", "/api/homes/%d" % h["id"], expect=200)
        self.call("GET", "/api/rooms/%d" % r["id"], expect=404)
        self.call("GET", "/api/items/%d" % it["id"], expect=404)
        self.assertFalse(os.path.exists(path))

    def test_totals_and_misc(self):
        h = self.home()
        r = self.room(h["id"])
        self.call("POST", "/api/rooms/%d/items" % r["id"],
                  {"name": "A", "purchased_value": 10, "replacement_value": 20}, 201)
        self.call("POST", "/api/rooms/%d/items" % r["id"],
                  {"name": "B", "purchased_value": 5}, 201)
        got = self.call("GET", "/api/homes/%d" % h["id"])
        self.assertEqual((got["item_count"], got["total_purchased"],
                          got["total_replacement"]), (2, 15, 25))
        self.call("POST", "/api/identify", {}, 400)
        self.call("GET", "/api/nothing", expect=404)
        self.call("DELETE", "/api/meta", expect=405)
        try:
            urllib.request.urlopen(self.base + "/images/..%2F..%2Fserver.py")
            self.fail("traversal served a file")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 404)


    def test_price_updated_stamp(self):
        h = self.home()
        r = self.room(h["id"], "Kitchen", prefill=True)
        tmpl = self.call("GET", "/api/rooms/%d/items" % r["id"])[0]
        self.assertIsNone(tmpl["price_updated_at"])
        base = "/api/rooms/%d/items" % r["id"]
        plain = self.call("POST", base, {"name": "A", "purchased_value": 10}, 201)
        self.assertIsNone(plain["price_updated_at"])
        priced = self.call("POST", base, {"name": "B", "replacement_value": 30}, 201)
        self.assertRegex(priced["price_updated_at"], r"^\d{4}-\d\d-\d\dT")
        same = self.call("PUT", "/api/items/%d" % plain["id"], {"replacement_value": 10}, 200)
        self.assertIsNone(same["price_updated_at"])
        changed = self.call("PUT", "/api/items/%d" % plain["id"], {"replacement_value": 12}, 200)
        self.assertIsNotNone(changed["price_updated_at"])
        conf = self.call("PUT", "/api/items/%d" % tmpl["id"], {"price_confirmed": True}, 200)
        self.assertIsNotNone(conf["price_updated_at"])
        self.assertEqual(conf["replacement_value"], tmpl["replacement_value"])

    def test_similar_url_validation(self):
        h = self.home()
        r = self.room(h["id"])
        base = "/api/rooms/%d/items" % r["id"]
        self.call("POST", base, {"name": "X", "similar_url": "javascript:alert(1)"}, 400)
        ok = self.call("POST", base, {"name": "X", "similar_url": "https://shop.example/x"}, 201)
        self.call("PUT", "/api/items/%d" % ok["id"], {"similar_url": "ftp://x"}, 400)
        self.call("PUT", "/api/items/%d" % ok["id"], {"similar_url": ""}, 200)

    def test_room_totals(self):
        h = self.home()
        r = self.room(h["id"])
        base = "/api/rooms/%d/items" % r["id"]
        self.call("POST", base, {"name": "A", "purchased_value": 10, "replacement_value": 20}, 201)
        self.call("POST", base, {"name": "B", "purchased_value": 5}, 201)
        got = self.call("GET", "/api/rooms/%d" % r["id"])
        self.assertEqual((got["total_purchased"], got["total_replacement"]), (15, 25))

    def test_insurance(self):
        h = self.home()
        path = "/api/homes/%d/insurance" % h["id"]
        empty = self.call("GET", path, expect=200)
        self.assertFalse(empty["configured"])
        self.assertIsNone(empty["contents_coverage"])
        got = self.call("PUT", path, {"insurer": "Acme", "contents_coverage": 50000,
                                      "renewal_date": "2027-03-01"}, 200)
        self.assertTrue(got["configured"])
        got = self.call("PUT", path, {"deductible": "1000"}, 200)  # merge, not replace
        self.assertEqual((got["insurer"], got["contents_coverage"], got["deductible"]),
                         ("Acme", 50000, 1000))
        self.call("PUT", path, {"renewal_date": "03/01/2027"}, 400)
        self.call("PUT", path, {"contents_coverage": -5}, 400)
        self.call("PUT", path, {"deductible": ""}, 200)
        self.assertIsNone(self.call("GET", path)["deductible"])
        self.call("DELETE", path, expect=200)
        self.assertFalse(self.call("GET", path)["configured"])
        self.call("PUT", path, {"insurer": "Acme"}, 200)
        self.call("DELETE", "/api/homes/%d" % h["id"], expect=200)
        self.call("GET", path, expect=404)

    def test_us_address(self):
        m = self.call("GET", "/api/meta")
        codes = [x["code"] for x in m["us_states"]]
        self.assertIn("CA", codes); self.assertIn("DC", codes); self.assertGreaterEqual(len(codes), 51)
        h = self.call("POST", "/api/homes", {"name": "Main", "street": "123 Main St", "street2": "Apt 4",
                                             "city": "Springfield", "state": "il", "zip": "627041234"}, 201)
        self.assertEqual((h["state"], h["zip"]), ("IL", "62704-1234"))
        self.assertEqual(h["address"], "123 Main St, Apt 4, Springfield, IL 62704-1234")
        self.assertEqual(h["legacy_address"], "")
        for bad in ({"state": "ZZ"}, {"state": "Illinois"}, {"zip": "6270"}, {"zip": "ABCDE"}, {"street": "x" * 200}):
            self.call("PUT", "/api/homes/%d" % h["id"], bad, 400)
        got = self.call("PUT", "/api/homes/%d" % h["id"], {"street2": "", "zip": "62704"}, 200)  # merges
        self.assertEqual(got["address"], "123 Main St, Springfield, IL 62704")
        part = self.call("POST", "/api/homes", {"name": "Lot", "city": "Austin", "state": "TX"}, 201)
        self.assertEqual(part["address"], "Austin, TX")

    def test_legacy_free_text_address(self):
        h = self.call("POST", "/api/homes", {"name": "Old", "address": "1 Shore Rd, somewhere"}, 201)
        self.assertEqual((h["address"], h["legacy_address"]), ("1 Shore Rd, somewhere", "1 Shore Rd, somewhere"))
        got = self.call("PUT", "/api/homes/%d" % h["id"], {"name": "Old 2"}, 200)  # untouched by other edits
        self.assertEqual(got["address"], "1 Shore Rd, somewhere")
        got = self.call("PUT", "/api/homes/%d" % h["id"], {"street": "9 Elm St", "city": "Reno",
                                                           "state": "NV", "zip": "89501"}, 200)
        self.assertEqual((got["address"], got["legacy_address"]), ("9 Elm St, Reno, NV 89501", ""))

    def test_insurance_website_liability_riders(self):
        h = self.home()
        path = "/api/homes/%d/insurance" % h["id"]
        riders = [{"name": "Jewelry", "limit": 10000}, {"name": "Fine art", "limit": "25000"},
                  {"name": "Bicycles", "limit": None}]
        got = self.call("PUT", path, {"insurer": "Acme", "website": "https://acme.example/home",
                                      "liability_coverage": 300000, "renewal_date": "2027-05-01",
                                      "riders": riders}, 200)
        self.assertEqual(got["website"], "https://acme.example/home")
        self.assertEqual(got["liability_coverage"], 300000)
        self.assertEqual([(r["name"], r["limit"]) for r in got["riders"]],
                         [("Jewelry", 10000), ("Fine art", 25000), ("Bicycles", None)])
        self.call("PUT", path, {"website": "javascript:alert(1)"}, 400)
        self.call("PUT", path, {"riders": [{"name": "r%d" % i, "limit": 1} for i in range(6)]}, 400)
        self.call("PUT", path, {"riders": [{"name": "", "limit": 5}]}, 400)
        self.call("PUT", path, {"riders": [{"name": "x", "limit": -5}]}, 400)
        self.call("PUT", path, {"riders": "nope"}, 400)
        five = self.call("PUT", path, {"riders": [{"name": "r%d" % i, "limit": i} for i in range(5)]}, 200)
        self.assertEqual(len(five["riders"]), 5)
        kept = self.call("PUT", path, {"insurer": "Other"}, 200)   # riders untouched by other edits
        self.assertEqual(len(kept["riders"]), 5)
        self.assertEqual(self.call("PUT", path, {"riders": []}, 200)["riders"], [])

    def test_homes_include_insurance(self):
        h = self.home("WithIns")
        bare = self.home("NoIns")
        self.call("PUT", "/api/homes/%d/insurance" % h["id"],
                  {"insurer": "Acme", "riders": [{"name": "Jewelry", "limit": 5000}]}, 200)
        by_name = {x["name"]: x for x in self.call("GET", "/api/homes", expect=200)}
        self.assertTrue(by_name["WithIns"]["insurance"]["configured"])
        self.assertEqual(by_name["WithIns"]["insurance"]["riders"][0]["name"], "Jewelry")
        self.assertFalse(by_name["NoIns"]["insurance"]["configured"])
        self.assertEqual(by_name["NoIns"]["insurance"]["riders"], [])
        one = self.call("GET", "/api/homes/%d" % h["id"], expect=200)
        self.assertEqual(one["insurance"]["insurer"], "Acme")
        self.assertFalse(bare["insurance"]["configured"])      # create response has the same shape

    def test_migrates_old_insurance_table(self):
        import sqlite3
        d = tempfile.mkdtemp()
        c = sqlite3.connect(os.path.join(d, "inventory.db"))
        c.executescript("""CREATE TABLE homes (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, address TEXT);
          CREATE TABLE home_insurance (home_id INTEGER PRIMARY KEY REFERENCES homes(id) ON DELETE CASCADE,
            insurer TEXT NOT NULL DEFAULT '', policy_number TEXT NOT NULL DEFAULT '', dwelling_coverage REAL,
            contents_coverage REAL, deductible REAL, annual_premium REAL, renewal_date TEXT NOT NULL DEFAULT '',
            contact TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '', updated_at TEXT);
          INSERT INTO homes (name) VALUES ('H'); INSERT INTO home_insurance (home_id, insurer) VALUES (1, 'Old Co');""")
        c.commit(); c.close()
        old = server.DATA_DIR
        try:
            server.DATA_DIR = d
            server.init_storage()
            conn = server.connect()
            ins = server.ins_out(conn.execute("SELECT * FROM home_insurance").fetchone(), 1)
            conn.close()
            self.assertEqual((ins["insurer"], ins["website"], ins["riders"], ins["liability_coverage"]),
                             ("Old Co", "", [], None))
        finally:
            server.DATA_DIR = old

    def test_model_number_and_description_limit(self):
        h = self.home()
        r = self.room(h["id"])
        base = "/api/rooms/%d/items" % r["id"]
        it = self.call("POST", base, {"name": "Mixer", "brand": "KitchenAid", "model_number": " KSM150PSER ",
                                      "description": "x" * 250}, 201)
        self.assertEqual((it["model_number"], len(it["description"])), ("KSM150PSER", 250))
        self.assertEqual(self.call("POST", base, {"name": "Plain"}, 201)["model_number"], "")
        e = self.call("POST", base, {"name": "Too long", "description": "x" * 251}, 400)
        self.assertIn("250 characters", e["error"])
        self.call("POST", base, {"name": "Long model", "model_number": "m" * 61}, 400)
        up = self.call("PUT", "/api/items/%d" % it["id"], {"model_number": "NEW-1"}, 200)
        self.assertEqual((up["model_number"], up["brand"]), ("NEW-1", "KitchenAid"))   # other fields untouched
        self.call("PUT", "/api/items/%d" % it["id"], {"description": "y" * 251}, 400)
        self.call("PUT", "/api/items/%d" % it["id"], {"model_number": ""}, 200)
        # an older description that is already longer than the limit can stay as it is, but cannot grow or change to another long one
        import sqlite3
        c = sqlite3.connect(os.path.join(self.tmp.name, "inventory.db"))
        c.execute("UPDATE items SET description = ? WHERE id = ?", ("z" * 300, it["id"]))
        c.commit(); c.close()
        self.call("PUT", "/api/items/%d" % it["id"], {"name": "Renamed", "description": "z" * 300}, 200)
        self.call("PUT", "/api/items/%d" % it["id"], {"description": "z" * 301}, 400)
        self.call("PUT", "/api/items/%d" % it["id"], {"description": "short now"}, 200)

    def test_purchase_and_replacement_dates_and_condition(self):
        import datetime
        h = self.home(); r = self.room(h["id"])
        base = "/api/rooms/%d/items" % r["id"]
        self.assertIn("Refurbished", self.call("GET", "/api/meta")["conditions"])
        it = self.call("POST", base, {"name": "Pot", "purchased_value": 99, "purchased_date": "2025-11-29",
                                      "purchased_condition": "New", "replacement_value": 109,
                                      "replacement_date": "2026-01-15"}, 201)
        self.assertEqual((it["purchased_date"], it["purchased_condition"], it["replacement_date"]),
                         ("2025-11-29", "New", "2026-01-15"))                      # an explicit date wins over "now"
        today = datetime.date.today().isoformat()
        auto = self.call("PUT", "/api/items/%d" % it["id"], {"replacement_value": 120}, 200)
        self.assertEqual(auto["replacement_date"], today)                           # changing the cost re-dates it
        same = self.call("PUT", "/api/items/%d" % it["id"], {"name": "Pot 2"}, 200)
        self.assertEqual(same["replacement_date"], today)                           # other edits leave it alone
        hand = self.call("PUT", "/api/items/%d" % it["id"], {"replacement_value": 125, "replacement_date": "2026-02-02"}, 200)
        self.assertEqual((hand["replacement_value"], hand["replacement_date"]), (125, "2026-02-02"))   # explicit beats auto
        self.assertEqual(self.call("PUT", "/api/items/%d" % it["id"], {"replacement_date": ""}, 200)["replacement_date"], "2026-02-02")
        plain = self.call("POST", base, {"name": "Plain"}, 201)
        self.assertEqual((plain["purchased_date"], plain["purchased_condition"], plain["replacement_date"]), ("", "", ""))
        for bad in ({"purchased_date": "03/01/2026"}, {"purchased_date": "2999-01-01"}, {"purchased_condition": "Mint"},
                    {"replacement_date": "yesterday"}, {"replacement_date": "2999-01-01"}):
            self.call("PUT", "/api/items/%d" % it["id"], bad, 400)
            self.call("POST", base, dict(bad, name="bad"), 400)
        self.assertEqual(self.call("PUT", "/api/items/%d" % it["id"], {"purchased_condition": ""}, 200)["purchased_condition"], "")
        listed = {i["name"]: i for i in self.call("GET", base)}
        self.assertEqual(listed["Pot 2"]["replacement_date"], "2026-02-02")          # list responses carry it too

    def test_migrates_old_database(self):
        import sqlite3
        d = tempfile.mkdtemp()
        c = sqlite3.connect(os.path.join(d, "inventory.db"))
        c.executescript("""CREATE TABLE homes (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, address TEXT);
          CREATE TABLE rooms (id INTEGER PRIMARY KEY AUTOINCREMENT, home_id INTEGER NOT NULL, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '');
          CREATE TABLE items (id INTEGER PRIMARY KEY AUTOINCREMENT, inv_no TEXT UNIQUE, room_id INTEGER NOT NULL, name TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '', brand TEXT NOT NULL DEFAULT '', purchased_value REAL NOT NULL DEFAULT 0,
            replacement_value REAL NOT NULL DEFAULT 0, similar_url TEXT NOT NULL DEFAULT '', item_type TEXT, image TEXT,
            similar_image TEXT, source TEXT NOT NULL DEFAULT 'manual');
          INSERT INTO homes (name, address) VALUES ('Old', '5 Pine Ct'); INSERT INTO rooms (home_id, name) VALUES (1, 'R');
          INSERT INTO items (room_id, name) VALUES (1, 'Old thing');""")
        c.commit(); c.close()
        old = server.DATA_DIR
        try:
            server.DATA_DIR = d
            server.init_storage()
            conn = server.connect()
            row = conn.execute("SELECT name, price_updated_at FROM items").fetchone()
            conn.close()
            self.assertEqual((row["name"], row["price_updated_at"]), ("Old thing", None))
            conn = server.connect()
            row2 = conn.execute("SELECT model_number, purchased_date, purchased_condition FROM items").fetchone()
            self.assertEqual(tuple(row2), ("", "", ""))
            conn.close()
            conn = server.connect()
            home = dict(conn.execute("SELECT * FROM homes").fetchone())
            conn.close()
            self.assertEqual((home["address"], home["street"], home["zip"]), ("5 Pine Ct", "", ""))
            self.assertEqual(server.home_out(home)["address"], "5 Pine Ct")
        finally:
            server.DATA_DIR = old


class FakeProvider:
    """Stands in for Ollama or the Anthropic API: records requests, returns canned replies."""
    def __init__(self):
        self.requests, self.reply, self.status, self.reply_fn = [], {}, 200, None
        self.tags, self.show = [{"name": "qwen2.5vl:7b"}], {}
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a): pass
            def _send(self, code, obj):
                raw = json.dumps(obj).encode()
                self.send_response(code); self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw)
            def do_GET(self):
                outer.requests.append(("GET", self.path, {}, dict(self.headers)))
                self._send(200, {"models": outer.tags})
            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n))
                outer.requests.append(("POST", self.path, body, dict(self.headers)))
                if self.path == "/api/show":
                    return self._send(200, outer.show.get(body.get("model"), {}))
                self._send(outer.status, outer.reply_fn(body) if outer.reply_fn else outer.reply)

        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = "http://127.0.0.1:%d" % self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown(); self.srv.server_close()


class FakeProvidersBase(ApiBase):
    """Starts fake Ollama / Claude servers (no real model needed)."""
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.ollama, cls.claude = FakeProvider(), FakeProvider()
        cls._old_base = server.ANTHROPIC_BASE_URL
        server.ANTHROPIC_BASE_URL = cls.claude.url

    @classmethod
    def tearDownClass(cls):
        server.ANTHROPIC_BASE_URL = cls._old_base
        cls.ollama.close(); cls.claude.close()
        super().tearDownClass()

    def setUp(self):
        self.ollama.requests.clear(); self.claude.requests.clear()
        self.ollama.status = self.claude.status = 200
        self.call("PUT", "/api/settings", {"ollama_url": self.ollama.url, "ollama_model": "qwen2.5vl:7b",
                                           "anthropic_key": "", "anthropic_model": "claude-haiku-5-5"}, 200)

    ITEM = {"name": " Stand Mixer ", "brand": "KitchenAid", "item_type": "appliance",
            "description": "Red tilt-head mixer.", "search_query": "kitchenaid artisan 5qt red"}



class IdentifyTest(FakeProvidersBase):
    """Photo-to-item."""

    def test_defaults_and_status(self):
        st = self.call("GET", "/api/settings", expect=200)
        self.assertEqual(st["ollama"]["model"], "qwen2.5vl:7b")
        self.assertEqual(st["anthropic"]["model"], "claude-haiku-5-5")
        self.assertFalse(st["anthropic"]["configured"])
        ps = self.call("GET", "/api/providers/status", expect=200)
        self.assertTrue(ps["ollama"]["reachable"] and ps["ollama"]["model_installed"])
        self.call("PUT", "/api/settings", {"ollama_model": "other:1b"}, 200)
        self.assertFalse(self.call("GET", "/api/providers/status")["ollama"]["model_installed"])
        self.call("PUT", "/api/settings", {"ollama_url": "file:///etc/passwd"}, 400)
        self.call("PUT", "/api/settings", {"ollama_model": "has space"}, 400)

    def test_ollama_identify(self):
        self.ollama.reply = {"message": {"content": json.dumps(self.ITEM)}}
        r = self.call("POST", "/api/identify", {"data": PNG}, 200)
        self.assertEqual((r["name"], r["item_type"], r["provider"], r["model"]),
                         ("Stand Mixer", "Appliance", "ollama", "qwen2.5vl:7b"))
        self.assertEqual(r["model_number"], "")                                   # not in this reply: empty, not an error
        self.assertIn("model_number", self.ollama.requests[-1][2]["format"]["properties"])
        self.ollama.reply = {"message": {"content": json.dumps(dict(self.ITEM, model_number=" KSM150 ", description="d" * 400))}}
        r2 = self.call("POST", "/api/identify", {"data": PNG}, 200)
        self.assertEqual((r2["model_number"], len(r2["description"])), ("KSM150", 250))   # trimmed, and kept within the limit
        method, path, body, _ = self.ollama.requests[-1]
        self.assertEqual(path, "/api/chat")
        self.assertEqual(body["model"], "qwen2.5vl:7b")
        self.assertFalse(body["stream"])
        self.assertEqual(body["options"]["temperature"], 0)
        self.assertIn("Appliance", body["format"]["properties"]["item_type"]["enum"])
        self.assertEqual(body["messages"][0]["images"], [PNG.split(",", 1)[1]])

    def test_output_is_normalised(self):
        self.ollama.reply = {"message": {"content": 'Sure! {"name":"Mug","item_type":"Other","brand":5}'}}
        r = self.call("POST", "/api/identify", {"data": PNG}, 200)
        self.assertEqual((r["name"], r["item_type"], r["brand"]), ("Mug", None, ""))
        self.ollama.reply = {"message": {"content": "no json here"}}
        self.call("POST", "/api/identify", {"data": PNG}, 502)

    def test_installed_models(self):
        self.ollama.tags = [
            {"name": "llama3.2:3b", "size": 2_000_000_000, "details": {"parameter_size": "3.2B", "quantization_level": "Q4_K_M"}},
            {"name": "qwen2.5vl:7b", "size": 6_000_000_000, "details": {"parameter_size": "7B", "family": "qwen25vl"}},
            {"name": "old-vision:7b", "details": {"families": ["clip", "llama"]}},
            {"name": "mystery:1b"}]
        self.ollama.show = {"llama3.2:3b": {"capabilities": ["completion"]},
                            "qwen2.5vl:7b": {"capabilities": ["completion", "vision"]},
                            "old-vision:7b": {"model_info": {"clip.vision.block_count": 24}}}
        r = self.call("GET", "/api/providers/ollama-models", expect=200)
        self.assertTrue(r["reachable"]); self.assertEqual(r["current"], "qwen2.5vl:7b")
        by = {m["name"]: m for m in r["models"]}
        self.assertEqual((by["qwen2.5vl:7b"]["vision"], by["llama3.2:3b"]["vision"], by["old-vision:7b"]["vision"],
                          by["mystery:1b"]["vision"]), (True, False, True, None))
        self.assertEqual((by["qwen2.5vl:7b"]["size"], by["qwen2.5vl:7b"]["parameter_size"]), (6_000_000_000, "7B"))
        self.assertEqual([m["name"] for m in r["models"]][:2], ["old-vision:7b", "qwen2.5vl:7b"])   # vision models first
        self.ollama.tags = [{"name": "qwen2.5vl:7b"}]
        self.call("PUT", "/api/settings", {"ollama_url": "http://127.0.0.1:9"}, 200)
        r = self.call("GET", "/api/providers/ollama-models", expect=200)
        self.assertEqual((r["reachable"], r["models"]), (False, []))

    def test_text_only_model_gets_a_clear_message(self):
        self.ollama.status = 400
        self.ollama.reply = {"error": "this model does not support image input"}
        e = self.call("POST", "/api/identify", {"data": PNG}, 502)
        self.assertIn("can't read images", e["error"])
        self.assertIn("qwen2.5vl:7b", e["error"])
        self.ollama.reply = {"error": "something else"}
        self.assertIn("HTTP 400", self.call("POST", "/api/identify", {"data": PNG}, 502)["error"])

    def test_ollama_errors(self):
        self.ollama.status = 404
        e = self.call("POST", "/api/identify", {"data": PNG}, 502)
        self.assertIn("ollama pull qwen2.5vl:7b", e["error"])
        self.call("PUT", "/api/settings", {"ollama_url": "http://127.0.0.1:9"}, 200)  # nothing listens
        e = self.call("POST", "/api/identify", {"data": PNG}, 502)
        self.assertIn("Could not reach Ollama", e["error"])

    def test_claude_byo_key(self):
        self.call("POST", "/api/identify", {"data": PNG, "provider": "anthropic"}, 400)  # no key yet
        st = self.call("PUT", "/api/settings", {"anthropic_key": "sk-ant-test-key-1234567890"}, 200)
        self.assertTrue(st["anthropic"]["configured"])
        self.assertNotIn("sk-ant", json.dumps(st))                       # key is never returned
        self.assertNotIn("sk-ant", json.dumps(self.call("GET", "/api/settings")))
        self.claude.reply = {"content": [{"type": "tool_use", "name": "record_item", "input": self.ITEM}]}
        r = self.call("POST", "/api/identify", {"data": PNG, "provider": "anthropic"}, 200)
        self.assertEqual((r["name"], r["provider"], r["model"]), ("Stand Mixer", "anthropic", "claude-haiku-5-5"))
        method, path, body, headers = self.claude.requests[-1]
        self.assertEqual(path, "/v1/messages")
        hdr = {k.lower(): v for k, v in headers.items()}
        self.assertEqual(hdr["x-api-key"], "sk-ant-test-key-1234567890")
        self.assertIn("anthropic-version", hdr)
        self.assertEqual(body["tool_choice"], {"type": "tool", "name": "record"})
        img = body["messages"][0]["content"][0]
        self.assertEqual((img["type"], img["source"]["media_type"]), ("image", "image/png"))
        self.assertEqual(self.ollama.requests, [])                      # cloud mode never touches Ollama
        mode = os.stat(os.path.join(self.tmp.name, "settings.json")).st_mode & 0o777
        self.assertEqual(mode, 0o600)
        self.claude.status = 401
        e = self.call("POST", "/api/identify", {"data": PNG, "provider": "anthropic"}, 502)
        self.assertIn("rejected the API key", e["error"]); self.assertNotIn("sk-ant", e["error"])
        self.call("PUT", "/api/settings", {"anthropic_key": ""}, 200)   # clearing removes it
        self.assertFalse(self.call("GET", "/api/settings")["anthropic"]["configured"])
        self.call("PUT", "/api/settings", {"anthropic_key": "short"}, 400)

    def test_input_validation(self):
        self.call("POST", "/api/identify", {"data": "junk"}, 400)
        self.call("POST", "/api/identify", {"data": PNG, "provider": "bogus"}, 400)

    def test_cross_site_protections(self):
        port = self.srv.server_address[1]
        for host, ctype, want in [("evil.example", "application/json", 403),
                                  ("127.0.0.1:%d" % port, "text/plain", 415),
                                  ("localhost:%d" % port, "application/json", 200)]:
            c = http.client.HTTPConnection("127.0.0.1", port)
            c.putrequest("PUT", "/api/settings", skip_host=True)
            c.putheader("Host", host); c.putheader("Content-Type", ctype)
            body = b'{"ollama_model": "qwen2.5vl:7b"}'
            c.putheader("Content-Length", str(len(body))); c.endheaders(body)
            self.assertEqual(c.getresponse().status, want, host + " " + ctype)
            c.close()


RECEIPT_REPLY = {"store": "Target", "date": "03/02/2026", "currency": "USD", "subtotal": 389.97, "tax": 31.2,
                 "total": 421.17, "line_items": [
                     {"description": "Toaster", "quantity": 1, "unit_price": 29.99, "total_price": 29.99, "category": "Appliance"},
                     {"description": "Nightstand", "quantity": 2, "unit_price": 179.99, "total_price": 359.98, "category": "Furniture"},
                     {"description": "Bananas", "quantity": 1, "unit_price": 1.2, "total_price": 1.2, "category": "Consumable"},
                     {"description": "TOTAL", "quantity": 1, "unit_price": 421.17, "total_price": 421.17, "category": "Other"}]}


class ScanLaterTest(FakeProvidersBase):
    """Photos saved first, read by the AI later: pending items, /scan, the queue and confirming."""

    def setUp(self):
        super().setUp()
        self.ollama.reply_fn = None
        home = self.call("POST", "/api/homes", {"name": "H"}, 201)
        self.room = self.call("POST", "/api/homes/%d/rooms" % home["id"], {"name": "Kitchen"}, 201)
        self.home = home

    def photo_item(self, name="Photo 1"):
        it = self.call("POST", "/api/rooms/%d/items" % self.room["id"], {"name": name, "scan_state": "pending"}, 201)
        return self.call("POST", "/api/items/%d/image?kind=photo" % it["id"], {"data": PNG}, 200)

    def answer(self, kind, **extra):
        def fn(body):
            props = body["format"]["properties"]
            if "kind" in props:
                return {"message": {"content": json.dumps({"kind": kind})}}
            if "line_items" in props:
                return {"message": {"content": json.dumps(RECEIPT_REPLY)}}
            return {"message": {"content": json.dumps(self.ITEM)}}
        self.ollama.reply_fn = fn

    def test_pending_items_and_queue(self):
        it = self.photo_item()
        self.assertEqual((it["scan_state"], it["scan_note"]), ("pending", ""))
        self.call("POST", "/api/rooms/%d/items" % self.room["id"], {"name": "x", "scan_state": "done"}, 400)
        self.call("POST", "/api/rooms/%d/items" % self.room["id"], {"name": "Plain"}, 201)
        q = self.call("GET", "/api/scan-queue", expect=200)
        mine = [i for i in q if i["home_id"] == self.home["id"]]
        self.assertEqual([(i["id"], i["room_name"], i["home_name"]) for i in mine], [(it["id"], "Kitchen", "H")])
        self.assertEqual(self.call("GET", "/api/homes/%d" % self.home["id"], expect=200)["scan_count"], 1)

    def test_scan_item(self):
        it = self.photo_item()
        self.answer("item")
        r = self.call("POST", "/api/items/%d/scan" % it["id"], {}, 200)
        self.assertEqual(r["kind"], "item")
        i = r["item"]
        self.assertEqual((i["name"], i["brand"], i["item_type"], i["source"], i["scan_state"]), ("Stand Mixer", "KitchenAid", "Appliance", "vision", "review"))
        self.assertIn("qwen2.5vl:7b", i["scan_note"])
        self.assertTrue(i["image"], "the photo stays")
        done = self.call("PUT", "/api/items/%d" % it["id"], {"scan_state": ""}, 200)       # "looks right"
        self.assertEqual((done["scan_state"], done["scan_note"]), ("", ""))
        self.assertEqual([i for i in self.call("GET", "/api/scan-queue", expect=200) if i["id"] == it["id"]], [])
        self.call("PUT", "/api/items/%d" % it["id"], {"scan_state": "review"}, 400)

    def test_scan_receipt_splits_into_items(self):
        it = self.photo_item("Receipt photo")
        self.answer("receipt")
        r = self.call("POST", "/api/items/%d/scan" % it["id"], {}, 200)
        self.assertEqual((r["kind"], r["deleted"]), ("receipt", it["id"]))
        self.assertEqual(len(r["created"]), 4)                              # toaster, 2 nightstands, bananas
        items = {i["id"]: i for i in self.call("GET", "/api/rooms/%d/items" % self.room["id"], expect=200)}
        self.assertNotIn(it["id"], items)
        made = [items[i] for i in r["created"]]
        self.assertEqual(sorted(i["name"] for i in made), ["Bananas", "Nightstand", "Nightstand", "Toaster"])
        self.assertTrue(all(i["scan_state"] == "review" and i["purchased_date"] == "2026-03-02" and "Target" in i["scan_note"] for i in made))
        self.assertEqual(sum(1 for i in made if i["image"]), 1, "the receipt photo goes with the first line only")
        self.assertEqual(next(i for i in made if i["name"] == "Toaster")["purchased_value"], 29.99)
        self.assertEqual(next(i for i in made if i["name"] == "Bananas")["item_type"], None)    # Consumable is not an item type
        first = next(i for i in made if i["image"])
        with urllib.request.urlopen(self.base + "/images/" + first["image"]) as resp:
            self.assertEqual(resp.status, 200)

    def test_scan_other_and_errors(self):
        it = self.photo_item()
        self.answer("other")
        r = self.call("POST", "/api/items/%d/scan" % it["id"], {}, 200)
        self.assertEqual((r["kind"], r["item"]["scan_state"]), ("other", "failed"))
        self.assertIn("not an item or a receipt", r["item"]["scan_note"])
        again = self.call("PUT", "/api/items/%d" % it["id"], {"scan_state": "pending"}, 200)    # read it again
        self.assertEqual(again["scan_state"], "pending")
        self.ollama.status = 500
        self.call("POST", "/api/items/%d/scan" % it["id"], {}, 502)                           # AI down: stays pending, nothing lost
        self.assertEqual(self.call("GET", "/api/items/%d" % it["id"], expect=200)["scan_state"], "pending")
        self.call("POST", "/api/items/%d/scan" % it["id"], {"provider": "anthropic"}, 400)    # no Claude key
        plain = self.call("POST", "/api/rooms/%d/items" % self.room["id"], {"name": "No photo", "scan_state": "pending"}, 201)
        self.call("POST", "/api/items/%d/scan" % plain["id"], {}, 400)


class BatchEndpointsTest(FakeProvidersBase):
    """/classify and /receipt through the same fake Ollama / Claude servers."""

    def test_classify(self):
        self.ollama.reply = {"message": {"content": '{"kind": "receipt"}'}}
        r = self.call("POST", "/api/classify", {"data": PNG}, 200)
        self.assertEqual((r["kind"], r["provider"], r["model"]), ("receipt", "ollama", "qwen2.5vl:7b"))
        self.assertEqual(self.ollama.requests[-1][2]["format"]["properties"]["kind"]["enum"], ["receipt", "item", "other"])
        self.ollama.reply = {"message": {"content": '{"kind": "banana"}'}}
        self.assertEqual(self.call("POST", "/api/classify", {"data": PNG}, 200)["kind"], "other")
        self.call("POST", "/api/classify", {"data": "junk"}, 400)

    def test_receipt(self):
        self.ollama.reply = {"message": {"content": json.dumps(RECEIPT_REPLY)}}
        r = self.call("POST", "/api/receipt", {"data": PNG}, 200)
        self.assertEqual((r["store"], r["date"], r["provider"]), ("Target", "2026-03-02", "ollama"))
        self.assertEqual([i["description"] for i in r["items"]], ["Toaster", "Nightstand", "Bananas"])
        self.assertEqual(r["items"][1]["quantity"], 2)
        cats = self.ollama.requests[-1][2]["format"]["properties"]["line_items"]["items"]["properties"]["category"]["enum"]
        self.assertEqual(cats, server.ITEM_TYPES + ["Consumable", "Other"])
        self.ollama.reply = {"message": {"content": "not json"}}
        self.call("POST", "/api/receipt", {"data": PNG}, 502)

    def test_receipt_and_classify_with_claude(self):
        self.call("POST", "/api/receipt", {"data": PNG, "provider": "anthropic"}, 400)       # no key yet
        self.call("PUT", "/api/settings", {"anthropic_key": "sk-ant-test-key-1234567890"}, 200)
        self.claude.reply = {"content": [{"type": "tool_use", "name": "record", "input": RECEIPT_REPLY}]}
        r = self.call("POST", "/api/receipt", {"data": PNG, "provider": "anthropic"}, 200)
        self.assertEqual((r["provider"], r["model"], len(r["items"])), ("anthropic", "claude-haiku-5-5", 3))
        self.claude.reply = {"content": [{"type": "tool_use", "name": "record", "input": {"kind": "item"}}]}
        self.assertEqual(self.call("POST", "/api/classify", {"data": PNG, "provider": "anthropic"}, 200)["kind"], "item")
        self.assertEqual(self.ollama.requests, [])
        self.call("PUT", "/api/settings", {"anthropic_key": ""}, 200)


PRODUCT_PAGE = ("<html><head><title>Mixer page</title><script type='application/ld+json'>"
                '{"@type":"Product","name":"KitchenAid Mixer","offers":{"@type":"Offer","price":"349.99","priceCurrency":"USD"}}'
                "</script></head><body>hi</body></html>")


SYNTHETIC_NEXT_PAGE = ("<html><head><title>Instant Pot</title></head><body><script id=\"__NEXT_DATA__\" type=\"application/json\">"
    + json.dumps({"props": {"pageProps": {"initialData": {"data": {
        "product": {"usItemId": "45918917", "name": "Instant Pot", "priceInfo": {"currentPrice": {
            "price": 99.0, "priceString": "$99.00", "currencyUnit": "USD"}}},
        "contentLayout": {"modules": [{"products": [
            {"usItemId": "11111111", "priceInfo": {"currentPrice": {"price": 5.0}}}]}]}}}}}})
    + "</script></body></html>")


class PageServer:
    """A pretend shop: /ok has a price, /meta uses og tags, /none has none, /redir and /bad redirect, /forbidden is 403."""
    def __init__(self):
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a): pass
            def do_GET(self):
                code, ctype, body, loc = 200, "text/html; charset=utf-8", "", None
                if self.path == "/ok": body = PRODUCT_PAGE
                elif self.path == "/meta": body = '<meta property="og:price:amount" content="1,299.50"><meta property="og:price:currency" content="EUR"><title>T</title>'
                elif self.path == "/none": body = "<html><body>Price: $5 (in text only)</body></html>"
                elif self.path == "/redir": code, loc = 302, "/ok"
                elif self.path == "/bad": code, loc = 302, "ftp://example.com/x"
                elif self.path == "/forbidden": code = 403
                elif self.path == "/img": ctype, body = "image/png", "x"
                elif self.path == "/withpic": body = '<html><meta property="og:image" content="/pic.png"><title>P</title></html>'
                elif self.path == "/ldpic": body = '<script type="application/ld+json">{"@type":"Product","name":"N","image":["/pic.png"]}</script>'
                elif self.path == "/badpic": body = '<meta property="og:image" content="/notimg">'
                elif self.path == "/notimg": body = "<html>not an image</html>"
                elif self.path.startswith("/ip/Instant-Pot/45918917"): body = SYNTHETIC_NEXT_PAGE
                elif self.path.startswith("/ip/Other/22222222"): body = SYNTHETIC_NEXT_PAGE      # id not in the data
                elif self.path == "/robot": body = "<html><title>Robot or human?</title><div id='px-captcha'>Press &amp; Hold</div></html>"
                elif self.path == "/blocked412": code, body = 412, "<html><div id='px-captcha'></div>Robot or human?</html>"
                elif self.path == "/blocked403": code, body = 403, "<html>Forbidden</html>"
                elif self.path == "/big": body = "<p>" + "x" * 2_000_000 + "</p>" + PRODUCT_PAGE
                elif self.path == "/pic.png": pass
                else: code = 404
                raw = body.encode()
                if self.path == "/pic.png":
                    code, ctype, raw = 200, "image/png", base64.b64decode(PNG.split(",", 1)[1])
                self.send_response(code)
                if loc: self.send_header("Location", loc)
                self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(raw)))
                self.end_headers(); self.wfile.write(raw)

        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = "http://127.0.0.1:%d" % self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown(); self.srv.server_close()


class FakeGoogle:
    """Stands in for Google's token and Drive endpoints."""
    def __init__(self):
        outer = self
        self.uploads, self.folders, self.token_forms = [], [], []
        self.fail_refresh = False

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a): pass
            def reply(self, code, obj):
                raw = json.dumps(obj).encode()
                self.send_response(code); self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw)
            def body(self):
                return self.rfile.read(int(self.headers.get("Content-Length") or 0))
            def do_POST(self):
                import urllib.parse as up
                b = self.body()
                if self.path == "/token":
                    f = {k: v[0] for k, v in up.parse_qs(b.decode()).items()}
                    outer.token_forms.append(f)
                    if f["client_secret"] != "good-secret":
                        return self.reply(401, {"error": "invalid_client"})
                    if f["grant_type"] == "authorization_code":
                        return self.reply(200, {"access_token": "a1", "refresh_token": "r1"})
                    if outer.fail_refresh or f["refresh_token"] != "r1":
                        return self.reply(400, {"error": "invalid_grant"})
                    return self.reply(200, {"access_token": "a2"})
                if self.headers.get("Authorization") != "Bearer a2":
                    return self.reply(401, {"error": {"message": "bad token"}})
                if self.path.startswith("/upload/drive/v3/files"):
                    outer.uploads.append((self.headers.get("Content-Type"), b))
                    return self.reply(200, {"id": "F1", "name": "x.pdf", "webViewLink": "https://drive.google.com/file/d/F1/view"})
                if self.path.startswith("/drive/v3/files"):
                    outer.folders.append(json.loads(b))
                    return self.reply(200, {"id": "FOLDER1"})
                self.reply(404, {})
            def do_GET(self):
                if self.headers.get("Authorization") != "Bearer a2":
                    return self.reply(401, {"error": {"message": "bad token"}})
                self.reply(200, {"files": []})

        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = "http://127.0.0.1:%d" % self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown(); self.srv.server_close()


def real_png(w=8, h=6):
    import struct, zlib
    def chunk(k, b):
        c = k + b
        return struct.pack(">I", len(b)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)
    rows = b"".join(b"\x00" + b"\x40\x80\xc0" * w for _ in range(h))
    return ("data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")).decode())


class ReportTest(ApiBase):
    def make_home(self):
        home = self.call("POST", "/api/homes", {"name": "Lake House", "street": "1 Main St", "city": "Austin", "state": "TX", "zip": "78701"}, 201)
        room = self.call("POST", "/api/homes/%d/rooms" % home["id"], {"name": "Kitchen"}, 201)
        for n in range(30):
            self.call("POST", "/api/rooms/%d/items" % room["id"], {"name": "Item %d" % n, "description": "d " * 100 if n % 2 else "",
                      "purchased_value": 10, "replacement_value": 12}, 201)
        self.call("POST", "/api/homes/%d/rooms" % home["id"], {"name": "Empty room"}, 201)
        return home

    def fetch(self, path):
        with urllib.request.urlopen(self.base + path) as r:
            return r.read(), r.headers

    def test_pdf_with_and_without_photos(self):
        home = self.make_home()
        items = self.call("GET", "/api/rooms/%d/items" % self.call("GET", "/api/homes/%d/rooms" % home["id"], expect=200)[1]["id"], expect=200)
        self.call("POST", "/api/items/%d/image?kind=photo" % items[0]["id"], {"data": real_png()}, 200)
        data, h = self.fetch("/api/homes/%d/report.pdf" % home["id"])
        self.assertTrue(data.startswith(b"%PDF-1.4") and data.rstrip().endswith(b"%%EOF"))
        self.assertEqual(h["Content-Type"], "application/pdf")
        self.assertIn("attachment", h["Content-Disposition"])
        self.assertIn("lake-house-inventory-", h["Content-Disposition"])
        self.assertNotIn(b"/Subtype /Image", data)
        pics, h2 = self.fetch("/api/homes/%d/report.pdf?photos=1" % home["id"])
        self.assertIn(b"/Subtype /Image", pics)
        self.assertIn("-photos-", h2["Content-Disposition"])
        pages = data.count(b"/Type /Page ")
        self.assertGreaterEqual(pages, 2)                      # 30 items need more than one page
        rep = self.call("GET", "/api/homes/%d/report" % home["id"], expect=200)
        self.assertEqual((rep["item_count"], rep["total_replacement"], len(rep["rooms"])), (30, 360, 2))
        self.call("GET", "/api/homes/999/report.pdf", expect=404)

    def test_hide_purchase_price(self):
        import zlib, re
        home = self.make_home()

        def text_of(path):
            data, h = self.fetch(path)
            streams = re.findall(rb"stream\n(.*?)\nendstream", data, re.S)
            out = b""
            for s in streams:
                try:
                    out += zlib.decompress(s)
                except zlib.error:
                    pass
            return out, h
        shown, h1 = text_of("/api/homes/%d/report.pdf" % home["id"])
        hidden, h2 = text_of("/api/homes/%d/report.pdf?hide_purchase_price=1" % home["id"])
        self.assertIn(b"($300.00) Tj", shown); self.assertIn(b"($10.00) Tj", shown)
        self.assertNotIn(b"($10.00) Tj", hidden); self.assertNotIn(b"($300.00) Tj", hidden)      # no per-item price, no total
        self.assertIn(b"($12.00) Tj", hidden)                                                     # replacement cost stays
        self.assertIn("-no-purchase-price-", h2["Content-Disposition"])
        both, _ = text_of("/api/homes/%d/report.pdf?photos=1&hide_purchase_price=1" % home["id"])
        self.assertNotIn(b"Purchased: $10.00", both)

    def test_pdf_writer_basics(self):
        import pdfwriter as pw
        self.assertEqual(pw.wrap("a b c", 1000), ["a b c"])
        self.assertTrue(all(pw.text_width(l, "F1", 10) <= 100 for l in pw.wrap("word " * 40 + "x" * 80, 100)))
        self.assertIsNone(pw.load_image(b"not an image"))
        self.assertEqual(pw.encode("caf\u00e9 \u2014 \u4e2d"), b"caf\xe9 \x97 ?")


class GoogleDriveTest(ReportTest):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.google = FakeGoogle()

    @classmethod
    def tearDownClass(cls):
        cls.google.close()
        super().tearDownClass()

    def setUp(self):
        import gdrive
        self.gd = gdrive
        for k, v in (("TOKEN_URL", self.google.url + "/token"), ("API_URL", self.google.url), ("AUTH_URL", "https://accounts.example/auth")):
            self.addCleanup(setattr, gdrive, k, getattr(gdrive, k)); setattr(gdrive, k, v)
        self.google.uploads.clear(); self.google.folders.clear(); self.google.token_forms.clear(); self.google.fail_refresh = False
        self.call("DELETE", "/api/google", expect=200)
        self.call("PUT", "/api/settings", {"google_client_id": "", "google_client_secret": "x"}, 200)

    def connect(self, secret="good-secret"):
        self.call("PUT", "/api/settings", {"google_client_id": "cid.apps.example", "google_client_secret": secret}, 200)
        url = self.call("GET", "/api/google/auth-url", expect=200)["url"]
        import urllib.parse as up
        q = {k: v[0] for k, v in up.parse_qs(up.urlparse(url).query).items()}
        return q

    def callback(self, **params):
        import urllib.parse as up
        try:
            with urllib.request.urlopen(self.base + "/oauth/google?" + up.urlencode(params)) as r:
                return r.status, r.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()

    def test_connect_and_upload(self):
        self.assertFalse(self.call("GET", "/api/settings", expect=200)["google"]["connected"])
        q = self.connect()
        self.assertEqual((q["client_id"], q["scope"], q["code_challenge_method"], q["access_type"]),
                         ("cid.apps.example", "https://www.googleapis.com/auth/drive.file", "S256", "offline"))
        self.assertTrue(q["redirect_uri"].endswith("/oauth/google"))
        status, html = self.callback(code="abc", state="forged")
        self.assertEqual(status, 400)                                   # unknown state is refused
        self.assertFalse(self.call("GET", "/api/settings", expect=200)["google"]["connected"])
        status, html = self.callback(code="abc", state=q["state"])
        self.assertEqual(status, 200); self.assertIn("connected", html)
        form = self.google.token_forms[-1]
        self.assertEqual((form["grant_type"], form["code"]), ("authorization_code", "abc"))
        self.assertTrue(form["code_verifier"])
        s = self.call("GET", "/api/settings", expect=200)["google"]
        self.assertEqual((s["connected"], s["client_id"], s["secret_configured"]), (True, "cid.apps.example", True))
        self.assertNotIn("good-secret", json.dumps(self.call("GET", "/api/settings", expect=200)))     # secret never returned
        self.assertNotIn("r1", json.dumps(self.call("GET", "/api/settings", expect=200)["google"]))
        self.assertEqual(self.callback(code="abc", state=q["state"])[0], 400)       # state is single use
        home = self.make_home()
        out = self.call("POST", "/api/homes/%d/report/drive?photos=1" % home["id"], {}, 200)
        self.assertEqual((out["link"], out["folder"]), ("https://drive.google.com/file/d/F1/view", "In My Home"))
        self.assertEqual(self.google.folders, [{"name": "In My Home", "mimeType": "application/vnd.google-apps.folder"}])
        ctype, body = self.google.uploads[-1]
        self.assertIn("multipart/related", ctype)
        self.assertIn(b"%PDF-1.4", body); self.assertIn(b'"parents": ["FOLDER1"]', body); self.assertIn(b"lake-house-inventory-photos-", body)
        mode = oct(os.stat(os.path.join(server.DATA_DIR, "settings.json")).st_mode & 0o777)
        self.assertEqual(mode, "0o600")

    def test_errors(self):
        home = self.make_home()
        self.call("POST", "/api/homes/%d/report/drive" % home["id"], {}, 400)          # not connected
        self.call("DELETE", "/api/google", expect=200)
        self.call("PUT", "/api/settings", {"google_client_id": "", "google_client_secret": ""}, 200)
        self.call("GET", "/api/google/auth-url", expect=400)                            # needs client id first
        q = self.connect(secret="wrong")
        status, html = self.callback(code="abc", state=q["state"])
        self.assertEqual(status, 400); self.assertIn("client id or secret", html)
        q = self.connect()
        self.callback(code="abc", state=q["state"])
        self.google.fail_refresh = True
        r = self.call("POST", "/api/homes/%d/report/drive" % home["id"], {}, 401)
        self.assertIn("Connect Google Drive again", r["error"])
        self.assertEqual(self.callback(error="access_denied")[0], 400)
        self.call("PUT", "/api/settings", {"google_client_id": "other"}, 200)           # new client drops the old sign-in
        self.assertFalse(self.call("GET", "/api/settings", expect=200)["google"]["connected"])


class LinkPriceTest(ApiBase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.shop = PageServer()

    @classmethod
    def tearDownClass(cls):
        cls.shop.close()
        super().tearDownClass()

    def setUp(self):
        server.ALLOW_PRIVATE_FETCH = True      # the fake shop is on localhost
        self.addCleanup(setattr, server, "ALLOW_PRIVATE_FETCH", False)

    def test_price_from_json_ld(self):
        r = self.call("POST", "/api/link-info", {"url": self.shop.url + "/ok"}, 200)
        self.assertEqual((r["price"], r["currency"], r["title"], r["source"]), (349.99, "USD", "KitchenAid Mixer", "json-ld"))
        self.assertEqual(r["host"], "127.0.0.1")

    def test_picture_from_link(self):
        home = self.call("POST", "/api/homes", {"name": "H"}, 201)
        room = self.call("POST", "/api/homes/%d/rooms" % home["id"], {"name": "R"}, 201)
        it = self.call("POST", "/api/rooms/%d/items" % room["id"], {"name": "X", "similar_url": self.shop.url + "/withpic"}, 201)
        r = self.call("POST", "/api/link-info", {"url": self.shop.url + "/withpic"}, 200)
        self.assertEqual(r["image"], self.shop.url + "/pic.png")
        r = self.call("POST", "/api/link-info", {"url": self.shop.url + "/ok"}, 200)
        self.assertEqual(r["image"], "")
        out = self.call("POST", "/api/items/%d/link-image" % it["id"], {}, 200)
        self.assertTrue(out["similar_image"].endswith(".png"))
        first = out["similar_image"]
        with urllib.request.urlopen(self.base + "/images/" + first) as resp:
            self.assertEqual(resp.read()[:4], b"\x89PNG")
        out = self.call("POST", "/api/items/%d/link-image" % it["id"], {"url": self.shop.url + "/ldpic"}, 200)   # json-ld image list
        self.assertNotEqual(out["similar_image"], first)
        self.assertFalse(os.path.exists(os.path.join(server.images_dir(), first)))   # old picture removed
        self.call("POST", "/api/items/%d/link-image" % it["id"], {"url": self.shop.url + "/none"}, 404)
        self.call("POST", "/api/items/%d/link-image" % it["id"], {"url": self.shop.url + "/badpic"}, 400)
        server.ALLOW_PRIVATE_FETCH = False
        self.call("POST", "/api/items/%d/link-image" % it["id"], {"url": self.shop.url + "/withpic"}, 400)   # private address refused
        no_link = self.call("POST", "/api/rooms/%d/items" % room["id"], {"name": "Y"}, 201)
        self.call("POST", "/api/items/%d/link-image" % no_link["id"], {}, 400)

    def test_price_from_meta_and_redirect(self):
        r = self.call("POST", "/api/link-info", {"url": self.shop.url + "/meta"}, 200)
        self.assertEqual((r["price"], r["currency"], r["source"]), (1299.5, "EUR", "meta"))
        r = self.call("POST", "/api/link-info", {"url": self.shop.url + "/redir"}, 200)
        self.assertEqual(r["price"], 349.99)
        self.assertTrue(r["url"].endswith("/ok"))

    def test_no_price_is_not_guessed(self):
        r = self.call("POST", "/api/link-info", {"url": self.shop.url + "/none"}, 200)
        self.assertIsNone(r["price"])

    def test_big_page_is_capped_and_errors_are_friendly(self):
        self.call("POST", "/api/link-info", {"url": self.shop.url + "/big"}, 200)     # truncated, still answers
        e = self.call("POST", "/api/link-info", {"url": self.shop.url + "/forbidden"}, 502)
        self.assertIn("by hand", e["error"])
        self.assertIn("not found", self.call("POST", "/api/link-info", {"url": self.shop.url + "/nope"}, 400)["error"])
        self.assertIn("not a web page", self.call("POST", "/api/link-info", {"url": self.shop.url + "/img"}, 400)["error"])
        self.assertIn("not a web page", self.call("POST", "/api/link-info", {"url": self.shop.url + "/bad"}, 400)["error"])
        for bad in ("javascript:alert(1)", "ftp://x.example/", "file:///etc/passwd", "not a url", ""):
            self.call("POST", "/api/link-info", {"url": bad}, 400)

    def test_synthetic_embedded_product_data(self):
        """A page that keeps its price in embedded JSON (shaped like Next.js __NEXT_DATA__); the data is made up."""
        r = self.call("POST", "/api/link-info", {"url": self.shop.url + "/ip/Instant-Pot/45918917?classType=REGULAR"}, 200)
        self.assertEqual((r["price"], r["currency"], r["source"]), (99.0, "USD", "page-data"))   # not the carousel's $5
        # same page, but the link's product id is not in the data: another product's price must not be borrowed
        r = self.call("POST", "/api/link-info", {"url": self.shop.url + "/ip/Other/22222222"}, 200)
        self.assertIsNone(r["price"])

    def test_robot_checks_are_reported_as_such(self):
        for path in ("/robot", "/blocked412"):
            e = self.call("POST", "/api/link-info", {"url": self.shop.url + path}, 502)
            self.assertIn("robot check", e["error"], path)
        e = self.call("POST", "/api/link-info", {"url": self.shop.url + "/blocked403"}, 502)
        self.assertIn("did not allow", e["error"])

    def test_item_id_from_url(self):
        import linkinfo
        f = linkinfo.item_id_from_url
        self.assertEqual(f("https://www.walmart.com/ip/Instant-Pot-DUO60-6-Qt/45918917?classType=REGULAR&from=/search"), "45918917")
        self.assertEqual(f("https://www.amazon.com/Some-Name/dp/B00FLYWNYQ/ref=sr_1_1"), "b00flywnyq")
        self.assertEqual(f("https://shop.example/item/12345"), "12345")
        self.assertIsNone(f("https://shop.example/about")); self.assertIsNone(f(""))

    def test_private_addresses_are_refused(self):
        server.ALLOW_PRIVATE_FETCH = False
        for u in (self.shop.url + "/ok", "http://localhost:%s/ok" % self.shop.url.rsplit(":", 1)[1],
                  "http://169.254.169.254/latest/meta-data/", "http://192.168.1.1/", "http://10.0.0.5/",
                  "http://[::1]/", "http://0.0.0.0/"):
            e = self.call("POST", "/api/link-info", {"url": u}, 400)
            self.assertIn("private or local", e["error"], u)


class TypeBumpTest(unittest.TestCase):
    """tools/type_bump.py: font sizes in the vendored design system are raised without editing it."""
    @classmethod
    def setUpClass(cls):
        import sys
        cls.root = os.path.dirname(os.path.abspath(__file__))
        sys.path.insert(0, os.path.join(cls.root, "tools"))
        import type_bump
        cls.tb = type_bump

    def test_rules(self):
        css = """body { font: 14px/1.45 var(--font); }
h1 { font-size: 18px; }
.a, .b > c { font-size:12px !important; color: red }
@media (max-width: 860px) { .x { font-size: 13px } .y { color: blue } }
@keyframes k { from { font-size: 10px } }
.u { font-size: 1.2em } .v { font: inherit } .w { font: italic 600 13px/18px Arial }
/* gone { font-size: 99px } */
.q { content: "}"; font-size: 11px }"""
        out = self.tb.bump_css(css, 1)
        for want in ("body { font: 15px/1.45 var(--font); }", "h1 { font-size: 19px; }",
                     ".a, .b > c { font-size: 13px !important; }", "@media (max-width: 860px) {\n  .x { font-size: 14px; }\n}",
                     ".w { font: italic 600 14px/18px Arial; }", ".q { font-size: 12px; }"):
            self.assertIn(want, out)
        for nope in (".y", "keyframes", ".u", ".v", "99", "font-size: 10"):
            self.assertNotIn(nope, out)
        self.assertIn("h1 { font-size: 20px; }", self.tb.bump_css(css, 2))
        self.assertIn("h1 { font-size: 17.5px; }", self.tb.bump_css(css, -0.5))

    def test_committed_file_is_current(self):
        """If static/ds/ is re-copied, regenerate static/type-bump.css with: python3 tools/type_bump.py"""
        with open(os.path.join(self.root, "static", "type-bump.css"), encoding="utf-8") as fh:
            self.assertEqual(fh.read(), self.tb.build(os.path.join(self.root, "static", "ds"), 1.0))


if __name__ == "__main__":
    unittest.main(verbosity=1)
