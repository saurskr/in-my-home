# Home Inventory

Local-first app for cataloging household items. All data stays on your machine.
Python 3.9+ standard library only (http.server + sqlite3), vanilla JS frontend.

```bash
python3 server.py          # http://localhost:8000  (PORT=9000 to change)
python3 test_api.py        # app tests
python3 -m unittest discover -s receipt_module/tests -t .   # receipt module tests
```

UI styling comes from the shared design system (`static/ds/`, a vendored copy of github.com/saurskr/design-system: Warm Paper / Midnight themes, Auto/Light/Dark switch in the sidebar).

Data lives in `data/inventory.db` and `data/images/` (git-ignored; override with `INVENTORY_DATA_DIR`).
The server binds to 127.0.0.1 only. Add authentication before exposing it on the LAN.

## Model
homes → rooms → items. Deleting a home or room cascades (and removes image files).
Items get an auto `INV-000001` number and a `price_updated_at` stamp (set when a replacement price is entered or changed, or by sending `price_confirmed: true`); `replacement_value` defaults to `purchased_value`.
Item types: Appliance, Furniture, Electronics, Memorabilia, Media, Collectible, Clothing.

## API (JSON under /api)
- `GET /meta`
- `GET|POST /homes`, `GET|PUT|DELETE /homes/:id` Homes take a US address: `street`, `street2`, `city`, `state` (2-letter code, see `us_states` in `GET /meta`), `zip` (12345 or 12345-6789); responses include a formatted `address`. Older free-text addresses still display until replaced.
- `GET|POST /homes/:id/rooms` (POST `prefill: true` creates template items; room name picks the template)
- `GET|PUT|DELETE /homes/:id/insurance` (optional per-home policy: insurer, website (http/https), policy number, expiry date (`renewal_date`, YYYY-MM-DD), dwelling / contents / liability coverage, deductible, annual premium, up to 5 `riders` of `{name, limit}`, contact, notes; PUT merges the fields you send). `GET /homes` and `GET /homes/:id` include the home's `insurance`
- `GET|PUT|DELETE /rooms/:id` (PUT with `home_id` moves the room and its items)
- `GET|POST /rooms/:id/items`, `GET|PUT|DELETE /items/:id`
- `similar_url` must be http(s). Rooms and homes report purchased and replacement totals.
- `POST /items/:id/image?kind=photo|similar` with `{data: "<base64 data URL>"}`
- `POST /identify` with `{data: <image data URL>, provider: "ollama" | "anthropic"}` returns `{name, brand, item_type, description, search_query, provider, model}` for you to review before saving (save with `source: "vision"`)
- `POST /classify` `{data, provider?}` -> `{kind: receipt|item|other}`; `POST /receipt` `{data, provider?}` -> store, date, totals, `items`, `warnings` (see `receipt_module/`)
- `POST /link-info` `{url}` -> `{price, currency, title, host, source}` (price is `null` when the page does not publish one)
- `GET|PUT /settings` (Ollama address and model, Claude model, API key; the key is write-only and never returned), `GET /providers/status`

