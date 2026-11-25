"""A very small PDF writer using only the Python standard library.

Supports: letter pages, Helvetica / Helvetica-Bold text with word wrapping, lines, filled rectangles,
page numbers, and pictures (JPEG as is; PNG without transparency or palette; anything else only if the
optional Pillow package happens to be installed). That is all the inventory report needs.
"""
import io
import re
import struct
import zlib

PAGE_W, PAGE_H = 612.0, 792.0   # US Letter, points

# Helvetica advance widths (1/1000 em) for ASCII 32..126, from the standard font metrics.
_REG = [278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278, 278,
        556, 556, 556, 556, 556, 556, 556, 556, 556, 556, 278, 278, 584, 584, 584, 556,
        1015, 667, 667, 722, 722, 667, 611, 778, 722, 278, 500, 667, 556, 833, 722, 778,
        667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 278, 278, 278, 469, 556,
        333, 556, 556, 500, 556, 556, 278, 556, 556, 222, 222, 500, 222, 833, 556, 556,
        556, 556, 333, 500, 278, 556, 500, 722, 500, 500, 500, 334, 260, 334, 584]
_BOLD = [278, 333, 474, 556, 556, 889, 722, 238, 333, 333, 389, 584, 278, 333, 278, 278,
         556, 556, 556, 556, 556, 556, 556, 556, 556, 556, 333, 333, 584, 584, 584, 611,
         975, 722, 722, 722, 722, 667, 611, 778, 722, 278, 556, 722, 611, 833, 722, 778,
         667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 333, 278, 333, 584, 556,
         333, 556, 611, 556, 611, 556, 333, 611, 611, 278, 278, 556, 278, 889, 611, 611,
         611, 611, 389, 556, 333, 611, 556, 778, 556, 556, 500, 389, 280, 389, 584]
_WIDTHS = {"F1": _REG, "F2": _BOLD}


def encode(text):
    """Text as WinAnsi (cp1252) bytes; characters that font cannot show become '?'."""
    text = (text or "").replace("\t", " ")
    text = re.sub(r"[\r\n]+", " ", text)
    return text.encode("cp1252", "replace")


def text_width(text, font="F1", size=10):
    w = _WIDTHS[font]
    total = 0
    for b in encode(text):
        total += w[b - 32] if 32 <= b <= 126 else (1000 if b in (0x85, 0x97) else 556)
    return total * size / 1000.0


def wrap(text, width, font="F1", size=10):
    """Break text into lines no wider than width points (long words are split)."""
    lines = []
    for para in str(text or "").split("\n"):
        cur = ""
        for word in para.split():
            while text_width(word, font, size) > width:       # a word longer than the line
                cut = len(word)
                while cut > 1 and text_width(word[:cut], font, size) > width:
                    cut -= 1
                if cur:
                    lines.append(cur)
                    cur = ""
                lines.append(word[:cut])
                word = word[cut:]
            trial = (cur + " " + word).strip()
            if cur and text_width(trial, font, size) > width:
                lines.append(cur)
                cur = word
            else:
                cur = trial
        lines.append(cur)
    return lines or [""]


def _esc(b):
    return b.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


class Image:
    """A picture prepared for embedding, or None if it cannot be (see load_image)."""
    def __init__(self, width, height, data, colorspace, filt, parms=None, bpc=8):
        self.width, self.height, self.data = width, height, data
        self.colorspace, self.filter, self.parms, self.bpc = colorspace, filt, parms, bpc


def _jpeg(raw):
    i = 2
    while i + 9 < len(raw):
        if raw[i] != 0xFF:
            i += 1
            continue
        marker = raw[i + 1]
        if marker in (0xC0, 0xC1, 0xC2):
            h, w = struct.unpack(">HH", raw[i + 5:i + 9])
            comps = raw[i + 9]
            cs = {1: "/DeviceGray", 3: "/DeviceRGB"}.get(comps)
            if not cs:
                return None            # CMYK and friends
            return Image(w, h, raw, cs, "/DCTDecode")
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        i += 2 + struct.unpack(">H", raw[i + 2:i + 4])[0]
    return None


def _png(raw):
    pos, idat, hdr = 8, [], None
    while pos + 8 <= len(raw):
        n, kind = struct.unpack(">I4s", raw[pos:pos + 8])
        body = raw[pos + 8:pos + 8 + n]
        if kind == b"IHDR":
            hdr = struct.unpack(">IIBBBBB", body)
        elif kind == b"IDAT":
            idat.append(body)
        pos += 12 + n
    if not hdr or not idat:
        return None
    w, h, depth, ctype, _, _, interlace = hdr
    if depth != 8 or interlace or ctype not in (0, 2):
        return None                    # palette, alpha, 16-bit or interlaced: leave to Pillow
    colors = 1 if ctype == 0 else 3
    return Image(w, h, b"".join(idat), "/DeviceGray" if colors == 1 else "/DeviceRGB", "/FlateDecode",
                 "<< /Predictor 15 /Colors %d /BitsPerComponent 8 /Columns %d >>" % (colors, w))


def load_image(raw):
    """Prepare image bytes for the PDF; None when the format cannot be embedded."""
    img = None
    if raw[:3] == b"\xff\xd8\xff":
        img = _jpeg(raw)
    elif raw[:8] == b"\x89PNG\r\n\x1a\n":
        img = _png(raw)
    if img:
        return img
    try:                                # optional: convert anything else with Pillow when available
        from PIL import Image as PIL
        im = PIL.open(io.BytesIO(raw))
        im.thumbnail((1400, 1400))
        if im.mode in ("RGBA", "LA", "P"):
            bg = PIL.new("RGB", im.size, "white")
            im = im.convert("RGBA")
            bg.paste(im, mask=im.split()[3])
            im = bg
        else:
            im = im.convert("RGB")
        out = io.BytesIO()
        im.save(out, "JPEG", quality=85)
        return _jpeg(out.getvalue())
    except Exception:
        return None


