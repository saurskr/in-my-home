// Tests for the in-browser backend (static/local/*). Run: node --test tests/
import test from "node:test";
import assert from "node:assert/strict";
import {createLocalApi} from "../static/local/api.js";
import {MemoryStore} from "../static/local/store.js";
import {createBrowserDrive} from "../static/local/drive.js";
import {normalizeReceipt, normalizeDate, toMoney} from "../static/local/ai.js";
import {wrap, textWidth, encode, buildPdf} from "../static/local/pdf.js";

const NOW = new Date(2026, 10, 20, 12, 0, 0);
const mk = (extra = {}) => { const store = new MemoryStore(); const api = createLocalApi({store, clock: () => NOW, ...extra}); return {api, store, call: api.call}; };
const fails = async (p, status, re) => { await assert.rejects(p, e => { assert.equal(e.status, status, e.message); if (re) assert.match(e.message, re); return true; }); };
// a real 8x6 JPEG would need a canvas; this is a tiny valid-looking JPEG header with SOF0 (enough for the PDF writer)
const JPEG = "data:image/jpeg;base64," + Buffer.from([0xFF, 0xD8, 0xFF, 0xE0, 0, 4, 0, 0, 0xFF, 0xC0, 0, 17, 8, 0, 6, 0, 8, 3, 1, 0x22, 0, 2, 0x11, 1, 3, 0x11, 1, 0xFF, 0xD9]).toString("base64");

async function setup(c) {
  const home = await c.call("POST", "/homes", {name: "Lake House", street: "1 Main St", city: "Austin", state: "tx", zip: "787010001"});
  const room = await c.call("POST", `/homes/${home.id}/rooms`, {name: "Kitchen"});
  return {home, room};
}

test("homes: address formatting, validation, totals", async () => {
  const c = mk(); const {home, room} = await setup(c);
  assert.equal(home.address, "1 Main St, Austin, TX 78701-0001");
  await fails(c.call("POST", "/homes", {name: ""}), 400, /name is required/);
  await fails(c.call("POST", "/homes", {name: "x", state: "ZZ"}), 400, /state/);
  await fails(c.call("POST", "/homes", {name: "x", zip: "12"}), 400, /zip/);
  await c.call("POST", `/rooms/${room.id}/items`, {name: "A", purchased_value: 10.1, replacement_value: 12.2});
  await c.call("POST", `/rooms/${room.id}/items`, {name: "B", purchased_value: 20.2});
  const h = await c.call("GET", `/homes/${home.id}`);
  assert.deepEqual([h.room_count, h.item_count, h.total_purchased, h.total_replacement], [1, 2, 30.3, 32.4]);   // B's replacement defaults to what was paid
  await fails(c.call("GET", "/homes/999"), 404);
  await fails(c.call("PATCH", "/homes", {}), 405);
  await fails(c.call("GET", "/nope"), 404);
});

