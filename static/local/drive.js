// Save a file to the user's own Google Drive from the browser.
// Uses Google's sign-in popup (Google Identity Services) with the narrow drive.file permission:
// the page can only see files it created itself. The user supplies their own "Web application" client id.

const SCOPE = "https://www.googleapis.com/auth/drive.file";
export const FOLDER_NAME = "In My Home";

export function createBrowserDrive({apiUrl = "https://www.googleapis.com", getToken = null, fetchImpl = (...a) => fetch(...a)} = {}) {
  let token = null, expires = 0, loading = null;

  function loadGis() {
    if (window.google && window.google.accounts && window.google.accounts.oauth2) return Promise.resolve();
    if (!loading) loading = new Promise((ok, no) => {
      const s = document.createElement("script"); s.src = "https://accounts.google.com/gsi/client"; s.async = true;
      s.onload = ok; s.onerror = () => { loading = null; no(Object.assign(new Error("Could not load Google sign-in. Check your internet connection."), {status: 502})); };
      document.head.appendChild(s);
    });
    return loading;
  }
  async function popupToken(clientId) {
    await loadGis();
    return new Promise((ok, no) => {
      const client = window.google.accounts.oauth2.initTokenClient({client_id: clientId, scope: SCOPE,
        callback: r => r.error ? no(Object.assign(new Error(r.error === "access_denied" ? "Google Drive access was not allowed." : "Google sign-in failed: " + r.error), {status: 400})) : ok(r),
        error_callback: e => no(Object.assign(new Error(e && e.type === "popup_closed" ? "The Google sign-in window was closed." : "Google sign-in could not start. Allow pop-ups for this site and try again."), {status: 400}))});
      client.requestAccessToken({prompt: token ? "" : "consent"});
    });
  }
  async function accessToken(clientId) {
    if (token && Date.now() < expires - 60000) return token;
    const r = await (getToken ? getToken(clientId) : popupToken(clientId));
    token = r.access_token; expires = Date.now() + (Number(r.expires_in) || 3600) * 1000; return token;
  }
  async function api(method, path, tok, body, headers = {}, query = null) {
    const url = apiUrl + path + (query ? "?" + new URLSearchParams(query) : "");
    let res;
    try { res = await fetchImpl(url, {method, headers: {Authorization: "Bearer " + tok, ...headers}, body}); }
    catch (e) { throw Object.assign(new Error("Could not reach Google Drive. Check your internet connection."), {status: 502}); }
    if (!res.ok) {
      let msg = ""; try { msg = ((await res.json()).error || {}).message || ""; } catch (e) { /* no body */ }
      if (res.status === 401) token = null;
      throw Object.assign(new Error(res.status === 401 || res.status === 403
        ? "Google Drive said no" + (msg ? ": " + msg : "") + ". Make sure the Drive API is turned on for your Google Cloud project."
        : `Google Drive returned an error (${res.status})${msg ? ": " + msg : ""}`), {status: res.status === 401 || res.status === 403 ? 403 : 502});
    }
    return res.json();
  }
  async function folder(tok) {
    const q = `name = '${FOLDER_NAME}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false`;
    const found = await api("GET", "/drive/v3/files", tok, undefined, {}, {q, fields: "files(id,name)", spaces: "drive"});
    if (found.files && found.files.length) return found.files[0].id;
    const made = await api("POST", "/drive/v3/files", tok, JSON.stringify({name: FOLDER_NAME, mimeType: "application/vnd.google-apps.folder"}), {"Content-Type": "application/json"}, {fields: "id"});
    return made.id;
  }
  return {
    async upload({clientId, filename, bytes, mime = "application/pdf"}) {
      const tok = await accessToken(clientId), parent = await folder(tok);
      const boundary = "hi" + Math.random().toString(16).slice(2);
      const enc = new TextEncoder();
      const head = enc.encode(`--${boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n${JSON.stringify({name: filename, parents: [parent]})}\r\n--${boundary}\r\nContent-Type: ${mime}\r\n\r\n`);
      const tail = enc.encode(`\r\n--${boundary}--`);
      const body = new Uint8Array(head.length + bytes.length + tail.length); body.set(head, 0); body.set(bytes, head.length); body.set(tail, head.length + bytes.length);
      const out = await api("POST", "/upload/drive/v3/files", tok, body, {"Content-Type": "multipart/related; boundary=" + boundary}, {uploadType: "multipart", fields: "id,name,webViewLink"});
      return {id: out.id, name: out.name || filename, link: out.webViewLink || `https://drive.google.com/file/d/${out.id}/view`, folder: FOLDER_NAME};
    },
    // used by Settings "Connect": ask for the permission once so problems show up early
    async connect(clientId) { await accessToken(clientId); return true; },
  };
}
