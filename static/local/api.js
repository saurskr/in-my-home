// The in-browser version of the Home Inventory API (a port of server.py).
// Same paths, same validation, same answers - but the data lives in this browser (see store.js).
// Used when the page is opened without the Python server (GitHub Pages, an installed phone app).

import * as ai from "./ai.js";
import {buildPdf} from "./pdf.js";
import {sampleData} from "./sample.js";

export const ITEM_TYPES = ["Appliance", "Furniture", "Electronics", "Memorabilia", "Media", "Collectible", "Clothing"];
export const SOURCES = ["manual", "template", "vision"];
export const CONDITIONS = ["New", "Refurbished", "Used - like new", "Used - good", "Used - fair", "Used - poor"];
export const DESCRIPTION_MAX = 250, MODEL_MAX = 60, CATEGORY_MAX = 40, MAX_CUSTOM_CATEGORIES = 50, MAX_RIDERS = 5;
export const US_STATES = [["AL", "Alabama"], ["AK", "Alaska"], ["AZ", "Arizona"], ["AR", "Arkansas"], ["CA", "California"], ["CO", "Colorado"],
  ["CT", "Connecticut"], ["DE", "Delaware"], ["DC", "District of Columbia"], ["FL", "Florida"], ["GA", "Georgia"], ["HI", "Hawaii"], ["ID", "Idaho"],
  ["IL", "Illinois"], ["IN", "Indiana"], ["IA", "Iowa"], ["KS", "Kansas"], ["KY", "Kentucky"], ["LA", "Louisiana"], ["ME", "Maine"], ["MD", "Maryland"],
  ["MA", "Massachusetts"], ["MI", "Michigan"], ["MN", "Minnesota"], ["MS", "Mississippi"], ["MO", "Missouri"], ["MT", "Montana"], ["NE", "Nebraska"],
  ["NV", "Nevada"], ["NH", "New Hampshire"], ["NJ", "New Jersey"], ["NM", "New Mexico"], ["NY", "New York"], ["NC", "North Carolina"],
  ["ND", "North Dakota"], ["OH", "Ohio"], ["OK", "Oklahoma"], ["OR", "Oregon"], ["PA", "Pennsylvania"], ["RI", "Rhode Island"],
  ["SC", "South Carolina"], ["SD", "South Dakota"], ["TN", "Tennessee"], ["TX", "Texas"], ["UT", "Utah"], ["VT", "Vermont"], ["VA", "Virginia"],
  ["WA", "Washington"], ["WV", "West Virginia"], ["WI", "Wisconsin"], ["WY", "Wyoming"], ["AS", "American Samoa"], ["GU", "Guam"],
  ["MP", "Northern Mariana Islands"], ["PR", "Puerto Rico"], ["VI", "U.S. Virgin Islands"], ["AA", "Armed Forces Americas"],
  ["AE", "Armed Forces Europe"], ["AP", "Armed Forces Pacific"]];
const STATE_CODES = new Set(US_STATES.map(s => s[0]));
const ADDRESS_FIELDS = ["street", "street2", "city", "state", "zip"];
export const ROOM_TEMPLATES = {
  "Kitchen": [["Toaster", "Appliance"], ["Microwave", "Appliance"], ["Refrigerator", "Appliance"], ["Blender", "Appliance"], ["Crockpot", "Appliance"]],
  "Master Bedroom": [["Bed", "Furniture"], ["Dresser", "Furniture"], ["Nightstand", "Furniture"], ["Nightstand", "Furniture"]],
  "Living Room": [["Sofa", "Furniture"], ["Television", "Electronics"], ["Bookcase", "Furniture"], ["Coffee Table", "Furniture"]],
  "Dining Room": [["Dining Table", "Furniture"], ["Dining Chair", "Furniture"], ["China Cabinet", "Furniture"]],
  "Guest Bedroom": [["Bed", "Furniture"], ["Dresser", "Furniture"], ["Nightstand", "Furniture"]],
  "Master Bathroom": [["Hair Dryer", "Appliance"], ["Scale", "Appliance"]],
  "Guest Bathroom": [["Hair Dryer", "Appliance"]],
  "Garage": [["Lawn Mower", "Appliance"], ["Workbench", "Furniture"], ["Tool Set", "Appliance"]],
};
const IMAGE_TYPES = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/gif": "gif"};
export const DEFAULT_SETTINGS = {ollama_url: "http://localhost:11434", ollama_model: "qwen2.5vl:7b", anthropic_key: "", anthropic_model: "claude-haiku-5-5", google_client_id: ""};