test("items: fields, validation, dates, categories, replacement date rules", async () => {
  const c = mk(); const {room} = await setup(c);
  const it = await c.call("POST", `/rooms/${room.id}/items`, {name: " Mixer ", brand: "KitchenAid", model_number: "KSM150", description: "red", purchased_value: "99.999",
    purchased_date: "2025-01-02", purchased_condition: "New", item_type: "appliance", similar_url: "https://x.example/p"});
  assert.equal(it.inv_no, "INV-000001"); assert.equal(it.name, "Mixer"); assert.equal(it.purchased_value, 100); assert.equal(it.item_type, "Appliance");
  assert.equal(it.replacement_date, "");                                  // no replacement given: never dated
  await fails(c.call("POST", `/rooms/${room.id}/items`, {name: "x", purchased_date: "2999-01-01"}), 400, /future/);
  await fails(c.call("POST", `/rooms/${room.id}/items`, {name: "x", purchased_date: "2026-02-30"}), 400, /date/);
  await fails(c.call("POST", `/rooms/${room.id}/items`, {name: "x", purchased_condition: "Mint"}), 400, /one of/);
  await fails(c.call("POST", `/rooms/${room.id}/items`, {name: "x", description: "d".repeat(251)}), 400, /250/);
  await fails(c.call("POST", `/rooms/${room.id}/items`, {name: "x", model_number: "m".repeat(61)}), 400, /60/);
  await fails(c.call("POST", `/rooms/${room.id}/items`, {name: "x", purchased_value: -1}), 400, /non-negative/);
  await fails(c.call("POST", `/rooms/${room.id}/items`, {name: "x", similar_url: "ftp://x"}), 400, /http/);
  await fails(c.call("POST", `/rooms/${room.id}/items`, {name: "x", item_type: "Tools"}), 400, /item_type/);
  await fails(c.call("POST", `/rooms/${room.id}/items`, {name: "x", source: "robot"}), 400, /source/);
  assert.equal((await c.call("GET", `/rooms/${room.id}/items`)).length, 1, "failed creates leave nothing behind");
  // replacement date rules
  const u1 = await c.call("PUT", `/items/${it.id}`, {replacement_value: 150}); assert.equal(u1.replacement_date, "2026-11-20");
  const u2 = await c.call("PUT", `/items/${it.id}`, {replacement_date: "2026-01-15"}); assert.equal(u2.replacement_date, "2026-01-15"); assert.equal(u2.replacement_value, 150);
  const u3 = await c.call("PUT", `/items/${it.id}`, {replacement_value: 150}); assert.equal(u3.replacement_date, "2026-01-15", "same cost does not re-date");
  const u4 = await c.call("PUT", `/items/${it.id}`, {price_confirmed: true}); assert.equal(u4.replacement_date, "2026-11-20");
  const u5 = await c.call("PUT", `/items/${it.id}`, {description: "d".repeat(251)}).catch(e => e); assert.equal(u5.status, 400);
  // older, longer description may be kept as is
  const store = c.store; const raw = await store.load(); raw.db.items[0].description = "d".repeat(300); await store.saveDb(raw.db);
  const c2 = createLocalApi({store, clock: () => NOW}); const kept = await c2.call("PUT", `/items/${it.id}`, {description: "d".repeat(300), brand: "KA"}); assert.equal(kept.description.length, 300);
  // moving rooms, deleting
  const r2 = await c.call("POST", `/homes/1/rooms`, {name: "Pantry"});
  assert.equal((await c.call("PUT", `/items/${it.id}`, {room_id: r2.id})).room_id, r2.id);
  await fails(c.call("PUT", `/items/${it.id}`, {room_id: 99}), 404);
  assert.deepEqual(await c.call("DELETE", `/items/${it.id}`), {deleted: it.id});
});

test("custom categories", async () => {
  const c = mk(); const {room} = await setup(c);
  await fails(c.call("POST", `/rooms/${room.id}/items`, {name: "Saw", item_type: "Tools"}), 400);
  const r = await c.call("POST", "/categories", {name: "  Tools "}); assert.equal(r.name, "Tools"); assert.ok(r.item_types.includes("Tools"));
  await fails(c.call("POST", "/categories", {name: "tools"}), 409); await fails(c.call("POST", "/categories", {name: "Appliance"}), 409);
  await fails(c.call("POST", "/categories", {name: ""}), 400); await fails(c.call("POST", "/categories", {name: "x".repeat(41)}), 400);
  const m = await c.call("GET", "/meta"); assert.ok(m.item_types.includes("Tools")); assert.ok(!m.builtin_types.includes("Tools"));
  const it = await c.call("POST", `/rooms/${room.id}/items`, {name: "Saw", item_type: "tools"}); assert.equal(it.item_type, "Tools");
  await fails(c.call("DELETE", "/categories/Tools"), 409, /still use/); await fails(c.call("DELETE", "/categories/Appliance"), 404);
  await c.call("DELETE", `/items/${it.id}`); await c.call("DELETE", "/categories/Tools");
  assert.ok(!(await c.call("GET", "/meta")).item_types.includes("Tools"));
});

