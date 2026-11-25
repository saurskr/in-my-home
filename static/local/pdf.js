// PDF writer and inventory report layout, in plain JavaScript (a port of pdfwriter.py and report.py).
// Photos are embedded when they are JPEG (the app stores every photo as JPEG); anything else shows "Photo not shown".

export const PAGE_W = 612, PAGE_H = 792;

const REG = [278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278, 278,
  556, 556, 556, 556, 556, 556, 556, 556, 556, 556, 278, 278, 584, 584, 584, 556,
  1015, 667, 667, 722, 722, 667, 611, 778, 722, 278, 500, 667, 556, 833, 722, 778,
  667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 278, 278, 278, 469, 556,
  333, 556, 556, 500, 556, 556, 278, 556, 556, 222, 222, 500, 222, 833, 556, 556,
  556, 556, 333, 500, 278, 556, 500, 722, 500, 500, 500, 334, 260, 334, 584];
const BOLD = [278, 333, 474, 556, 556, 889, 722, 238, 333, 333, 389, 584, 278, 333, 278, 278,
  556, 556, 556, 556, 556, 556, 556, 556, 556, 556, 333, 333, 584, 584, 584, 611,
  975, 722, 722, 722, 722, 667, 611, 778, 722, 278, 556, 722, 611, 833, 722, 778,
  667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 333, 278, 333, 584, 556,
  333, 556, 611, 556, 611, 556, 333, 611, 611, 278, 278, 556, 278, 889, 611, 611,
  611, 611, 389, 556, 333, 611, 556, 778, 556, 556, 500, 389, 280, 389, 584];
const WIDTHS = {F1: REG, F2: BOLD};

// Windows-1252 bytes for the characters between U+0080 and U+009F's slots
const CP1252 = {0x20AC: 0x80, 0x201A: 0x82, 0x0192: 0x83, 0x201E: 0x84, 0x2026: 0x85, 0x2020: 0x86, 0x2021: 0x87,
  0x02C6: 0x88, 0x2030: 0x89, 0x0160: 0x8A, 0x2039: 0x8B, 0x0152: 0x8C, 0x017D: 0x8E, 0x2018: 0x91, 0x2019: 0x92,
  0x201C: 0x93, 0x201D: 0x94, 0x2022: 0x95, 0x2013: 0x96, 0x2014: 0x97, 0x02DC: 0x98, 0x2122: 0x99, 0x0161: 0x9A,
  0x203A: 0x9B, 0x0153: 0x9C, 0x017E: 0x9E, 0x0178: 0x9F};

export function encode(text) {
  const s = String(text == null ? "" : text).replace(/\t/g, " ").replace(/[\r\n]+/g, " ");
  const out = [];
  for (const ch of s) {
    const c = ch.codePointAt(0);
    if (c < 0x80 || (c >= 0xA0 && c <= 0xFF)) out.push(c);
    else if (CP1252[c] !== undefined) out.push(CP1252[c]);
    else out.push(0x3F);
  }
  return out;
}

export function textWidth(text, font = "F1", size = 10) {
  const w = WIDTHS[font];
  let total = 0;
  for (const b of encode(text)) total += (b >= 32 && b <= 126) ? w[b - 32] : (b === 0x85 || b === 0x97 ? 1000 : 556);
  return total * size / 1000;
}

export function wrap(text, width, font = "F1", size = 10) {
  const lines = [];
  for (const para of String(text == null ? "" : text).split("\n")) {
    let cur = "";
    for (let word of para.split(/\s+/).filter(Boolean)) {
      while (textWidth(word, font, size) > width) {
        let cut = word.length;
        while (cut > 1 && textWidth(word.slice(0, cut), font, size) > width) cut--;
        if (cur) { lines.push(cur); cur = ""; }
        lines.push(word.slice(0, cut)); word = word.slice(cut);
      }
      const trial = (cur + " " + word).trim();
      if (cur && textWidth(trial, font, size) > width) { lines.push(cur); cur = word; } else cur = trial;
    }
    lines.push(cur);
  }
  return lines.length ? lines : [""];
}

