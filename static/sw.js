// Makes the in-browser version work offline once it has been opened. Online, the newest files are used;
// offline, the last copies are. (Not used when the page is served by the Python app.)
const CACHE = "in-my-home-v1";
const CORE = ["./", "index.html", "manifest.webmanifest", "type-bump.css", "icons/icon-192.png", "icons/icon-512.png",
  "local/api.js", "local/ai.js", "local/pdf.js", "local/store.js", "local/sample.js", "local/drive.js",
  "ds/base/base.css", "ds/tokens/tokens.css", "ds/components/card/card.css", "ds/components/panel/panel.css",
  "ds/components/shell/shell.css", "ds/components/status/status.css", "ds/components/tray/tray.css", "ds/components/tray/tray.js"];

self.addEventListener("install", e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(CORE)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", e => {
  e.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))).then(() => self.clients.claim()));
});
self.addEventListener("fetch", e => {
  const req = e.request, url = new URL(req.url);
  if (req.method !== "GET" || url.origin !== location.origin) return;      // Anthropic, Ollama, Google calls go straight out
  e.respondWith(fetch(req).then(res => {
    if (res.ok) { const copy = res.clone(); caches.open(CACHE).then(c => c.put(req, copy)); }
    return res;
  }).catch(() => caches.match(req).then(hit => hit || (req.mode === "navigate" ? caches.match("index.html") : Response.error()))));
});