export class ApiError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}
const bad = (m, s = 400) => new ApiError(s, m);
const lc = s => String(s || "").toLowerCase();
const byName = (a, b) => (lc(a.name) < lc(b.name) ? -1 : lc(a.name) > lc(b.name) ? 1 : a.id - b.id);
const r2 = n => Math.round((n + Number.EPSILON) * 100) / 100;
const sum = (xs, f) => r2(xs.reduce((a, x) => a + (f(x) || 0), 0));
const pad = (n, w = 2) => String(n).padStart(w, "0");
const ymdOf = d => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const isoNow = d => d.toISOString().replace(/\.\d{3}Z$/, "Z");

// ---------- validation ----------
function text(b, key, required = false) {
  const v = b[key];
  if (v === undefined || v === null) { if (required) throw bad(`${key} is required`); return ""; }
  if (typeof v !== "string") throw bad(`${key} must be a string`);
  const t = v.trim();
  if (required && !t) throw bad(`${key} is required`);
  return t;
}
function money(b, key) {
  const v = b[key];
  if (v === undefined || v === null || v === "") return null;
  if (typeof v === "boolean" || !["number", "string"].includes(typeof v)) throw bad(`${key} must be a number`);
  const f = typeof v === "string" ? (v.trim() === "" ? NaN : Number(v)) : v;
  if (Number.isNaN(f)) throw bad(`${key} must be a number`);
  if (f < 0 || !Number.isFinite(f)) throw bad(`${key} must be a non-negative number`);
  return r2(f);
}
const urlField = (b, key) => { const v = text(b, key); if (v && !/^https?:\/\/\S+$/i.test(v)) throw bad(`${key} must start with http:// or https://`); return v; };
const modelField = b => { const v = text(b, "model_number"); if (v.length > MODEL_MAX) throw bad(`model_number must be ${MODEL_MAX} characters or fewer`); return v; };
const isInt = v => Number.isInteger(v) && typeof v === "number";

export function formatAddress(h) {
  const line1 = [h.street, h.street2].filter(Boolean).join(", ");
  const sz = [h.state, h.zip].filter(Boolean).join(" ");
  const line2 = [h.city, sz].filter(Boolean).join(", ");
  return [line1, line2].filter(Boolean).join(", ");
}