test("rooms: templates, rollback, moving between homes, cascade delete with images", async () => {
  const c = mk(); const {home, room} = await setup(c);
  const k = await c.call("POST", `/homes/${home.id}/rooms`, {name: "garage", prefill: true}); assert.equal(k.item_count, 3);
  assert.ok((await c.call("GET", `/rooms/${k.id}/items`)).every(i => i.source === "template"));
  const before = (await c.call("GET", `/homes/${home.id}/rooms`)).length;
  await fails(c.call("POST", `/homes/${home.id}/rooms`, {name: "Attic", prefill: true}), 400, /template/);
  assert.equal((await c.call("GET", `/homes/${home.id}/rooms`)).length, before, "failed room create is rolled back");
  const it = await c.call("POST", `/rooms/${room.id}/items`, {name: "Photo item"});
  await c.call("POST", `/items/${it.id}/image?kind=photo`, {data: JPEG});
  const withImg = await c.call("GET", `/items/${it.id}`); assert.ok(withImg.image.endsWith(".jpg")); assert.equal(c.api.imageUrl(withImg.image), JPEG);
  await fails(c.call("POST", `/items/${it.id}/image?kind=photo`, {data: "junk"}), 400); await fails(c.call("POST", `/items/${it.id}/image?kind=other`, {data: JPEG}), 400);
  const replaced = await c.call("POST", `/items/${it.id}/image?kind=photo`, {data: JPEG}); assert.notEqual(replaced.image, withImg.image);
  assert.equal(c.api.imageUrl(withImg.image), "", "old picture removed");
  const home2 = await c.call("POST", "/homes", {name: "Cabin"});
  assert.equal((await c.call("PUT", `/rooms/${room.id}`, {home_id: home2.id})).home_id, home2.id);
  await c.call("DELETE", `/homes/${home2.id}`);
  assert.equal(c.api.imageUrl(replaced.image), "", "deleting a home removes its pictures");
  assert.equal((await c.call("GET", `/homes`)).length, 1);
});

test("insurance: validation and attached to the home", async () => {
  const c = mk(); const {home} = await setup(c);
  assert.equal((await c.call("GET", `/homes/${home.id}`)).insurance.configured, false);
  const r = await c.call("PUT", `/homes/${home.id}/insurance`, {insurer: "Acme", website: "https://a.example", renewal_date: "2027-03-01", dwelling_coverage: "400000",
    riders: [{name: "Jewelry", limit: 10000}]});
  assert.equal(r.configured, true); assert.equal(r.dwelling_coverage, 400000); assert.equal(r.riders[0].limit, 10000);
  assert.equal((await c.call("GET", `/homes/${home.id}`)).insurance.insurer, "Acme");
  await fails(c.call("PUT", `/homes/${home.id}/insurance`, {riders: Array(6).fill({name: "x"})}), 400, /At most 5/);
  await fails(c.call("PUT", `/homes/${home.id}/insurance`, {riders: [{limit: 5}]}), 400, /needs a name/);
  await fails(c.call("PUT", `/homes/${home.id}/insurance`, {renewal_date: "03/01/2027"}), 400, /YYYY-MM-DD/);
  await fails(c.call("PUT", `/homes/${home.id}/insurance`, {website: "nope"}), 400);
  assert.equal((await c.call("GET", `/homes/${home.id}/insurance`)).insurer, "Acme", "failed update changed nothing");
  await c.call("DELETE", `/homes/${home.id}/insurance`); assert.equal((await c.call("GET", `/homes/${home.id}/insurance`)).configured, false);
});

test("data survives a reload (same store)", async () => {
  const c = mk(); const {room} = await setup(c);
  await c.call("POST", `/rooms/${room.id}/items`, {name: "Kept"});
  const again = createLocalApi({store: c.store, clock: () => NOW});
  assert.equal((await again.call("GET", `/rooms/${room.id}/items`))[0].name, "Kept");
  const next = await again.call("POST", `/rooms/${room.id}/items`, {name: "Next"}); assert.equal(next.inv_no, "INV-000002");
});

