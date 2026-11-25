// Where the in-browser version keeps its data.
// db     : one JSON object (homes, rooms, items, insurance, categories, settings) - text only, small
// images : photos, one record each (data URLs), so a save never rewrites every picture
// MemoryStore is for tests; IdbStore uses the browser's IndexedDB.

export class MemoryStore {
  constructor() { this.db = null; this.images = new Map(); }
  async load() { return {db: this.db ? JSON.parse(JSON.stringify(this.db)) : null, images: new Map(this.images)}; }
  async saveDb(db) { this.db = JSON.parse(JSON.stringify(db)); }
  async putImage(name, data) { this.images.set(name, data); }
  async delImage(name) { this.images.delete(name); }
  async clear() { this.db = null; this.images.clear(); }
}

export class IdbStore {
  constructor(name = "home-inventory") {   // the storage name is kept so existing data is not orphaned by the rename
    this.name = name; this._open = null; }
  _db() {
    if (!this._open) this._open = new Promise((ok, no) => {
      const req = indexedDB.open(this.name, 1);
      req.onupgradeneeded = () => { const d = req.result; d.createObjectStore("meta"); d.createObjectStore("images"); };
      req.onsuccess = () => ok(req.result);
      req.onerror = () => no(req.error || new Error("This browser would not open its storage."));
    });
    return this._open;
  }
  async _tx(store, mode, fn) {
    const d = await this._db();
    return new Promise((ok, no) => {
      const tx = d.transaction(store, mode), os = tx.objectStore(store);
      let result; const r = fn(os); if (r) r.onsuccess = () => { result = r.result; };
      tx.oncomplete = () => ok(result); tx.onerror = () => no(tx.error); tx.onabort = () => no(tx.error);
    });
  }
  async load() {
    const db = (await this._tx("meta", "readonly", os => os.get("db"))) || null;
    const d = await this._db();
    const images = new Map();
    await new Promise((ok, no) => {
      const req = d.transaction("images", "readonly").objectStore("images").openCursor();
      req.onsuccess = () => { const c = req.result; if (c) { images.set(c.key, c.value); c.continue(); } else ok(); };
      req.onerror = () => no(req.error);
    });
    return {db, images};
  }
  saveDb(db) { return this._tx("meta", "readwrite", os => os.put(db, "db")); }
  putImage(name, data) { return this._tx("images", "readwrite", os => os.put(data, name)); }
  delImage(name) { return this._tx("images", "readwrite", os => os.delete(name)); }
  async clear() { await this._tx("meta", "readwrite", os => os.clear()); await this._tx("images", "readwrite", os => os.clear()); }
}
