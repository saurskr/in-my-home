"""Save files to the user's own Google Drive (standard library only).

How it is set up: the user creates their own OAuth client ("Desktop app") in Google Cloud, turns on the
Drive API, and pastes the client id and secret into Settings. The app asks for the narrow `drive.file`
scope, which only lets it see files it created itself. The sign-in uses the loopback redirect with PKCE.
"""
import base64
import hashlib
import json
import os
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request

AUTH_URL = os.environ.get("INVENTORY_GOOGLE_AUTH_URL", "https://accounts.google.com/o/oauth2/v2/auth")
TOKEN_URL = os.environ.get("INVENTORY_GOOGLE_TOKEN_URL", "https://oauth2.googleapis.com/token")
API_URL = os.environ.get("INVENTORY_GOOGLE_API_URL", "https://www.googleapis.com")
SCOPE = "https://www.googleapis.com/auth/drive.file"
FOLDER_NAME = "In My Home"
TIMEOUT = 60
PENDING_SECONDS = 600
_pending = {}        # state -> (code_verifier, redirect_uri, expires)


class DriveError(Exception):
    def __init__(self, message, status=502):
        super().__init__(message)
        self.message, self.status = message, status


def _b64(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def start_auth(client_id, redirect_uri):
    """The Google sign-in address to open in a browser; remembers the state and PKCE verifier."""
    now = time.time()
    for k in [k for k, v in _pending.items() if v[2] < now]:
        del _pending[k]
    state, verifier = secrets.token_urlsafe(24), secrets.token_urlsafe(48)
    _pending[state] = (verifier, redirect_uri, now + PENDING_SECONDS)
    q = urllib.parse.urlencode({
        "client_id": client_id, "redirect_uri": redirect_uri, "response_type": "code", "scope": SCOPE,
        "access_type": "offline", "prompt": "consent", "state": state,
        "code_challenge": _b64(hashlib.sha256(verifier.encode()).digest()), "code_challenge_method": "S256"})
    return AUTH_URL + "?" + q


def take_pending(state):
    """(verifier, redirect_uri) for a state we issued, once; None for anything else."""
    item = _pending.pop(state or "", None)
    if not item or item[2] < time.time():
        return None
    return item[0], item[1]


def _post_form(url, fields):
    req = urllib.request.Request(url, data=urllib.parse.urlencode(fields).encode(), method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read())
        except ValueError:
            detail = {}
        err = detail.get("error")
        if err == "invalid_grant":
            raise DriveError("Google sign-in has expired or was revoked. Connect Google Drive again in Settings.", 401)
        if err in ("invalid_client", "unauthorized_client"):
            raise DriveError("Google did not accept the client id or secret. Check them in Settings.", 400)
        raise DriveError("Google refused the request (%s)." % (detail.get("error_description") or err or e.code))
    except (urllib.error.URLError, OSError, ValueError):
        raise DriveError("Could not reach Google. Check your internet connection.")


def exchange_code(client_id, client_secret, code, verifier, redirect_uri):
    out = _post_form(TOKEN_URL, {"client_id": client_id, "client_secret": client_secret, "code": code,
                                 "code_verifier": verifier, "redirect_uri": redirect_uri, "grant_type": "authorization_code"})
    if not out.get("refresh_token"):
        raise DriveError("Google did not return a long-lived sign-in. Remove this app at "
                         "myaccount.google.com/permissions and connect again.")
    return out["refresh_token"]


def access_token(client_id, client_secret, refresh_token):
    out = _post_form(TOKEN_URL, {"client_id": client_id, "client_secret": client_secret,
                                 "refresh_token": refresh_token, "grant_type": "refresh_token"})
    if not out.get("access_token"):
        raise DriveError("Google did not return an access token.")
    return out["access_token"]


def _api(method, path, token, body=None, headers=None, query=None):
    url = API_URL + path + ("?" + urllib.parse.urlencode(query) if query else "")
    h = {"Authorization": "Bearer " + token}
    h.update(headers or {})
    req = urllib.request.Request(url, data=body, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            msg = (json.loads(e.read()).get("error") or {}).get("message") or ""
        except ValueError:
            msg = ""
        if e.code in (401, 403):
            raise DriveError("Google Drive said no" + (": " + msg if msg else "") +
                             ". Make sure the Drive API is turned on for your Google Cloud project.", 403)
        raise DriveError("Google Drive returned an error (%s)%s" % (e.code, ": " + msg if msg else ""))
    except (urllib.error.URLError, OSError, ValueError):
        raise DriveError("Could not reach Google Drive. Check your internet connection.")


def find_or_make_folder(token, name=FOLDER_NAME):
    q = "name = '%s' and mimeType = 'application/vnd.google-apps.folder' and trashed = false" % name.replace("'", "\\'")
    found = _api("GET", "/drive/v3/files", token, query={"q": q, "fields": "files(id,name)", "spaces": "drive"})
    if found.get("files"):
        return found["files"][0]["id"]
    made = _api("POST", "/drive/v3/files", token, json.dumps({"name": name, "mimeType": "application/vnd.google-apps.folder"}).encode(),
                {"Content-Type": "application/json"}, {"fields": "id"})
    return made["id"]


def upload(token, filename, data, mime="application/pdf", folder_id=None):
    """Create a file in Drive (inside the folder when given). Returns {"id", "name", "link"}."""
    meta = {"name": filename}
    if folder_id:
        meta["parents"] = [folder_id]
    boundary = "hi" + secrets.token_hex(12)
    body = (("--%s\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n%s\r\n--%s\r\nContent-Type: %s\r\n\r\n"
             % (boundary, json.dumps(meta), boundary, mime)).encode() + data + ("\r\n--%s--" % boundary).encode())
    out = _api("POST", "/upload/drive/v3/files", token, body,
               {"Content-Type": "multipart/related; boundary=" + boundary},
               {"uploadType": "multipart", "fields": "id,name,webViewLink"})
    return {"id": out.get("id"), "name": out.get("name") or filename,
            "link": out.get("webViewLink") or ("https://drive.google.com/file/d/%s/view" % out.get("id"))}