export function createLocalApi({store, fetchImpl = (...a) => fetch(...a), clock = () => new Date(), drive = null, imageMaker = null} = {}) {
  let db = null, images = new Map(), ready = null;
  const today = () => ymdOf(clock());
  const stamp = () => isoNow(clock());
  const blank = () => ({seq: {homes: 0, rooms: 0, items: 0}, homes: [], rooms: [], items: [], insurance: {}, categories: [], settings: {}, meta: {demo: false}});

  function init() {
    if (!ready) ready = (async () => {
      const loaded = await store.load();
      db = loaded.db || blank(); images = loaded.images;
      for (const k of Object.keys(blank())) if (db[k] === undefined) db[k] = blank()[k];
    })();
    return ready;
  }

  // ----- helpers over the data -----
  const allTypes = () => [...ITEM_TYPES, ...db.categories];
  function checkType(v) {
    if (v === undefined || v === null || v === "") return null;
    const m = allTypes().find(t => lc(t) === lc(v));
    if (!m) throw bad("item_type must be one of: " + allTypes().join(", "));
    return m;
  }
  const dateField = (b, key) => {
    const v = text(b, key); if (!v) return "";
    const m = /^(\d{4})-(\d\d)-(\d\d)$/.exec(v); const d = m && new Date(+m[1], +m[2] - 1, +m[3]);
    if (!m || d.getFullYear() !== +m[1] || d.getMonth() !== +m[2] - 1 || d.getDate() !== +m[3]) throw bad(`${key} must be a date like 2026-03-01`);
    if (v > today()) throw bad(`${key} cannot be in the future`);
    return v;
  };
  const conditionField = b => { const v = text(b, "purchased_condition"); if (v && !CONDITIONS.includes(v)) throw bad("purchased_condition must be one of: " + CONDITIONS.join(", ")); return v; };
  const descriptionField = (b, current) => {
    const v = text(b, "description");
    if (v.length > DESCRIPTION_MAX && v !== current) throw bad(`description must be ${DESCRIPTION_MAX} characters or fewer (it is ${v.length})`);
    return v;
  };
  const getOr404 = (list, id, what) => { const r = list.find(x => x.id === id); if (!r) throw bad(`${what} not found`, 404); return r; };
  const itemOut = r => ({...r, scan_state: r.scan_state || "", scan_note: r.scan_note || "", replacement_date: (r.price_updated_at || "").slice(0, 10)});

  function insOut(row, homeId) {
    if (!row) return {insurer: "", website: "", policy_number: "", renewal_date: "", contact: "", notes: "", riders: [], dwelling_coverage: null,
      contents_coverage: null, liability_coverage: null, deductible: null, annual_premium: null, home_id: homeId, configured: false, updated_at: null};
    return {...row, riders: Array.isArray(row.riders) ? row.riders : [], configured: true};
  }
  function homeOut(h) {
    const rooms = db.rooms.filter(r => r.home_id === h.id), roomIds = new Set(rooms.map(r => r.id)), items = db.items.filter(i => roomIds.has(i.room_id));
    const structured = formatAddress(h), legacy = h.address || "";
    return {...h, room_count: rooms.length, item_count: items.length, scan_count: items.filter(i => i.scan_state).length, total_purchased: sum(items, i => i.purchased_value),
      total_replacement: sum(items, i => i.replacement_value), legacy_address: structured ? "" : legacy, address: structured || legacy,
      insurance: insOut(db.insurance[h.id], h.id)};
  }
  function roomOut(r) {
    const items = db.items.filter(i => i.room_id === r.id);
    return {...r, item_count: items.length, total_purchased: sum(items, i => i.purchased_value), total_replacement: sum(items, i => i.replacement_value)};
  }
  function addressValues(b, cur) {
    const out = {}; for (const k of ADDRESS_FIELDS) out[k] = cur[k] || "";
    for (const k of ["street", "street2", "city"]) if (k in b) { const v = text(b, k); if (v.length > 120) throw bad(`${k} is too long`); out[k] = v; }
    if ("state" in b) { const v = text(b, "state").toUpperCase(); if (v && !STATE_CODES.has(v)) throw bad("state must be a 2-letter US state or territory code"); out.state = v; }
    if ("zip" in b) { const v = text(b, "zip"); if (v && !/^\d{5}(-?\d{4})?$/.test(v)) throw bad("zip must be 12345 or 12345-6789"); out.zip = /^\d{9}$/.test(v) ? v.slice(0, 5) + "-" + v.slice(5) : v; }
    return out;
  }
  function newItem(roomId, b, source = "manual") {
    const purchased = money(b, "purchased_value"), replacement = money(b, "replacement_value");
    const rdate = dateField(b, "replacement_date");
    const pu = rdate ? rdate + "T00:00:00Z" : (replacement !== null || b.price_confirmed === true ? stamp() : null);
    const scanState = text(b, "scan_state");
    if (!["", "pending"].includes(scanState)) throw bad("scan_state must be empty or pending");
    const src = b.source !== undefined ? b.source : source;
    if (!SOURCES.includes(src)) throw bad("source must be one of: " + SOURCES.join(", "));
    const row = {name: text(b, "name", true), description: descriptionField(b), brand: text(b, "brand"), model_number: modelField(b),
      purchased_date: dateField(b, "purchased_date"), purchased_condition: conditionField(b), purchased_value: purchased ?? 0,
      replacement_value: replacement ?? (purchased ?? 0), similar_url: urlField(b, "similar_url"), item_type: checkType(b.item_type), source: src, price_updated_at: pu, scan_state: scanState, scan_note: ""};
    const id = ++db.seq.items;
    const item = {id, inv_no: "INV-" + pad(id, 6), room_id: roomId, ...row, image: null, similar_image: null};
    db.items.push(item);
    return item;
  }
  async function dropImages(names) { for (const n of names) if (n) { images.delete(n); await store.delImage(n); } }
  async function storeImage(item, kind, dataUrl) {
    const m = /^data:([\w/+.-]+);base64,(.*)$/s.exec(dataUrl || "");
    if (!m || !IMAGE_TYPES[m[1]]) throw bad("data must be a base64 data URL (png, jpeg, webp, gif)");
    if (!m[2]) throw bad("empty image");
    const col = kind === "photo" ? "image" : "similar_image", old = item[col];
    const name = `${item.id}-${kind}-${Math.random().toString(16).slice(2, 10)}.${IMAGE_TYPES[m[1]]}`;
    images.set(name, dataUrl); await store.putImage(name, dataUrl);
    item[col] = name; await dropImages([old]);
  }
  const imagesOfItems = items => items.flatMap(i => [i.image, i.similar_image]);

  // ----- report -----
  function gather(homeId) {
    const home = homeOut(getOr404(db.homes, homeId, "home"));
    const rooms = db.rooms.filter(r => r.home_id === homeId).sort(byName).map(r => {
      const items = db.items.filter(i => i.room_id === r.id).sort(byName).map(itemOut);
      return {...r, items, total_purchased: sum(items, i => i.purchased_value), total_replacement: sum(items, i => i.replacement_value)};
    });
    return {home, rooms, item_count: rooms.reduce((a, r) => a + r.items.length, 0), total_purchased: sum(rooms, r => r.total_purchased), total_replacement: sum(rooms, r => r.total_replacement)};
  }
  function makeReport(homeId, q) {
    const data = gather(homeId);
    const photos = ["1", "true", "yes"].includes(q.get("photos") || "0");
    const showPrice = !["1", "true", "yes"].includes(q.get("hide_purchase_price") || "0");
    const bytes = buildPdf(data, {withPhotos: photos, showPrice, getImage: n => images.get(n), today: today()});
    const slug = data.home.name.replace(/[^A-Za-z0-9]+/g, "-").replace(/^-|-$/g, "").toLowerCase() || "home";
    return {bytes, filename: `${slug}-inventory${photos ? "-photos" : ""}${showPrice ? "" : "-no-purchase-price"}-${today()}.pdf`, ctype: "application/pdf"};
  }

  // ----- settings (kept in this browser; the key is never sent anywhere except to Anthropic when you choose Claude) -----
  const settings = () => ({...DEFAULT_SETTINGS, ...db.settings});
  function publicSettings() {
    const s = settings();
    return {ollama: {url: s.ollama_url, model: s.ollama_model},
      anthropic: {configured: !!s.anthropic_key, key_source: s.anthropic_key ? "settings" : null, model: s.anthropic_model},
      google: {client_id: s.google_client_id, secret_configured: true, connected: !!s.google_client_id, web: true},
      defaults: {ollama_url: DEFAULT_SETTINGS.ollama_url, ollama_model: DEFAULT_SETTINGS.ollama_model, anthropic_model: DEFAULT_SETTINGS.anthropic_model},
      mode: "local", demo: !!db.meta.demo};
  }

  // ---------- handlers: (b = body, q = URLSearchParams, ...ids) -> data | {status, data} | {raw} ----------
  const H = {};
  H.meta = () => ({item_types: allTypes(), builtin_types: ITEM_TYPES, conditions: CONDITIONS, sources: SOURCES,
    us_states: US_STATES.map(([code, name]) => ({code, name})),
    room_templates: Object.fromEntries(Object.entries(ROOM_TEMPLATES).map(([k, v]) => [k, v.map(([name, item_type]) => ({name, item_type}))]))});

  H.categoryCreate = b => {
    const name = text(b, "name", true).split(/\s+/).join(" ");
    if (name.length > CATEGORY_MAX) throw bad(`Category name is limited to ${CATEGORY_MAX} characters.`);
    if ([...allTypes(), "Other", "Consumable"].some(t => lc(t) === lc(name))) throw bad(`A category named '${name}' already exists.`, 409);
    if (db.categories.length >= MAX_CUSTOM_CATEGORIES) throw bad(`You can add up to ${MAX_CUSTOM_CATEGORIES} custom categories.`);
    db.categories.push(name); db.categories.sort((a, b2) => (lc(a) < lc(b2) ? -1 : 1));
    return {status: 201, data: {name, item_types: allTypes()}};
  };
  H.categoryDelete = (b, q, name) => {
    const hit = db.categories.find(c => c === decodeURIComponent(name));
    if (!hit) throw bad("category not found (built-in categories cannot be removed)", 404);
    const used = db.items.filter(i => lc(i.item_type) === lc(hit)).length;
    if (used) throw bad(`${used} item${used === 1 ? "" : "s"} still use '${hit}'. Change them first.`, 409);
    db.categories = db.categories.filter(c => c !== hit);
    return {deleted: hit, item_types: allTypes()};
  };

  H.homeList = () => db.homes.map(homeOut).sort(byName);
  H.homeCreate = b => {
    const name = text(b, "name", true), a = addressValues(b, {});
    const legacy = formatAddress(a) ? "" : text(b, "address");
    const h = {id: ++db.seq.homes, name, address: legacy, ...a}; db.homes.push(h);
    return {status: 201, data: homeOut(h)};
  };
  H.homeGet = (b, q, id) => homeOut(getOr404(db.homes, id, "home"));
  H.homeUpdate = (b, q, id) => {
    const h = getOr404(db.homes, id, "home");
    const name = "name" in b ? text(b, "name", true) : h.name, a = addressValues(b, h);
    let legacy = "address" in b ? text(b, "address") : (h.address || "");
    if (formatAddress(a)) legacy = "";
    Object.assign(h, {name, address: legacy, ...a});
    return homeOut(h);
  };
  H.homeDelete = async (b, q, id) => {
    getOr404(db.homes, id, "home");
    const roomIds = new Set(db.rooms.filter(r => r.home_id === id).map(r => r.id)), gone = db.items.filter(i => roomIds.has(i.room_id));
    db.items = db.items.filter(i => !roomIds.has(i.room_id)); db.rooms = db.rooms.filter(r => r.home_id !== id);
    db.homes = db.homes.filter(h => h.id !== id); delete db.insurance[id];
    await dropImages(imagesOfItems(gone));
    return {deleted: id};
  };

  H.roomList = (b, q, homeId) => { getOr404(db.homes, homeId, "home"); return db.rooms.filter(r => r.home_id === homeId).sort(byName).map(roomOut); };
  H.roomCreate = (b, q, homeId) => {
    getOr404(db.homes, homeId, "home");
    const name = text(b, "name", true), description = text(b, "description");
    let tmpl = null;
    if (b.prefill) {
      const want = b.template ? text(b, "template") : Object.keys(ROOM_TEMPLATES).find(k => lc(k) === lc(name));
      tmpl = Object.keys(ROOM_TEMPLATES).find(k => want && lc(k) === lc(want));
      if (!tmpl) throw bad(`no room template for '${name}'`);
    }
    const room = {id: ++db.seq.rooms, home_id: homeId, name, description}; db.rooms.push(room);
    if (tmpl) for (const [n, t] of ROOM_TEMPLATES[tmpl]) newItem(room.id, {name: n, item_type: t}, "template");
    return {status: 201, data: roomOut(room)};
  };
  H.roomGet = (b, q, id) => roomOut(getOr404(db.rooms, id, "room"));
  H.roomUpdate = (b, q, id) => {
    const r = getOr404(db.rooms, id, "room");
    const name = "name" in b ? text(b, "name", true) : r.name, description = "description" in b ? text(b, "description") : r.description;
    let homeId = r.home_id;
    if ("home_id" in b) { if (!isInt(b.home_id)) throw bad("home_id must be an integer"); getOr404(db.homes, b.home_id, "home"); homeId = b.home_id; }
    Object.assign(r, {name, description, home_id: homeId});
    return roomOut(r);
  };
  H.roomDelete = async (b, q, id) => {
    getOr404(db.rooms, id, "room");
    const gone = db.items.filter(i => i.room_id === id); db.items = db.items.filter(i => i.room_id !== id); db.rooms = db.rooms.filter(r => r.id !== id);
    await dropImages(imagesOfItems(gone));
    return {deleted: id};
  };

  H.itemList = (b, q, roomId) => { getOr404(db.rooms, roomId, "room"); return db.items.filter(i => i.room_id === roomId).sort(byName).map(itemOut); };
  H.itemCreate = (b, q, roomId) => { getOr404(db.rooms, roomId, "room"); return {status: 201, data: itemOut(newItem(roomId, b))}; };
  H.itemGet = (b, q, id) => itemOut(getOr404(db.items, id, "item"));
  H.itemUpdate = (b, q, id) => {
    const cur = getOr404(db.items, id, "item"), f = {...cur};
    if ("name" in b) f.name = text(b, "name", true);
    if ("brand" in b) f.brand = text(b, "brand");
    if ("description" in b) f.description = descriptionField(b, cur.description);
    if ("model_number" in b) f.model_number = modelField(b);
    if ("purchased_date" in b) f.purchased_date = dateField(b, "purchased_date");
    if ("purchased_condition" in b) f.purchased_condition = conditionField(b);
    if ("similar_url" in b) f.similar_url = urlField(b, "similar_url");
    if ("item_type" in b) f.item_type = checkType(b.item_type);
    if ("purchased_value" in b) { const v = money(b, "purchased_value"); f.purchased_value = v ?? 0; }
    if ("replacement_value" in b) {
      const v = money(b, "replacement_value"), nv = v ?? f.purchased_value;
      if (nv !== cur.replacement_value) f.price_updated_at = stamp();
      f.replacement_value = nv;
    }
    if (b.price_confirmed === true) f.price_updated_at = stamp();
    if ("replacement_date" in b) { const rd = dateField(b, "replacement_date"); if (rd && rd !== (f.price_updated_at || "").slice(0, 10)) f.price_updated_at = rd + "T00:00:00Z"; }
    if ("scan_state" in b) { const st = text(b, "scan_state"); if (!["", "pending"].includes(st)) throw bad("scan_state must be empty or pending"); f.scan_state = st; f.scan_note = ""; }
    if ("source" in b) { if (!SOURCES.includes(b.source)) throw bad("invalid source"); f.source = b.source; }
    if ("room_id" in b) { if (!isInt(b.room_id)) throw bad("room_id must be an integer"); getOr404(db.rooms, b.room_id, "room"); f.room_id = b.room_id; }
    Object.assign(cur, f);
    return itemOut(cur);
  };
  H.itemDelete = async (b, q, id) => {
    const cur = getOr404(db.items, id, "item"); db.items = db.items.filter(i => i.id !== id);
    await dropImages([cur.image, cur.similar_image]); return {deleted: id};
  };
  H.itemImage = async (b, q, id) => {
    const cur = getOr404(db.items, id, "item"), kind = q.get("kind") || "photo";
    if (!["photo", "similar"].includes(kind)) throw bad("kind must be photo or similar");
    await storeImage(cur, kind, b.data); return itemOut(cur);
  };
  // ----- photos first, AI later: pending items are read when the AI is reachable, then reclassified -----
  H.scanQueue = () => db.items.filter(i => i.scan_state).map(i => {
    const room = db.rooms.find(r => r.id === i.room_id) || {}, home = db.homes.find(h => h.id === room.home_id) || {};
    return {...itemOut(i), room_name: room.name || "", home_id: room.home_id, home_name: home.name || ""};
  });
  H.itemScan = async (b, q, id) => {
    const cur = getOr404(db.items, id, "item");
    if (!cur.image) throw bad("This item has no photo to read.");
    const data = images.get(cur.image);
    if (!data) throw bad("The photo file is missing.", 404);
    const provider = b.provider || "ollama", types = allTypes();
    const call = fn => aiCall(fn)({});
    const {kind} = await call(() => ai.classify(fetchImpl, settings(), {data, provider}));
    const out = {kind, item: null, created: [], deleted: null};
    if (kind === "item") {
      const r = await call(() => ai.identify(fetchImpl, settings(), {data, provider}, types));
      Object.assign(cur, {name: r.name, brand: r.brand, model_number: r.model_number, item_type: r.item_type || cur.item_type, description: r.description,
        source: "vision", scan_state: "review", scan_note: `Identified by ${r.model}. Check the details.`});
      out.item = itemOut(cur);
    } else if (kind === "receipt") {
      const rec = await call(() => ai.receipt(fetchImpl, settings(), {data, provider}, [...types, "Consumable", "Other"]));
      if (!rec.items.length) { Object.assign(cur, {scan_state: "failed", scan_note: "No line items could be read from this receipt."}); out.item = itemOut(cur); return out; }
      const bought = rec.date && rec.date <= today() ? rec.date : "";
      const note = "From a receipt" + (rec.store ? " at " + rec.store : "") + ".";
      let first = true;
      for (const ln of rec.items) {
        const unit = ln.unit_price !== null ? ln.unit_price : (ln.total_price || 0);
        for (let n = 0; n < Math.min(ln.quantity, 20); n++) {
          const it = newItem(cur.room_id, {name: ln.description.slice(0, 120), purchased_value: unit, purchased_date: bought, item_type: types.includes(ln.category) ? ln.category : "", source: "vision"});
          Object.assign(it, {scan_state: "review", scan_note: note});
          if (first) { it.image = cur.image; first = false; }
          out.created.push(it.id);
        }
      }
      cur.image = null; db.items = db.items.filter(i => i.id !== id); out.deleted = id;
    } else {
      Object.assign(cur, {scan_state: "failed", scan_note: "This photo is not an item or a receipt."}); out.item = itemOut(cur);
    }
    return out;
  };
  const needServer = () => { throw bad("Reading a price or picture from a web link needs the Python app on your computer. In this version, type the price in by hand.", 501); };
  H.linkInfo = needServer; H.linkImage = needServer;

  H.insGet = (b, q, homeId) => { getOr404(db.homes, homeId, "home"); return insOut(db.insurance[homeId], homeId); };
  H.insPut = (b, q, homeId) => {
    getOr404(db.homes, homeId, "home");
    const cur = insOut(db.insurance[homeId], homeId);
    for (const k of ["insurer", "policy_number", "contact", "notes"]) if (k in b) cur[k] = text(b, k);
    if ("website" in b) cur.website = urlField(b, "website");
    for (const k of ["dwelling_coverage", "contents_coverage", "liability_coverage", "deductible", "annual_premium"]) if (k in b) cur[k] = money(b, k);
    if ("riders" in b) {
      if (!Array.isArray(b.riders)) throw bad("riders must be a list");
      if (b.riders.length > MAX_RIDERS) throw bad(`At most ${MAX_RIDERS} riders are allowed`);
      cur.riders = b.riders.map(r => {
        if (!r || typeof r !== "object" || Array.isArray(r)) throw bad("each rider must be an object with a name and limit");
        const name = text(r, "name"); if (!name) throw bad("Each rider needs a name"); if (name.length > 80) throw bad("rider name is too long");
        return {name, limit: money(r, "limit")};
      });
    }
    if ("renewal_date" in b) {
      const d = text(b, "renewal_date");
      if (d) { const m = /^(\d{4})-(\d\d)-(\d\d)$/.exec(d); const dt = m && new Date(+m[1], +m[2] - 1, +m[3]);
        if (!m || dt.getMonth() !== +m[2] - 1 || dt.getDate() !== +m[3]) throw bad("renewal_date must be YYYY-MM-DD"); }
      cur.renewal_date = d;
    }
    const {configured, ...row} = cur; db.insurance[homeId] = {...row, home_id: homeId, updated_at: stamp()};
    return insOut(db.insurance[homeId], homeId);
  };
  H.insDelete = (b, q, homeId) => { getOr404(db.homes, homeId, "home"); delete db.insurance[homeId]; return {deleted: homeId}; };

  H.reportJson = (b, q, id) => gather(id);
  H.reportPdf = (b, q, id) => { getOr404(db.homes, id, "home"); return {raw: makeReport(id, q)}; };
  H.reportDrive = async (b, q, id) => {
    getOr404(db.homes, id, "home");
    if (!drive) throw bad("Saving to Google Drive is not available here.", 501);
    const s = settings();
    if (!s.google_client_id) throw bad("Add your Google client id in Settings first.");
    const r = makeReport(id, q);
    try { return await drive.upload({clientId: s.google_client_id, filename: r.filename, bytes: r.bytes}); }
    catch (e) { throw bad(e.message, e.status || 502); }
  };

  H.settingsGet = () => publicSettings();
  H.settingsPut = (b) => {
    const s = {...db.settings};
    if ("ollama_url" in b) { const u = text(b, "ollama_url").replace(/\/+$/, ""); if (u && !/^https?:\/\/[^\s/]+$/i.test(u)) throw bad("ollama_url must look like http://localhost:11434"); s.ollama_url = u; }
    for (const k of ["ollama_model", "anthropic_model"]) if (k in b) { const v = text(b, k); if (v.length > 100 || /\s/.test(v)) throw bad(`${k} is not a valid model name`); s[k] = v; }
    if ("anthropic_key" in b) { const v = text(b, "anthropic_key"); if (v && (v.length < 20 || /\s/.test(v))) throw bad("That does not look like an API key"); if (v) s.anthropic_key = v; else delete s.anthropic_key; }
    if ("google_client_id" in b) { const v = text(b, "google_client_id"); if (v && (v.length > 200 || /\s/.test(v))) throw bad("That does not look like a Google client id"); s.google_client_id = v; }
    db.settings = s; return publicSettings();
  };
  H.googleDisconnect = () => { const s = {...db.settings}; delete s.google_client_id; db.settings = s; return publicSettings(); };
  H.providersStatus = async () => ({ollama: await ai.ollamaStatus(fetchImpl, settings()), anthropic: {configured: !!settings().anthropic_key, model: settings().anthropic_model}});
  H.ollamaModels = () => ai.ollamaModels(fetchImpl, settings());
  const aiCall = fn => async b => { try { return await fn(b); } catch (e) { throw e instanceof ApiError ? e : bad(e.message, e.status || 502); } };
  H.identify = aiCall(b => ai.identify(fetchImpl, settings(), b, allTypes()));
  H.classify = aiCall(b => ai.classify(fetchImpl, settings(), b));
  H.receipt = aiCall(b => ai.receipt(fetchImpl, settings(), b, [...allTypes(), "Consumable", "Other"]));

  // ----- backup, demo data, erase -----
  async function exportAll() {
    const imgs = {}; for (const [k, v] of images) imgs[k] = v;
    const {settings: _keep, ...rest} = db;     // the saved API key and client id stay on this device
    return {app: "home-inventory", version: 1, exported: stamp(), db: rest, images: imgs};
  }
  async function importAll(obj) {
    if (!obj || obj.app !== "home-inventory" || !obj.db || !Array.isArray(obj.db.homes) || !Array.isArray(obj.db.rooms) || !Array.isArray(obj.db.items))
      throw bad("That file is not a Home Inventory backup.");
    const next = {...blank(), ...obj.db, settings: db.settings};
    for (const k of ["seq"]) next[k] = {homes: 0, rooms: 0, items: 0, ...(obj.db[k] || {})};
    for (const k of ["homes", "rooms", "items"]) next.seq[k] = Math.max(next.seq[k], ...next[k].map(x => x.id || 0), 0);
    const nextImages = new Map(Object.entries(obj.images || {}).filter(([, v]) => typeof v === "string" && v.startsWith("data:image/")));
    await store.clear(); db = next; images = nextImages;
    for (const [k, v] of images) await store.putImage(k, v);
    await store.saveDb(db);
  }
  async function loadSample() {
    if (db.homes.length) throw bad("Sample data is only loaded into an empty inventory.");
    const s = sampleData(today());
    const homeId = H.homeCreate(s.home).data.id;
    H.insPut(s.insurance, null, homeId);
    for (const r of s.rooms) {
      const room = H.roomCreate({name: r.name, description: r.description || ""}, null, homeId).data;
      for (const it of r.items) {
        const item = newItem(room.id, it.fields, "manual");
        item.price_updated_at = it.replacement_date ? it.replacement_date + "T00:00:00Z" : null;   // null = the price was never dated
        if (imageMaker && it.picture) { const data = await imageMaker(it.picture); if (data) await storeImage(item, "photo", data); }
      }
    }
    db.meta.demo = true;
  }
  async function eraseAll() { await store.clear(); db = blank(); images = new Map(); }

  // ---------- routing ----------
  const ID = "(\\d+)";
  const ROUTES = [
    ["GET", "/meta", H.meta], ["POST", "/categories", H.categoryCreate], ["DELETE", "/categories/([^/]+)", H.categoryDelete],
    ["GET", "/homes", H.homeList], ["POST", "/homes", H.homeCreate], ["GET", `/homes/${ID}`, H.homeGet], ["PUT", `/homes/${ID}`, H.homeUpdate], ["DELETE", `/homes/${ID}`, H.homeDelete],
    ["GET", `/homes/${ID}/report`, H.reportJson], ["GET", `/homes/${ID}/report\\.pdf`, H.reportPdf], ["POST", `/homes/${ID}/report/drive`, H.reportDrive],
    ["GET", `/homes/${ID}/rooms`, H.roomList], ["POST", `/homes/${ID}/rooms`, H.roomCreate],
    ["GET", `/homes/${ID}/insurance`, H.insGet], ["PUT", `/homes/${ID}/insurance`, H.insPut], ["DELETE", `/homes/${ID}/insurance`, H.insDelete],
    ["GET", `/rooms/${ID}`, H.roomGet], ["PUT", `/rooms/${ID}`, H.roomUpdate], ["DELETE", `/rooms/${ID}`, H.roomDelete],
    ["GET", `/rooms/${ID}/items`, H.itemList], ["POST", `/rooms/${ID}/items`, H.itemCreate],
    ["GET", `/items/${ID}`, H.itemGet], ["PUT", `/items/${ID}`, H.itemUpdate], ["DELETE", `/items/${ID}`, H.itemDelete],
    ["POST", `/items/${ID}/image`, H.itemImage], ["POST", `/items/${ID}/scan`, H.itemScan], ["GET", "/scan-queue", H.scanQueue], ["POST", `/items/${ID}/link-image`, H.linkImage],
    ["POST", "/identify", H.identify], ["POST", "/classify", H.classify], ["POST", "/receipt", H.receipt], ["POST", "/link-info", H.linkInfo],
    ["GET", "/settings", H.settingsGet], ["PUT", "/settings", H.settingsPut], ["DELETE", "/google", H.googleDisconnect],
    ["GET", "/providers/status", H.providersStatus], ["GET", "/providers/ollama-models", H.ollamaModels],
  ].map(([m, p, fn]) => [m, new RegExp("^" + p + "$"), fn]);

  async function call(method, path, body) {
    await init();
    const url = new URL(path, "http://local"), p = url.pathname.length > 1 ? url.pathname.replace(/\/+$/, "") : url.pathname;
    let matched = false;
    for (const [m, rx, fn] of ROUTES) {
      const mt = rx.exec(p); if (!mt) continue; matched = true; if (m !== method) continue;
      const ids = mt.slice(1).map(g => /^\d+$/.test(g) ? Number(g) : g);
      const b = body === undefined ? {} : JSON.parse(JSON.stringify(body));
      if (b === null || typeof b !== "object" || Array.isArray(b)) throw bad("JSON body must be an object");
      const mutating = method !== "GET";
      const snapshot = mutating ? JSON.stringify(db) : null;
      try {
        const out = await fn(b, url.searchParams, ...ids);
        if (mutating) await store.saveDb(db);
        if (out && out.raw) return out.raw;
        return out && out.status ? out.data : out;
      } catch (e) {
        if (mutating) db = JSON.parse(snapshot);
        throw e instanceof ApiError ? e : (e.status ? new ApiError(e.status, e.message) : e);
      }
    }
    throw bad(matched ? "method not allowed" : "not found", matched ? 405 : 404);
  }

  return {
    call, init,
    imageUrl: name => images.get(name) || "",
    isEmpty: () => !db || db.homes.length === 0,
    isDemo: () => !!(db && db.meta.demo),
    clearDemoFlag: async () => { db.meta.demo = false; await store.saveDb(db); },
    exportAll, importAll, loadSample: async () => { await init(); await loadSample(); await store.saveDb(db); }, eraseAll,
    setImageMaker: f => { imageMaker = f; }, setDrive: d => { drive = d; },
  };
}