test("report: json, pdf with/without photos, hidden purchase price", async () => {
  const c = mk(); const {home, room} = await setup(c);
  await c.call("PUT", `/homes/${home.id}/insurance`, {insurer: "Acme", riders: [{name: "Art", limit: 5000}]});
  for (let n = 0; n < 40; n++) await c.call("POST", `/rooms/${room.id}/items`, {name: `Item ${n} é —`, description: n % 2 ? "word ".repeat(40) : "", purchased_value: 10, replacement_value: 12, purchased_date: "2025-01-01"});
  const first = (await c.call("GET", `/rooms/${room.id}/items`))[0]; await c.call("POST", `/items/${first.id}/image?kind=photo`, {data: JPEG});
  await c.call("POST", `/homes/${home.id}/rooms`, {name: "Empty"});
  const rep = await c.call("GET", `/homes/${home.id}/report`); assert.deepEqual([rep.item_count, rep.total_purchased, rep.total_replacement, rep.rooms.length], [40, 400, 480, 2]);
  const text = b => Buffer.from(b).toString("latin1");
  const plain = await c.call("GET", `/homes/${home.id}/report.pdf`);
  assert.equal(plain.ctype, "application/pdf"); assert.match(plain.filename, /^lake-house-inventory-2026-11-20\.pdf$/);
  const t = text(plain.bytes); assert.ok(t.startsWith("%PDF-1.4") && t.trimEnd().endsWith("%%EOF")); assert.ok(!t.includes("/Subtype /Image"));
  assert.ok((t.match(/\/Type \/Page /g) || []).length >= 2, "40 items need more than one page");
  assert.ok(t.includes("($400.00) Tj") && t.includes("($10.00) Tj"));
  const pics = await c.call("GET", `/homes/${home.id}/report.pdf?photos=1`); assert.ok(text(pics.bytes).includes("/Subtype /Image")); assert.match(pics.filename, /-photos-/);
  const hidden = await c.call("GET", `/homes/${home.id}/report.pdf?hide_purchase_price=1`); const h = text(hidden.bytes);
  assert.ok(!h.includes("($10.00) Tj") && !h.includes("($400.00) Tj") && h.includes("($12.00) Tj")); assert.match(hidden.filename, /-no-purchase-price-/);
  assert.ok(!text((await c.call("GET", `/homes/${home.id}/report.pdf?photos=1&hide_purchase_price=1`)).bytes).includes("Purchased: $10.00"));
  await fails(c.call("GET", "/homes/999/report.pdf"), 404);
});

test("pdf helpers", () => {
  assert.deepEqual(wrap("a b c", 1000), ["a b c"]);
  assert.ok(wrap("word ".repeat(40) + "x".repeat(80), 100).every(l => textWidth(l, "F1", 10) <= 100));
  assert.deepEqual(encode("café — 中"), [99, 97, 102, 233, 32, 0x97, 32, 63]);
  assert.ok(buildPdf({home: {name: "H", insurance: {}}, rooms: [], item_count: 0, total_purchased: 0, total_replacement: 0}).length > 400);
});

test("backup export / import, sample data, erase", async () => {
  const c = mk(); const {room} = await setup(c);
  const it = await c.call("POST", `/rooms/${room.id}/items`, {name: "Pic"}); await c.call("POST", `/items/${it.id}/image?kind=photo`, {data: JPEG});
  await c.call("PUT", "/settings", {anthropic_key: "sk-ant-" + "x".repeat(30)});
  const backup = await c.api.exportAll();
  assert.ok(!JSON.stringify(backup).includes("sk-ant-"), "the API key is never part of a backup");
  assert.equal(Object.keys(backup.images).length, 1);
  const d = mk(); await d.api.init(); await d.api.importAll(JSON.parse(JSON.stringify(backup)));
  const home2 = (await d.call("GET", "/homes"))[0]; assert.equal(home2.item_count, 1);
  const item2 = (await d.call("GET", `/rooms/${room.id}/items`))[0]; assert.equal(d.api.imageUrl(item2.image), JPEG);
  assert.equal((await d.call("POST", `/rooms/${room.id}/items`, {name: "Next"})).inv_no, "INV-000002", "counters continue after an import");
  await fails(d.api.importAll({app: "other"}), 400, /not a Home Inventory backup/);
  await d.api.eraseAll(); assert.equal((await d.call("GET", "/homes")).length, 0);
  const e = mk(); await e.api.loadSample();
  const [h] = await e.call("GET", "/homes"); assert.equal(h.name, "Maple Street House"); assert.ok(h.item_count >= 15); assert.equal(h.insurance.riders.length, 2); assert.ok(e.api.isDemo());
  const rep = await e.call("GET", `/homes/${h.id}/report`); assert.ok(rep.rooms.flatMap(r => r.items).some(i => !i.replacement_date), "sample has an item whose price was never dated");
  await fails(e.api.loadSample(), 400, /empty/);
});