// ---- bytes ----
class Buf {
  constructor() { this.parts = []; this.length = 0; }
  add(x) {
    let u;
    if (typeof x === "string") { u = new Uint8Array(x.length); for (let i = 0; i < x.length; i++) u[i] = x.charCodeAt(i) & 0xFF; }
    else u = x instanceof Uint8Array ? x : Uint8Array.from(x);
    this.parts.push(u); this.length += u.length; return this;
  }
  bytes() { const out = new Uint8Array(this.length); let o = 0; for (const p of this.parts) { out.set(p, o); o += p.length; } return out; }
}
const esc = arr => { const o = []; for (const b of arr) { if (b === 0x5C || b === 0x28 || b === 0x29) o.push(0x5C); o.push(b); } return o; };
const f2 = n => (Math.round(n * 100) / 100).toFixed(2);

// ---- pictures ----
export function dataUrlBytes(url) {
  const m = /^data:([\w/+.-]+);base64,(.*)$/s.exec(url || "");
  if (!m) return null;
  const bin = atob(m[2]); const u = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) u[i] = bin.charCodeAt(i);
  return {mime: m[1], bytes: u};
}

export function loadJpeg(u) {
  if (!(u[0] === 0xFF && u[1] === 0xD8 && u[2] === 0xFF)) return null;
  let i = 2;
  while (i + 9 < u.length) {
    if (u[i] !== 0xFF) { i++; continue; }
    const marker = u[i + 1];
    if (marker === 0xC0 || marker === 0xC1 || marker === 0xC2) {
      const h = (u[i + 5] << 8) | u[i + 6], w = (u[i + 7] << 8) | u[i + 8], comps = u[i + 9];
      const cs = comps === 1 ? "/DeviceGray" : comps === 3 ? "/DeviceRGB" : null;
      return cs ? {width: w, height: h, data: u, colorspace: cs} : null;
    }
    if (marker === 0xD8 || marker === 0x01 || (marker >= 0xD0 && marker <= 0xD7)) { i += 2; continue; }
    i += 2 + ((u[i + 2] << 8) | u[i + 3]);
  }
  return null;
}

export class Document {
  constructor(title, footer = "", margin = 48) {
    this.title = title; this.footer = footer; this.margin = margin; this.pages = []; this.newPage();
  }
  get left() { return this.margin; }
  get right() { return PAGE_W - this.margin; }
  get width() { return PAGE_W - 2 * this.margin; }
  get bottom() { return this.margin + 14; }
  newPage() { this.pages.push({ops: [], images: []}); this.y = PAGE_H - this.margin; }
  ensure(h) { if (this.y - h < this.bottom) { this.newPage(); return true; } return false; }
  _op(s) { this.pages[this.pages.length - 1].ops.push(s); }
  text(x, y, s, font = "F1", size = 10, color = [0, 0, 0]) {
    this._op({text: encode(s), x, y, font, size, color});
  }
  textRight(xr, y, s, font = "F1", size = 10, color = [0, 0, 0]) { this.text(xr - textWidth(s, font, size), y, s, font, size, color); }
  paragraph(s, font = "F1", size = 10, color = [0, 0, 0], opts = {}) {
    const x = opts.x ?? this.left, width = opts.width ?? this.width, leading = opts.leading ?? size * 1.3;
    for (const line of wrap(s, width, font, size)) {
      this.ensure(leading); this.y -= leading; this.text(x, this.y + size * 0.25, line, font, size, color);
    }
    this.y -= opts.gap || 0;
  }
  line(x1, y1, x2, y2, color = [0.8, 0.8, 0.8], width = 0.5) { this._op({raw: `${color.map(f2).join(" ")} RG ${f2(width)} w ${f2(x1)} ${f2(y1)} m ${f2(x2)} ${f2(y2)} l S`}); }
  rect(x, y, w, h, fill) { this._op({raw: `${fill.map(f2).join(" ")} rg ${f2(x)} ${f2(y)} ${f2(w)} ${f2(h)} re f`}); }
  image(img, x, y, w, h) {
    const page = this.pages[this.pages.length - 1]; const name = "Im" + (page.images.length + 1);
    page.images.push([name, img]); this._op({raw: `q ${f2(w)} 0 0 ${f2(h)} ${f2(x)} ${f2(y)} cm /${name} Do Q`});
  }