## Photo identification
Default: a local vision model through [Ollama](https://ollama.com) (`qwen2.5vl:7b`; use `qwen2.5vl:3b` on small GPUs).
```bash
ollama pull qwen2.5vl:7b
```
**Choosing a model:** *Settings* lists what is installed in Ollama (the same list `ollama list` prints, read from Ollama's local API) with size, parameters and whether each model can read images; press *Use* to switch. Install more with `ollama pull <name>` in a terminal, then press *Refresh list*. Only vision models work for photos (qwen2.5vl, gemma3, llama3.2-vision, minicpm-v ...). Nothing else changes: the same prompt and answer format are used for any vision model, though accuracy and speed differ.

Optional: Claude with your own API key (Settings page). It is off until a key is saved, a photo is sent only when you click
*Identify with Claude* and confirm, and the key is stored in `data/settings.json` (owner-only permissions) or read from `ANTHROPIC_API_KEY`.
Environment overrides: `OLLAMA_HOST`, `ANTHROPIC_API_KEY`, `ANTHROPIC_BASE_URL`, `INVENTORY_ALLOWED_HOSTS`.

Security: the API only accepts `Host: localhost|127.0.0.1|[::1]` (blocks DNS rebinding) and requires `Content-Type: application/json`
on writes (blocks cross-site form posts). Add more hosts with `INVENTORY_ALLOWED_HOSTS` and add authentication before LAN use.

## Adding items
- **Add item:** a compact form with the photo on the left and the fields on the right. Choosing a photo runs the local model
  (a spinning "LLM working" wheel shows over the photo) and fills in name, brand, type and description for you to check.
- **Add items by image:** choose up to 20 photos at once. Each is classified as an item photo or a **receipt**. Item photos become
  one row each; receipts are read line by line (quantity, price, category). Everything lands in one review table where you edit,
  untick and set quantities; nothing is saved until you click *Add*. Food and supplies on a receipt start unticked.
  "Read as receipt / item" re-reads an image if it was classified wrongly.
- Receipt reading lives in a standalone, reusable package: [`receipt_module/`](receipt_module/README.md).

## Item screen and editing
Each item has: Name, Room, Type, Brand, Model #, Description (up to 250 characters), Purchased price, Purchased date, Purchased condition, Replacement cost, Replacement cost date (when the cost was last updated; set automatically when you change the cost, or by hand) and a Link to a similar item.
The details panel is a summary card: photo, name and status chips on top; a Value block with the replacement cost, what you paid, a comparison bar and a large **Check current price** button; then the remaining details. The edit form lists every field one per line.

**Categories:** the Type list can be extended. Choose "New category…" in the Type list, or add and remove categories in Settings (a custom category can be removed once no item uses it). Photo identification and receipt reading use them too. Receipts fill in the purchased date. The photo model fills in the Model # when it can read one off the item.

The room page is split 50/50: the item list on the left, item details on the right (photos beside the facts). The list shows **10 or 25 items per
page** (your choice is remembered) with Prev / page numbers / Next; room totals always cover every item, not just the page. After you add or save an
item, the list jumps to the page it is on.

**Edit** (top of the item details) opens a drawer that slides in from the right, with Save, Cancel and Delete at the top. Esc, Cancel or a click
outside asks first if there are unsaved changes. The drawer is the shared tray component from the design system (`static/ds/components/tray/`).
To try the other styles, change `TRAY_VARIANT` in `static/index.html` to `"grow"` (widens out of the details panel) or `"instant"` (no motion).

## Text size
All text is one step (1px) bigger than the design system's defaults. The app's own CSS carries the bump directly; for the vendored design-system files
`static/type-bump.css` is generated by `python3 tools/type_bump.py` (`--step 2` for two sizes up, `--step 0` to undo). Re-run it after re-copying `static/ds/`.

## Capture now, scan later
Like an expense report: take the photos first, let the AI read them afterwards (even days later, when you have internet or are at the computer with Ollama).
- Open **Capture** in the top bar, pick the room, and press **Take photos** (the phone camera) or **Choose from gallery**. Each photo is saved immediately as a *waiting* item in that room, named like "Photo Nov 25, 2:02 PM #1". No internet or AI is needed for this; in the browser/phone version it works fully offline. Nothing is sent anywhere.
- Later, press **Scan waiting photos** on the same page (choose the local model, or Claude if you added a key). For each photo the AI decides what it is and **reclassifies** it:
  - an object: the item's name, brand, model #, type and description are filled in (the price is left alone);
  - a receipt: it becomes one item per purchased line (quantity copies, purchase date and price from the receipt; the receipt photo stays with the first line);
  - anything else: marked *Couldn't read*.
- Everything the AI produced goes to **Check the AI's result**: review and press **Looks right** (or **Confirm all**), open the item to edit it, or **Scan again**. Items show a "Waiting to scan" / "Check AI result" tag in the room list and a banner in the details. The number on the Capture tab counts photos still to scan or check. Nothing is lost if the AI is down: the item stays waiting.
- Claude needs the internet. If the phone is offline when you choose Claude, scanning waits and your photos stay safe.
- API: items accept `scan_state` (`pending`, or `""` to confirm), `POST /api/items/<id>/scan {"provider": "ollama"|"anthropic"}`, `GET /api/scan-queue`.

## Browser version (GitHub Pages, phone app)
The same screens also run **without Python**, entirely in the browser. Open `static/index.html` from any web host (GitHub Pages, or `python3 -m http.server --directory static`) and the page notices there is no server and keeps your data in the browser's own storage (IndexedDB) instead. Add `?local=1` to force this mode even when the Python server is running.

- **Demo or your own data:** an empty copy offers "Try the demo" (a made-up home with photos, prices and insurance) or "Start with my own home". The demo shows a banner and can be erased in one click.
- **Install on Android/iOS:** it is a PWA. In Chrome use the menu → "Install app" (or Settings → Your data → Install as an app). Once opened it also works offline.
- **Your data stays on that device.** Settings → Your data lets you export a backup (.json, with photos, never including your API key) and import it on another device. Clearing the site's data erases everything, so export now and then.
- **Report and PDF** work the same (the PDF is built in the browser). **Save to Google Drive** uses Google's sign-in popup with your own *Web application* client id (add the site address under "Authorized JavaScript origins").
- **Photo reading** works with Claude (your own key, sent only to Anthropic) and with Ollama if it runs on the same device and was started with `OLLAMA_ORIGINS="*"`. A phone can use Claude.
- **Not available in this version:** reading a price or picture from a pasted web link (browsers cannot read other sites' pages). Type the price in, or use the Python app.
- **Publishing:** `.github/workflows/pages.yml` deploys the `static/` folder. Turn it on once under the repository's Settings → Pages → Source: "GitHub Actions".
- **Code:** `static/local/` is a JavaScript port of `server.py` (`api.js`), `pdfwriter.py`/`report.py` (`pdf.js`) and `receipt_module/` (`ai.js`). Tests: `node --test tests/`.

## Report (PDF) and Google Drive
Open a home and press **Report** in the top bar. The page lists every item by room and offers:
- **Include item photos**: with photos, each item is a block with its picture; without, a compact table.
- **Hide the purchased price**: leaves out what was paid (per item and in the totals). Purchase date and condition stay. The replacement cost is always shown.
- **Download PDF**: a real PDF made by the app itself (`pdfwriter.py`, no extra packages). Photos are embedded when they are JPEG, or PNG without transparency; other formats are embedded only if the optional Pillow package is installed, otherwise the report says "Photo not shown". New uploads are converted to JPEG, so this only affects photos uploaded by older versions.
- **Save to Google Drive**: uploads the same PDF to a "Home Inventory" folder in your Drive.

The PDF is also available directly: `/api/homes/<id>/report.pdf?photos=1&hide_purchase_price=1`.

**Google Drive setup (once).** The app needs your own free Google sign-in; it cannot ship one. In Settings, the Google Drive panel lists the steps: create a Google Cloud project, enable the Drive API, create an OAuth client of type *Desktop app*, paste the client id and secret, press **Connect Google Drive** and approve. The app asks only for the `drive.file` permission, which lets it see just the files it creates. The secret and the sign-in token are stored in `data/settings.json` (owner-only) and never sent back to the browser. Nothing is uploaded unless you press the button.
The Drive upload is covered by tests against a pretend Google server, but I could not test it against the real Google from the development environment, so treat the first real upload as the final check.

## Find similar, and prices from links
The item's equivalent-item picture is taken from the page behind its link (the product picture the page publishes) when you save a link, or with **Pull from link**; you can also upload one. In the item pane, **Check current price** opens a box with the search text built from the item's name, brand, model # and description (repeats removed, up to 200 characters; you can edit it or untick the description), condition and max price, and buttons for the stores (Google Shopping, Amazon, Walmart, Target, Best Buy, eBay or the web). Each opens in a new tab. If the item already has a saved link, the price is read from it first and you can set the replacement cost to it with one click. Only your search words leave
your computer, and only when you click. Price and condition filters are added to the address where the store supports them; I could not
check every store's parameters, so treat them as a convenience.

Paste a product link (in the pane's *Paste a link* row, or into *Link to a similar item* in the form) and the app reads the page for its price (`POST /api/link-info`).
It reads what the site publishes for browsers and search engines: JSON-LD, Open Graph / product meta tags, `itemprop="price"`, and the page's embedded product data
(for example Next.js `__NEXT_DATA__`), where it only takes the price of the product whose id is in the link, never another product's. It never guesses from page text.
Many large retailers (Walmart, Amazon ...) put a "robot check" in front of automated requests; the app detects that and says so instead of reporting "no price".
USD prices are offered as the replacement cost; other currencies are shown but not used. The fetch uses a plain desktop-browser identity (override with `INVENTORY_FETCH_UA`),
refuses private/local addresses, re-checks every redirect, reads at most 3 MB and only HTML, and does not use a system proxy.

**Why a link gave no price?** Run the diagnostic on the same computer that runs the app: `python3 tools/check_link.py "<url>"` prints the HTTP status, whether a robot check
came back, which price sources the page has, and what was found. Add `--save page.html` to keep what the site returned.

## Roadmap
1. Compare candidate vision models on your own photos (small bake-off script).
2. Phone access (PWA over LAN) with auth.
3. Search/filter, CSV export for insurance, per-home totals report.