class Document:
    def __init__(self, title="Document", margin=48, footer=""):
        self.title, self.margin, self.footer = title, margin, footer
        self.pages = []                 # each: {"ops": [bytes], "images": {name: Image}}
        self.y = 0
        self.new_page()

    # ----- layout helpers -----
    @property
    def left(self):
        return self.margin

    @property
    def right(self):
        return PAGE_W - self.margin

    @property
    def width(self):
        return PAGE_W - 2 * self.margin

    @property
    def bottom(self):
        return self.margin + 14         # room for the footer

    def new_page(self):
        self.pages.append({"ops": [], "images": {}})
        self.y = PAGE_H - self.margin

    def ensure(self, height):
        """Start a new page unless `height` points still fit."""
        if self.y - height < self.bottom:
            self.new_page()
            return True
        return False

    # ----- drawing -----
    def _op(self, s):
        self.pages[-1]["ops"].append(s if isinstance(s, bytes) else s.encode("latin-1"))

    @staticmethod
    def _rgb(c):
        return "%.3f %.3f %.3f" % c

    def text(self, x, y, s, font="F1", size=10, color=(0, 0, 0)):
        self._op(b"BT " + self._rgb(color).encode() + b" rg /" + font.encode() + (" %.2f Tf %.2f %.2f Td (" % (size, x, y)).encode()
                 + _esc(encode(s)) + b") Tj ET")

    def text_right(self, xr, y, s, font="F1", size=10, color=(0, 0, 0)):
        self.text(xr - text_width(s, font, size), y, s, font, size, color)

    def paragraph(self, s, font="F1", size=10, color=(0, 0, 0), x=None, width=None, leading=None, gap=0):
        x = self.left if x is None else x
        width = self.width if width is None else width
        leading = leading or size * 1.3
        for line in wrap(s, width, font, size):
            self.ensure(leading)
            self.y -= leading
            self.text(x, self.y + size * 0.25, line, font, size, color)
        self.y -= gap

    def line(self, x1, y1, x2, y2, color=(0.8, 0.8, 0.8), width=0.5):
        self._op("%s RG %.2f w %.2f %.2f m %.2f %.2f l S" % (self._rgb(color), width, x1, y1, x2, y2))

    def rect(self, x, y, w, h, fill):
        self._op("%s rg %.2f %.2f %.2f %.2f re f" % (self._rgb(fill), x, y, w, h))

    def image(self, img, x, y, w, h):
        page = self.pages[-1]
        name = "Im%d" % (len(page["images"]) + 1)
        page["images"][name] = img
        self._op("q %.2f 0 0 %.2f %.2f %.2f cm /%s Do Q" % (w, h, x, y, name))

    # ----- output -----
    def render(self):
        objs = []                       # list of bytes (object bodies), index+1 = object number

        def add(body):
            objs.append(body)
            return len(objs)

        add(b"")                        # 1: catalog (filled later)
        add(b"")                        # 2: pages (filled later)
        f1 = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
        f2 = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>")
        kids = []
        total = len(self.pages)
        for n, page in enumerate(self.pages, 1):
            self.pages.index(page)
            ops = list(page["ops"])
            foot = "%s   Page %d of %d" % (self.footer, n, total) if self.footer else "Page %d of %d" % (n, total)
            ops.append(b"BT 0.45 0.45 0.45 rg /F1 8.00 Tf %.2f %.2f Td (" % (self.left, self.margin - 6) + _esc(encode(foot)) + b") Tj ET")
            stream = zlib.compress(b"\n".join(ops))
            content = add(b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(stream) + stream + b"\nendstream")
            xobj = []
            for name, img in page["images"].items():
                head = "<< /Type /XObject /Subtype /Image /Width %d /Height %d /ColorSpace %s /BitsPerComponent %d /Filter %s" % (
                    img.width, img.height, img.colorspace, img.bpc, img.filter)
                if img.parms:
                    head += " /DecodeParms " + img.parms
                head += " /Length %d >>\nstream\n" % len(img.data)
                xobj.append("/%s %d 0 R" % (name, add(head.encode() + img.data + b"\nendstream")))
            res = "<< /Font << /F1 %d 0 R /F2 %d 0 R >> /XObject << %s >> >>" % (f1, f2, " ".join(xobj))
            kids.append(add(("<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %.0f %.0f] /Resources %s /Contents %d 0 R >>"
                             % (PAGE_W, PAGE_H, res, content)).encode()))
        objs[1] = ("<< /Type /Pages /Count %d /Kids [%s] >>" % (len(kids), " ".join("%d 0 R" % k for k in kids))).encode()
        objs[0] = b"<< /Type /Catalog /Pages 2 0 R >>"
        info = add(b"<< /Title (" + _esc(encode(self.title)) + b") /Producer (In My Home) >>")
        out = io.BytesIO()
        out.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets = []
        for i, body in enumerate(objs, 1):
            offsets.append(out.tell())
            out.write(b"%d 0 obj\n" % i + body + b"\nendobj\n")
        xref = out.tell()
        out.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1))
        for off in offsets:
            out.write(b"%010d 00000 n \n" % off)
        out.write(b"trailer\n<< /Size %d /Root 1 0 R /Info %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, info, xref))
        return out.getvalue()