  render() {
    const objs = []; const add = body => { objs.push(body); return objs.length; };
    add(null); add(null);
    const f1 = add("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>");
    const f2o = add("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>");
    const kids = [], total = this.pages.length;
    this.pages.forEach((page, n) => {
      const c = new Buf();
      const foot = (this.footer ? this.footer + "   " : "") + `Page ${n + 1} of ${total}`;
      const ops = page.ops.concat([{text: encode(foot), x: this.left, y: this.margin - 6, font: "F1", size: 8, color: [0.45, 0.45, 0.45]}]);
      for (const op of ops) {
        if (op.raw) c.add(op.raw + "\n");
        else { c.add(`BT ${op.color.map(f2).join(" ")} rg /${op.font} ${f2(op.size)} Tf ${f2(op.x)} ${f2(op.y)} Td (`).add(esc(op.text)).add(") Tj ET\n"); }
      }
      const content = c.bytes();
      const cid = add(new Buf().add(`<< /Length ${content.length} >>\nstream\n`).add(content).add("\nendstream").bytes());
      const xo = page.images.map(([name, img]) => {
        const head = `<< /Type /XObject /Subtype /Image /Width ${img.width} /Height ${img.height} /ColorSpace ${img.colorspace} /BitsPerComponent 8 /Filter /DCTDecode /Length ${img.data.length} >>\nstream\n`;
        return `/${name} ${add(new Buf().add(head).add(img.data).add("\nendstream").bytes())} 0 R`;
      });
      kids.push(add(`<< /Type /Page /Parent 2 0 R /MediaBox [0 0 ${PAGE_W} ${PAGE_H}] /Resources << /Font << /F1 ${f1} 0 R /F2 ${f2o} 0 R >> /XObject << ${xo.join(" ")} >> >> /Contents ${cid} 0 R >>`));
    });
    objs[0] = "<< /Type /Catalog /Pages 2 0 R >>";
    objs[1] = `<< /Type /Pages /Count ${kids.length} /Kids [${kids.map(k => k + " 0 R").join(" ")}] >>`;
    const info = add(new Buf().add("<< /Title (").add(esc(encode(this.title))).add(") /Producer (In My Home) >>").bytes());
    const out = new Buf().add("%PDF-1.4\n%\xE2\xE3\xCF\xD3\n"); const offsets = [];
    objs.forEach((body, i) => { offsets.push(out.length); out.add(`${i + 1} 0 obj\n`).add(body).add("\nendobj\n"); });
    const xref = out.length;
    out.add(`xref\n0 ${objs.length + 1}\n0000000000 65535 f \n`);
    for (const off of offsets) out.add(String(off).padStart(10, "0") + " 00000 n \n");
    out.add(`trailer\n<< /Size ${objs.length + 1} /Root 1 0 R /Info ${info} 0 R >>\nstartxref\n${xref}\n%%EOF\n`);
    return out.bytes();
  }
}

// ---------------- report layout ----------------
const INK = [0.13, 0.12, 0.10], MUTED = [0.42, 0.40, 0.36], BAND = [0.93, 0.91, 0.86], ACCENT = [0.64, 0.28, 0.17];

export function money(v) {
  const n = Number(v || 0);
  if (!Number.isFinite(n) || !n) return "-";
  return "$" + n.toLocaleString("en-US", {minimumFractionDigits: 2, maximumFractionDigits: 2});
}
export function niceDate(s) {
  const m = /^(\d{4})-(\d\d)-(\d\d)/.exec(s || "");
  if (!m) return "";
  const d = new Date(+m[1], +m[2] - 1, +m[3]);
  return d.toLocaleDateString("en-US", {month: "short", day: "numeric", year: "numeric"});
}