test("link features explain what is missing", async () => {
  const c = mk(); const {room} = await setup(c);
  const it = await c.call("POST", `/rooms/${room.id}/items`, {name: "x", similar_url: "https://a.example/p"});
  await fails(c.call("POST", "/link-info", {url: "https://a.example"}), 501, /Python app/);
  await fails(c.call("POST", `/items/${it.id}/link-image`, {}), 501, /Python app/);
});

test("settings: kept locally, validated, key never returned", async () => {
  const c = mk();
  assert.equal((await c.call("GET", "/settings")).ollama.model, "qwen2.5vl:7b");
  await fails(c.call("PUT", "/settings", {ollama_url: "not a url"}), 400); await fails(c.call("PUT", "/settings", {anthropic_key: "short"}), 400);
  const s = await c.call("PUT", "/settings", {anthropic_key: "sk-ant-" + "k".repeat(30), ollama_model: "llava:7b", google_client_id: "abc.apps.googleusercontent.com"});
  assert.deepEqual([s.anthropic.configured, s.ollama.model, s.google.connected], [true, "llava:7b", true]); assert.ok(!JSON.stringify(s).includes("sk-ant"));
  assert.equal((await c.call("DELETE", "/google")).google.connected, false);
  assert.equal((await c.call("PUT", "/settings", {anthropic_key: ""})).anthropic.configured, false);
});

// ---- photo reading with a pretend Anthropic / Ollama ----
function fakeFetch(handlers) {
  const log = [];
  const f = async (url, opts = {}) => {
    log.push({url, opts, body: opts.body && typeof opts.body === "string" ? JSON.parse(opts.body) : opts.body});
    for (const [re, fn] of handlers) if (re.test(url)) { const r = await fn(url, opts, log[log.length - 1].body); return {ok: r.status === undefined || r.status < 400, status: r.status || 200, text: async () => JSON.stringify(r.json ?? {}), json: async () => r.json ?? {}}; }
    throw new TypeError("network down");
  };
  f.log = log; return f;
}
test("identify / classify / receipt through Claude and Ollama", async () => {
  const item = {name: "Stand Mixer", brand: "KitchenAid", model_number: "KSM150", item_type: "appliance", description: "Red mixer", search_query: "kitchenaid mixer"};
  const claude = fakeFetch([[/anthropic/, async (u, o, b) => ({json: {content: [{type: "tool_use", input: b.tool_choice.name === "record" && JSON.stringify(b).includes("receipt") ? {kind: "receipt"} : item}]}})]]);
  const c = mk({fetchImpl: claude}); const {room} = await setup(c);
  await c.call("POST", "/categories", {name: "Tools"});
  await fails(c.call("POST", "/identify", {data: JPEG, provider: "anthropic"}), 400, /Claude is not set up/);
  await c.call("PUT", "/settings", {anthropic_key: "sk-ant-" + "k".repeat(30)});
  const r = await c.call("POST", "/identify", {data: JPEG, provider: "anthropic"});
  assert.deepEqual([r.name, r.item_type, r.provider], ["Stand Mixer", "Appliance", "anthropic"]);
  const sent = claude.log[0]; assert.equal(sent.opts.headers["anthropic-dangerous-direct-browser-access"], "true"); assert.ok(sent.body.tools[0].input_schema.properties.item_type.enum.includes("Tools"));
  await fails(c.call("POST", "/identify", {data: "junk", provider: "anthropic"}), 400);
  await fails(c.call("POST", "/identify", {data: JPEG, provider: "bing"}), 400);
  // Ollama: unreachable, then working
  const down = mk({fetchImpl: fakeFetch([])}); await fails(down.call("POST", "/identify", {data: JPEG}), 502, /Could not reach Ollama/);
  const ollama = fakeFetch([[/api\/chat/, async (u, o, b) => ({json: {message: {content: JSON.stringify(b.format.properties.kind ? {kind: "banana"} : b.format.properties.line_items ? {store: "Target", date: "03/02/2026", currency: "USD", subtotal: 0, tax: 0, total: 100,
    line_items: [{description: "Toaster", quantity: 1, unit_price: 40, total_price: 40, category: "appliance"}, {description: "TOTAL", quantity: 1, unit_price: 100, total_price: 100, category: "Other"}, {description: "Bananas", quantity: 1, unit_price: 1, total_price: 1, category: "Consumable"}]} : item)}}})]]);
  const o = mk({fetchImpl: ollama});
  assert.equal((await o.call("POST", "/identify", {data: JPEG})).brand, "KitchenAid");
  assert.equal((await o.call("POST", "/classify", {data: JPEG})).kind, "other");     // unknown answers become "other"
  const rec = await o.call("POST", "/receipt", {data: JPEG}); assert.deepEqual([rec.store, rec.date, rec.items.map(i => i.description), rec.items[0].category], ["Target", "2026-03-02", ["Toaster", "Bananas"], "Appliance"]);
  assert.ok(rec.warnings.some(w => /add up/.test(w)));
});

test("receipt helpers", () => {
  assert.equal(toMoney("$1,299.00"), 1299); assert.equal(toMoney("12,99"), 12.99); assert.equal(toMoney("(5.00)"), -5); assert.equal(toMoney("abc"), null);
  assert.equal(normalizeDate("Mar 2, 2026"), "2026-03-02"); assert.equal(normalizeDate("3/2/26 14:05"), "2026-03-02"); assert.equal(normalizeDate("2026-02-30"), ""); assert.equal(normalizeDate("1/1/1980"), "");
  assert.throws(() => normalizeReceipt("not json"), /usable receipt/);
});

test("Google Drive upload from the browser (pretend Google)", async () => {
  const calls = [];
  const f = async (url, opts) => {
    calls.push({url, opts});
    const j = o => ({ok: true, status: 200, json: async () => o});
    if (opts.headers.Authorization !== "Bearer tok1") return {ok: false, status: 401, json: async () => ({error: {message: "bad"}})};
    if (url.includes("/upload/drive/v3/files")) return j({id: "F1", name: "x.pdf", webViewLink: "https://drive.google.com/file/d/F1/view"});
    if (opts.method === "GET") return j({files: []});
    return j({id: "FOLDER1"});
  };
  const drive = createBrowserDrive({apiUrl: "https://g.test", getToken: async id => { assert.equal(id, "cid"); return {access_token: "tok1", expires_in: 3600}; }, fetchImpl: f});
  const c = mk({drive}); const {home} = await setup(c);
  await fails(c.call("POST", `/homes/${home.id}/report/drive`, {}), 400, /client id/);
  await c.call("PUT", "/settings", {google_client_id: "cid"});
  const out = await c.call("POST", `/homes/${home.id}/report/drive?photos=0`, {});
  assert.deepEqual([out.link, out.folder], ["https://drive.google.com/file/d/F1/view", "Home Inventory"]);
  const up = calls.find(x => x.url.includes("/upload/")); assert.match(up.opts.headers["Content-Type"], /multipart\/related/);
  const body = Buffer.from(up.opts.body).toString("latin1"); assert.ok(body.includes("%PDF-1.4") && body.includes('"parents":["FOLDER1"]') && body.includes("lake-house-inventory-"));
  const bad = createBrowserDrive({apiUrl: "https://g.test", getToken: async () => ({access_token: "other"}), fetchImpl: f});
  await assert.rejects(bad.upload({clientId: "cid", filename: "a.pdf", bytes: new Uint8Array([1])}), e => e.status === 403 && /Drive API/.test(e.message));
});