function summary(d, data, today, showPrice) {
  const h = data.home;
  d.y -= 4;
  d.text(d.left, d.y - 18, "Home inventory report", "F2", 20, ACCENT); d.y -= 26;
  d.paragraph(h.name, "F2", 14, INK, {gap: 1});
  if (h.address) d.paragraph(h.address, "F1", 10, MUTED);
  d.paragraph("Prepared " + niceDate(today), "F1", 9, MUTED, {gap: 6});
  const boxes = [["Rooms", String(data.rooms.length)], ["Items", String(data.item_count)]];
  if (showPrice) boxes.push(["Purchased", money(data.total_purchased)]);
  boxes.push(["Replacement value", money(data.total_replacement)]);
  const bw = d.width / boxes.length;
  d.ensure(44); d.rect(d.left, d.y - 38, d.width, 38, BAND);
  boxes.forEach(([k, v], n) => { const x = d.left + n * bw + 10; d.text(x, d.y - 14, k, "F1", 8, MUTED); d.text(x, d.y - 29, v, "F2", 13, INK); });
  d.y -= 48;
  const ins = h.insurance || {};
  if (ins.configured) {
    d.paragraph("Insurance", "F2", 11, INK, {gap: 2});
    const bits = [["Insurer", ins.insurer], ["Policy #", ins.policy_number], ["Expires", niceDate(ins.renewal_date)], ["Website", ins.website], ["Contact", ins.contact]];
    d.paragraph(bits.filter(b => b[1]).map(b => `${b[0]}: ${b[1]}`).join("  |  ") || "On file", "F1", 9, INK);
    const limits = [["Dwelling", ins.dwelling_coverage], ["Contents", ins.contents_coverage], ["Liability", ins.liability_coverage], ["Deductible", ins.deductible]];
    d.paragraph(limits.filter(b => b[1]).map(b => `${b[0]}: ${money(b[1])}`).join("  |  "), "F1", 9, INK);
    for (const r of ins.riders || []) d.paragraph(`Rider: ${r.name || ""} - ${money(r.limit)}`, "F1", 9, INK);
    d.y -= 6;
  }
}

function roomHeader(d, room) {
  d.ensure(60);
  d.rect(d.left, d.y - 22, d.width, 22, BAND);
  d.text(d.left + 8, d.y - 15, room.name, "F2", 12, INK);
  const n = room.items.length;
  d.textRight(d.right - 8, d.y - 15, `${n} item${n === 1 ? "" : "s"}   Replacement ${money(room.total_replacement)}`, "F1", 9, MUTED);
  d.y -= 28;
}

function table(d, room, cols, showPrice) {
  const xs = [d.left]; for (const w of cols) xs.push(xs[xs.length - 1] + w);
  const head = () => {
    d.ensure(40);
    ["Item", "Details", "Purchased", "Replacement"].forEach((label, i) => d.text(xs[i] + 2, d.y - 9, label, "F2", 8, MUTED));
    d.y -= 13; d.line(d.left, d.y, d.right, d.y);
  };
  head();
  for (const it of room.items) {
    const c1 = [[it.name, "F2", 9, INK], [`${it.inv_no || ""}${it.item_type ? " · " + it.item_type : ""}`, "F1", 8, MUTED]];
    const c2 = []; const bm = [it.brand, it.model_number].filter(Boolean).join(" ");
    if (bm) c2.push([bm, "F1", 9, INK]);
    if (it.description) c2.push([it.description, "F1", 8, MUTED]);
    const c3 = showPrice ? [[money(it.purchased_value), "F1", 9, INK]] : [];
    if (it.purchased_date) c3.push([niceDate(it.purchased_date), "F1", 8, MUTED]);
    if (it.purchased_condition) c3.push([it.purchased_condition, "F1", 8, MUTED]);
    const c4 = [[money(it.replacement_value), "F1", 9, INK]];
    if (it.replacement_date) c4.push([niceDate(it.replacement_date), "F1", 8, MUTED]);
    const laid = [c1, c2, c3, c4].map((c, i) => c.flatMap(([t, f, s, col]) => wrap(t, cols[i] - 6, f, s).map(ln => [ln, f, s, col])));
    const h = Math.max(...laid.map(c => c.reduce((a, l) => a + l[2] * 1.25, 0))) + 6;
    if (d.ensure(h + 4)) head();
    const y = d.y - 3;
    laid.forEach((c, i) => { let yy = y; for (const [ln, f, s, col] of c) { yy -= s * 1.25; d.text(xs[i] + 2, yy + s * 0.25, ln, f, s, col); } });
    d.y -= h; d.line(d.left, d.y, d.right, d.y, [0.9, 0.88, 0.84], 0.4);
  }
}