test("photos first, AI later: pending items, scanning, receipts split, confirm", async () => {
  const state = {kind: "item"};
  const item = {name: "Stand Mixer", brand: "KitchenAid", model_number: "KSM150", item_type: "appliance", description: "Red mixer", search_query: "q"};
  const rcpt = {store: "Target", date: "03/02/2026", currency: "USD", subtotal: 0, tax: 0, total: 0, line_items: [
    {description: "Toaster", quantity: 1, unit_price: 29.99, total_price: 29.99, category: "Appliance"},
    {description: "Nightstand", quantity: 2, unit_price: 179.99, total_price: 359.98, category: "Furniture"},
    {description: "Bananas", quantity: 1, unit_price: 1.2, total_price: 1.2, category: "Consumable"}]};
  const f = fakeFetch([[/api\/chat/, async (u, o, b) => ({status: state.down ? 500 : 200, json: {message: {content: JSON.stringify(
    b.format.properties.kind ? {kind: state.kind} : b.format.properties.line_items ? rcpt : item)}}})]]);
  const c = mk({fetchImpl: f}); const {home, room} = await setup(c);
  const mkPhoto = async name => { const it = await c.call("POST", `/rooms/${room.id}/items`, {name, scan_state: "pending"}); return c.call("POST", `/items/${it.id}/image?kind=photo`, {data: JPEG}); };
  await fails(c.call("POST", `/rooms/${room.id}/items`, {name: "x", scan_state: "done"}), 400, /scan_state/);
  const a = await mkPhoto("Photo 1");
  assert.equal(a.scan_state, "pending");
  assert.equal((await c.call("GET", `/homes/${home.id}`)).scan_count, 1);
  const q = await c.call("GET", "/scan-queue"); assert.deepEqual(q.map(i => [i.id, i.room_name, i.home_name]), [[a.id, "Kitchen", "Lake House"]]);
  // item
  const r1 = await c.call("POST", `/items/${a.id}/scan`, {}); assert.equal(r1.kind, "item");
  assert.deepEqual([r1.item.name, r1.item.item_type, r1.item.scan_state, r1.item.source], ["Stand Mixer", "Appliance", "review", "vision"]); assert.match(r1.item.scan_note, /qwen2\.5vl:7b/); assert.ok(r1.item.image);
  const ok = await c.call("PUT", `/items/${a.id}`, {scan_state: ""}); assert.deepEqual([ok.scan_state, ok.scan_note], ["", ""]);
  assert.equal((await c.call("GET", "/scan-queue")).length, 0);
  await fails(c.call("PUT", `/items/${a.id}`, {scan_state: "review"}), 400);
  // receipt: one item per line (quantity copies), photo goes with the first, placeholder removed
  const b = await mkPhoto("Receipt photo"); state.kind = "receipt";
  const r2 = await c.call("POST", `/items/${b.id}/scan`, {}); assert.equal(r2.deleted, b.id); assert.equal(r2.created.length, 4);
  const all = await c.call("GET", `/rooms/${room.id}/items`); assert.ok(!all.some(i => i.id === b.id));
  const made = all.filter(i => r2.created.includes(i.id));
  assert.deepEqual(made.map(i => i.name).sort(), ["Bananas", "Nightstand", "Nightstand", "Toaster"]);
  assert.ok(made.every(i => i.scan_state === "review" && i.purchased_date === "2026-03-02" && /Target/.test(i.scan_note)));
  assert.equal(made.filter(i => i.image).length, 1); assert.equal(made.find(i => i.name === "Toaster").purchased_value, 29.99); assert.equal(made.find(i => i.name === "Bananas").item_type, null);
  assert.equal(c.api.imageUrl(made.find(i => i.image).image), JPEG, "the receipt photo is kept");
  // other, then read again, then the AI is down: nothing is lost
  const d = await mkPhoto("Odd"); state.kind = "other";
  const r3 = await c.call("POST", `/items/${d.id}/scan`, {}); assert.deepEqual([r3.kind, r3.item.scan_state], ["other", "failed"]); assert.match(r3.item.scan_note, /not an item or a receipt/);
  assert.equal((await c.call("PUT", `/items/${d.id}`, {scan_state: "pending"})).scan_state, "pending");
  state.down = true; await fails(c.call("POST", `/items/${d.id}/scan`, {}), 502);
  assert.equal((await c.call("GET", `/items/${d.id}`)).scan_state, "pending");
  await fails(c.call("POST", `/items/${d.id}/scan`, {provider: "anthropic"}), 400, /Claude is not set up/);
  const e = await c.call("POST", `/rooms/${room.id}/items`, {name: "No photo", scan_state: "pending"}); await fails(c.call("POST", `/items/${e.id}/scan`, {}), 400, /no photo/);
});