function cards(d, room, getImage, showPrice) {
  const iw = 96, ih = 74, tw = d.width - iw - 14;
  for (const it of room.items) {
    const lines = [[it.name, "F2", 10.5, INK]];
    const sub = [it.inv_no, it.item_type, [it.brand, it.model_number].filter(Boolean).join(" ")].filter(Boolean).join("  ·  ");
    if (sub) lines.push([sub, "F1", 8.5, MUTED]);
    if (it.description) lines.push([it.description, "F1", 9, INK]);
    const extra = [niceDate(it.purchased_date), it.purchased_condition].filter(Boolean);
    const paid = ((showPrice ? [money(it.purchased_value)] : []).concat(extra)).join("  ·  ");
    if (paid) lines.push(["Purchased: " + paid, "F1", 9, INK]);
    let rep = "Replacement: " + money(it.replacement_value);
    if (it.replacement_date) rep += `  (as of ${niceDate(it.replacement_date)})`;
    lines.push([rep, "F1", 9, INK]);
    if (it.similar_url) lines.push(["Similar item: " + it.similar_url, "F1", 7.5, MUTED]);
    const laid = lines.flatMap(([t, f, s, col]) => wrap(t, tw, f, s).map(ln => [ln, f, s, col]));
    const h = Math.max(ih, laid.reduce((a, l) => a + l[2] * 1.3, 0)) + 10;
    d.ensure(h);
    const top = d.y - 4;
    let img = null;
    if (it.image) { const raw = getImage(it.image); const parsed = raw && dataUrlBytes(raw); img = parsed && loadJpeg(parsed.bytes); }
    if (img) {
      const k = Math.min(iw / img.width, ih / img.height), w = img.width * k, hh = img.height * k;
      d.image(img, d.left + (iw - w) / 2, top - hh, w, hh);
    } else {
      d.rect(d.left, top - ih, iw, ih, [0.95, 0.94, 0.91]);
      d.text(d.left + 24, top - ih / 2 - 3, it.image ? "Photo not shown" : "No photo", "F1", 7.5, MUTED);
    }
    let y = top;
    for (const [ln, f, s, col] of laid) { y -= s * 1.3; d.text(d.left + iw + 14, y + s * 0.25, ln, f, s, col); }
    d.y -= h; d.line(d.left, d.y + 3, d.right, d.y + 3, [0.9, 0.88, 0.84], 0.4);
  }
}

export function buildPdf(data, {withPhotos = false, showPrice = true, getImage = () => null, today = new Date().toISOString().slice(0, 10)} = {}) {
  const d = new Document("Home inventory report - " + data.home.name, data.home.name + " inventory");
  summary(d, data, today, showPrice);
  const cols = [150, 190, 82, d.width - 150 - 190 - 82];
  for (const room of data.rooms) {
    roomHeader(d, room);
    if (!room.items.length) { d.paragraph("No items in this room.", "F1", 9, MUTED, {gap: 8}); continue; }
    if (withPhotos) cards(d, room, getImage, showPrice); else table(d, room, cols, showPrice);
    d.y -= 10;
  }
  if (!data.rooms.length) d.paragraph("This home has no rooms yet.", "F1", 10, MUTED);
  return d.render();
}
