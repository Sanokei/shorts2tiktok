"""Local bridge that copies your own YouTube Shorts onto your TikTok account.

Runs entirely on this machine: it talks to the YouTube Data API and the TikTok
Content Posting API directly, nothing is routed through a third party.
"""
import hashlib
import json
import mimetypes
import queue
import re
import secrets
import shutil
import string
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
APP = ROOT / "app"
DOWNLOADS = ROOT / "downloads"
CONFIG_PATH = ROOT / "config.json"

UI_PORT = 8712
CALLBACK_PORT = 8713
# Default for Login Kit's Desktop tab, which is the only one that takes a
# loopback address. If the console refuses it, any https URL you control works
# instead: TikTok lands there with the code in the query string and you paste
# the address back into the Connect panel.
LOOPBACK_REDIRECT = "http://127.0.0.1:%d/callback" % CALLBACK_PORT

TT_AUTHORIZE = "https://www.tiktok.com/v2/auth/authorize/"
TT_TOKEN = "https://open.tiktokapis.com/v2/oauth/token/"
TT_CREATOR_INFO = "https://open.tiktokapis.com/v2/post/publish/creator_info/query/"
TT_INIT_DIRECT = "https://open.tiktokapis.com/v2/post/publish/video/init/"
TT_INIT_INBOX = "https://open.tiktokapis.com/v2/post/publish/inbox/video/init/"
TT_STATUS = "https://open.tiktokapis.com/v2/post/publish/status/fetch/"

SCOPES_DIRECT = "user.info.basic,video.publish,video.upload"
SCOPES_INBOX = "user.info.basic,video.upload"

DEFAULT_CONFIG = {
    "youtube_api_key": "",
    "youtube_channel": "",
    "tiktok_client_key": "",
    "tiktok_client_secret": "",
    "redirect_uri": LOOPBACK_REDIRECT,
    "caption_template": "{title} {tags}",
    "extra_hashtags": "",
    "mode": "inbox",
    "privacy_level": "SELF_ONLY",
    "disable_comment": False,
    "disable_duet": False,
    "disable_stitch": False,
    "tokens": {},
    "ported": {},
}

_cfg_lock = threading.Lock()


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_PATH.exists():
        try:
            cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            pass
    return cfg


def save_config(cfg):
    with _cfg_lock:
        CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def update_config(**fields):
    cfg = load_config()
    cfg.update(fields)
    save_config(cfg)
    return cfg


class ApiError(Exception):
    pass


def http_json(url, method="GET", body=None, headers=None, timeout=60):
    data = None
    hdrs = dict(headers or {})
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        hdrs.setdefault("Content-Type", "application/json; charset=UTF-8")
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        try:
            parsed = json.loads(detail)
            err = parsed.get("error")
            if isinstance(err, dict):
                detail = err.get("message") or err.get("code") or detail
            elif isinstance(err, str):
                detail = (err + ": " + parsed.get("error_description", "")).strip(": ")
        except json.JSONDecodeError:
            pass
        raise ApiError("%s %s" % (exc.code, detail[:400])) from None
    except urllib.error.URLError as exc:
        raise ApiError("network error: %s" % (exc.reason,)) from None


# ---------------------------------------------------------------- YouTube ---

YT_API = "https://www.googleapis.com/youtube/v3"


def yt_get(path, api_key, **params):
    params["key"] = api_key
    return http_json(YT_API + "/" + path + "?" + urllib.parse.urlencode(params))


def parse_duration(iso):
    m = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso or "")
    if not m:
        return 0
    d, h, mi, s = (int(x) if x else 0 for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + s


def resolve_channel(api_key, raw):
    raw = (raw or "").strip()
    if not raw:
        raise ApiError("No YouTube channel set.")
    if "youtube.com" in raw:
        m = re.search(r"channel/(UC[\w-]{22})", raw)
        if m:
            raw = m.group(1)
        else:
            m = re.search(r"@([\w.-]+)", raw)
            raw = "@" + m.group(1) if m else raw
    if raw.startswith("UC") and len(raw) == 24:
        data = yt_get("channels", api_key, part="snippet,contentDetails", id=raw)
    elif raw.startswith("@"):
        data = yt_get("channels", api_key, part="snippet,contentDetails", forHandle=raw)
    else:
        data = yt_get("channels", api_key, part="snippet,contentDetails", forUsername=raw)
    items = data.get("items") or []
    if not items:
        raise ApiError("YouTube returned no channel for %r." % (raw,))
    ch = items[0]
    return {
        "id": ch["id"],
        "title": ch["snippet"]["title"],
        "handle": ch["snippet"].get("customUrl", ""),
        "uploads": ch["contentDetails"]["relatedPlaylists"]["uploads"],
    }


def ytdlp_binary():
    local = ROOT / ".venv" / "Scripts" / "yt-dlp.exe"
    if local.exists():
        return str(local)
    return shutil.which("yt-dlp")


def ytdlp_short_ids(channel_id, limit=400):
    """Ask yt-dlp which videos live on the channel's Shorts tab.

    The Data API has no Shorts flag, so this is what separates a Short from any
    other sub-3-minute upload. Returns None when yt-dlp cannot answer.
    """
    exe = ytdlp_binary()
    if not exe:
        return None
    url = "https://www.youtube.com/channel/%s/shorts" % channel_id
    try:
        out = subprocess.run(
            [exe, "--flat-playlist", "--print", "%(id)s",
             "--playlist-end", str(limit), "--no-warnings", url],
            capture_output=True, text=True, timeout=180)
    except (subprocess.TimeoutExpired, OSError):
        return None
    ids = set(line.strip() for line in out.stdout.splitlines() if line.strip())
    return ids or None


def list_shorts(cfg, max_videos=400):
    api_key = cfg["youtube_api_key"]
    if not api_key:
        raise ApiError("Add your YouTube API key first.")
    channel = resolve_channel(api_key, cfg["youtube_channel"])
    video_ids, page = [], None
    while len(video_ids) < max_videos:
        extra = {"pageToken": page} if page else {}
        data = yt_get("playlistItems", api_key, part="contentDetails",
                      playlistId=channel["uploads"], maxResults=50, **extra)
        for item in data.get("items", []):
            video_ids.append(item["contentDetails"]["videoId"])
        page = data.get("nextPageToken")
        if not page:
            break

    shorts_tab = ytdlp_short_ids(channel["id"])
    ported = cfg.get("ported", {})
    videos = []
    for i in range(0, len(video_ids), 50):
        data = yt_get("videos", api_key, part="snippet,contentDetails,statistics",
                      id=",".join(video_ids[i:i + 50]))
        for item in data.get("items", []):
            seconds = parse_duration(item["contentDetails"].get("duration"))
            vid = item["id"]
            is_short = vid in shorts_tab if shorts_tab else 0 < seconds <= 180
            if not is_short:
                continue
            snip = item["snippet"]
            thumbs = snip.get("thumbnails", {})
            thumb = (thumbs.get("medium") or thumbs.get("default") or {}).get("url", "")
            videos.append({
                "id": vid,
                "title": snip.get("title", ""),
                "description": snip.get("description", ""),
                "published": snip.get("publishedAt", ""),
                "seconds": seconds,
                "views": int(item.get("statistics", {}).get("viewCount", 0) or 0),
                "thumbnail": thumb,
                "ported": ported.get(vid),
            })
    videos.sort(key=lambda v: v["published"], reverse=True)
    return {"channel": channel, "videos": videos,
            "detection": "shorts tab" if shorts_tab else "duration only"}


HASHTAG_RE = re.compile(r"#[^\s#]+")


def build_caption(video, cfg):
    tags = HASHTAG_RE.findall(video.get("description", ""))
    raw_extra = re.split(r"[,\s]+", cfg.get("extra_hashtags", "") or "")
    extra = [t if t.startswith("#") else "#" + t for t in raw_extra if t]
    seen, merged = set(), []
    for tag in tags + extra:
        if tag.lower() not in seen:
            seen.add(tag.lower())
            merged.append(tag)
    template = cfg.get("caption_template") or "{title} {tags}"
    try:
        caption = template.format(title=video.get("title", ""),
                                  tags=" ".join(merged),
                                  description=video.get("description", ""))
    except (KeyError, IndexError):
        caption = video.get("title", "")
    return re.sub(r"[ \t]+", " ", caption).strip()[:2200]


def download_short(video_id, log):
    path = DOWNLOADS / (video_id + ".mp4")
    if path.exists() and path.stat().st_size > 0:
        log("using cached download")
        return path
    exe = ytdlp_binary()
    if not exe:
        raise ApiError("yt-dlp is not installed. Run: pip install yt-dlp")
    cmd = [exe, "-f", "bv*[ext=mp4][vcodec^=avc1]+ba[ext=m4a]/b[ext=mp4]/b",
           "--merge-output-format", "mp4", "--no-playlist", "--no-warnings",
           "-o", str(DOWNLOADS / "%(id)s.%(ext)s"),
           "https://www.youtube.com/shorts/" + video_id]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    if proc.returncode != 0 or not path.exists():
        raise ApiError("download failed: %s" % (proc.stderr or proc.stdout)[-300:].strip())
    log("downloaded %.1f MB" % (path.stat().st_size / 1e6))
    return path


# ----------------------------------------------------------------- TikTok ---

_pending_auth = {}


VERIFIER_CHARS = string.ascii_letters + string.digits + "-._~"


def is_loopback(uri):
    host = urllib.parse.urlparse(uri).hostname or ""
    return host in ("127.0.0.1", "localhost", "::1")


def tiktok_auth_url(cfg, mode):
    """Authorization code flow against whichever redirect URI is configured.

    A loopback redirect is Login Kit's desktop flow, which requires PKCE, and
    TikTok wants the challenge as a hex digest rather than the base64url form
    most other providers expect. An https redirect is the web flow, where
    sending a challenge TikTok did not ask for only creates ways to fail.
    """
    state = secrets.token_urlsafe(24)
    redirect = cfg.get("redirect_uri") or LOOPBACK_REDIRECT
    params = {
        "client_key": cfg["tiktok_client_key"],
        "response_type": "code",
        "scope": SCOPES_DIRECT if mode == "direct" else SCOPES_INBOX,
        "redirect_uri": redirect,
        "state": state,
    }
    if is_loopback(redirect):
        verifier = "".join(secrets.choice(VERIFIER_CHARS) for _ in range(64))
        _pending_auth[state] = verifier
        params["code_challenge"] = hashlib.sha256(verifier.encode()).hexdigest()
        params["code_challenge_method"] = "S256"
    return TT_AUTHORIZE + "?" + urllib.parse.urlencode(params), state


def _post_form(url, form):
    req = urllib.request.Request(
        url, data=urllib.parse.urlencode(form).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            payload = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        raise ApiError("TikTok rejected the request: %s" % exc.read().decode()[:400]) from None
    if payload.get("error"):
        raise ApiError("%s: %s" % (payload["error"], payload.get("error_description", "")))
    payload["obtained_at"] = int(time.time())
    return payload


def tiktok_exchange_code(cfg, code, state):
    form = {
        "client_key": cfg["tiktok_client_key"],
        "client_secret": cfg["tiktok_client_secret"],
        "code": urllib.parse.unquote(code),
        "grant_type": "authorization_code",
        "redirect_uri": cfg.get("redirect_uri") or LOOPBACK_REDIRECT,
    }
    verifier = _pending_auth.pop(state, None)
    if verifier:
        form["code_verifier"] = verifier
    payload = _post_form(TT_TOKEN, form)
    update_config(tokens=payload)
    return payload


def tiktok_refresh(cfg):
    tokens = cfg.get("tokens") or {}
    if not tokens.get("refresh_token"):
        raise ApiError("TikTok is not connected yet.")
    payload = _post_form(TT_TOKEN, {
        "client_key": cfg["tiktok_client_key"],
        "client_secret": cfg["tiktok_client_secret"],
        "grant_type": "refresh_token",
        "refresh_token": tokens["refresh_token"],
    })
    update_config(tokens=payload)
    return payload


def tiktok_bearer():
    cfg = load_config()
    tokens = cfg.get("tokens") or {}
    if not tokens.get("access_token"):
        raise ApiError("TikTok is not connected yet.")
    age = int(time.time()) - tokens.get("obtained_at", 0)
    if age > tokens.get("expires_in", 86400) - 300:
        tokens = tiktok_refresh(cfg)
    return {"Authorization": "Bearer " + tokens["access_token"]}


def tiktok_creator_info():
    return http_json(TT_CREATOR_INFO, method="POST", body={}, headers=tiktok_bearer())


CHUNK = 64 * 1024 * 1024


def tiktok_upload(path, caption, cfg, log):
    size = path.stat().st_size
    if size <= CHUNK:
        chunk_size, chunk_count = size, 1
    else:
        chunk_size, chunk_count = CHUNK, size // CHUNK
    source = {"source": "FILE_UPLOAD", "video_size": size,
              "chunk_size": chunk_size, "total_chunk_count": chunk_count}

    if cfg.get("mode") == "direct":
        init_url = TT_INIT_DIRECT
        body = {
            "post_info": {
                "title": caption,
                "privacy_level": cfg.get("privacy_level", "SELF_ONLY"),
                "disable_comment": bool(cfg.get("disable_comment")),
                "disable_duet": bool(cfg.get("disable_duet")),
                "disable_stitch": bool(cfg.get("disable_stitch")),
            },
            "source_info": source,
        }
    else:
        init_url = TT_INIT_INBOX
        body = {"source_info": source}

    init = http_json(init_url, method="POST", body=body, headers=tiktok_bearer())
    data = init.get("data") or {}
    upload_url = data.get("upload_url")
    publish_id = data.get("publish_id")
    if not upload_url:
        raise ApiError("TikTok refused the upload: %s" % json.dumps(init)[:300])
    log("upload slot opened")

    with path.open("rb") as fh:
        for index in range(chunk_count):
            start = index * chunk_size
            end = size - 1 if index == chunk_count - 1 else start + chunk_size - 1
            fh.seek(start)
            payload = fh.read(end - start + 1)
            req = urllib.request.Request(upload_url, data=payload, method="PUT", headers={
                "Content-Type": "video/mp4",
                "Content-Length": str(len(payload)),
                "Content-Range": "bytes %d-%d/%d" % (start, end, size),
            })
            with urllib.request.urlopen(req, timeout=900) as resp:
                resp.read()
            log("sent chunk %d/%d" % (index + 1, chunk_count))

    for _ in range(40):
        time.sleep(3)
        try:
            status = http_json(TT_STATUS, method="POST",
                               body={"publish_id": publish_id}, headers=tiktok_bearer())
        except ApiError:
            continue
        state = (status.get("data") or {}).get("status", "")
        if state in ("PUBLISH_COMPLETE", "SEND_TO_USER_INBOX"):
            return publish_id, state
        if state == "FAILED":
            reason = (status.get("data") or {}).get("fail_reason", "unknown")
            raise ApiError("TikTok processing failed: %s" % reason)
    return publish_id, "STILL_PROCESSING"


# -------------------------------------------------------------- job runner --

JOBS = {}
JOB_QUEUE = queue.Queue()


def run_one(video, entry):
    cfg = load_config()

    def log(msg):
        entry["log"].append(msg)
        entry["message"] = msg

    entry["status"] = "downloading"
    log("fetching from YouTube")
    path = download_short(video["id"], log)
    entry["status"] = "uploading"
    caption = build_caption(video, cfg)
    entry["caption"] = caption
    # Inbox uploads carry no caption, you type it in the app, so keep a copy
    # on disk next to the video as well as in the page.
    path.with_suffix(".caption.txt").write_text(caption, encoding="utf-8")
    publish_id, state = tiktok_upload(path, caption, cfg, log)
    entry["status"] = "done"
    entry["message"] = ("posted to TikTok" if state == "PUBLISH_COMPLETE"
                        else "waiting in your TikTok inbox")
    ported = load_config().get("ported", {})
    ported[video["id"]] = {"publish_id": publish_id, "state": state, "at": int(time.time())}
    update_config(ported=ported)


def worker():
    while True:
        job_id, videos = JOB_QUEUE.get()
        job = JOBS[job_id]
        for video in videos:
            entry = job["items"][video["id"]]
            try:
                run_one(video, entry)
            except Exception as exc:
                entry["status"] = "error"
                entry["message"] = str(exc)
            time.sleep(11)  # stay under TikTok's per-minute request ceiling
        job["done"] = True
        JOB_QUEUE.task_done()


# ----------------------------------------------------------------- routing --

def json_response(handler, payload, code=200):
    body = json.dumps(payload).encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


class UIHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def _read_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(length) or "{}") if length else {}

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        route = url.path
        params = urllib.parse.parse_qs(url.query)
        try:
            if route == "/":
                return self._serve_file(APP / "index.html")
            if route.startswith("/app/"):
                return self._serve_file(APP / route[5:])
            if route == "/api/config":
                cfg = load_config()
                tokens = cfg.pop("tokens", {})
                cfg["secret_set"] = bool(cfg.pop("tiktok_client_secret", ""))
                cfg["connected"] = bool(tokens.get("access_token"))
                cfg.setdefault("redirect_uri", LOOPBACK_REDIRECT)
                cfg["loopback_redirect"] = LOOPBACK_REDIRECT
                cfg["ported_count"] = len(cfg.get("ported", {}))
                cfg.pop("ported", None)
                return json_response(self, cfg)
            if route == "/api/shorts":
                return json_response(self, list_shorts(load_config()))
            if route == "/api/creator":
                return json_response(self, tiktok_creator_info())
            if route == "/api/job":
                job = JOBS.get((params.get("id") or [""])[0])
                return json_response(self, job or {"error": "unknown job"})
        except ApiError as exc:
            return json_response(self, {"error": str(exc)}, 400)
        except Exception as exc:
            return json_response(self, {"error": repr(exc)}, 500)
        self.send_error(404)

    def do_POST(self):
        route = urllib.parse.urlparse(self.path).path
        try:
            body = self._read_body()
            if route == "/api/config":
                cfg = load_config()
                for key in ("youtube_api_key", "youtube_channel", "tiktok_client_key",
                            "tiktok_client_secret", "caption_template", "extra_hashtags",
                            "mode", "privacy_level", "disable_comment", "disable_duet",
                            "disable_stitch", "redirect_uri"):
                    if key in body and body[key] != "":
                        cfg[key] = body[key]
                save_config(cfg)
                return json_response(self, {"ok": True})
            if route == "/api/auth/start":
                cfg = load_config()
                if not cfg["tiktok_client_key"] or not cfg["tiktok_client_secret"]:
                    raise ApiError("Add your TikTok client key and secret first.")
                url, _ = tiktok_auth_url(cfg, cfg.get("mode", "inbox"))
                return json_response(self, {"url": url})
            if route == "/api/auth/paste":
                qs = urllib.parse.parse_qs(urllib.parse.urlparse(body.get("url", "")).query)
                code = (qs.get("code") or [""])[0]
                if not code:
                    raise ApiError("That URL has no code in it.")
                tiktok_exchange_code(load_config(), code, (qs.get("state") or [""])[0])
                return json_response(self, {"ok": True})
            if route == "/api/disconnect":
                update_config(tokens={})
                return json_response(self, {"ok": True})
            if route == "/api/port":
                videos = body.get("videos") or []
                if not videos:
                    raise ApiError("No videos selected.")
                job_id = secrets.token_hex(8)
                JOBS[job_id] = {
                    "id": job_id,
                    "done": False,
                    "order": [v["id"] for v in videos],
                    "items": dict((v["id"], {"id": v["id"], "title": v.get("title", ""),
                                             "status": "queued", "message": "",
                                             "caption": "", "log": []}) for v in videos),
                }
                JOB_QUEUE.put((job_id, videos))
                return json_response(self, {"job": job_id})
        except ApiError as exc:
            return json_response(self, {"error": str(exc)}, 400)
        except Exception as exc:
            return json_response(self, {"error": repr(exc)}, 500)
        self.send_error(404)

    def _serve_file(self, path):
        path = path.resolve()
        if not path.is_file() or path.parent != APP.resolve():
            return self.send_error(404)
        data = path.read_bytes()
        ctype = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)


class CallbackHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_GET(self):
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        code = (qs.get("code") or [""])[0]
        if code:
            try:
                tiktok_exchange_code(load_config(), code, (qs.get("state") or [""])[0])
                message = "TikTok connected. You can close this tab."
            except ApiError as exc:
                message = "Could not finish sign-in: %s" % exc
        else:
            reason = (qs.get("error_description") or qs.get("error") or ["no reason given"])[0]
            message = "TikTok did not send an authorization code: " + reason
        body = ("<meta charset=utf-8><body style='font:16px system-ui;background:#111;"
                "color:#eee;padding:3rem'><p>" + message + "</p>").encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def start_callback_server():
    srv = ThreadingHTTPServer(("127.0.0.1", CALLBACK_PORT), CallbackHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()


def main():
    DOWNLOADS.mkdir(exist_ok=True)
    threading.Thread(target=worker, daemon=True).start()
    start_callback_server()
    srv = ThreadingHTTPServer(("127.0.0.1", UI_PORT), UIHandler)
    print("\n  Shorts to TikTok is running at http://127.0.0.1:%d/" % UI_PORT)
    print("  Redirect URI in use: %s\n" % (load_config().get("redirect_uri")
                                           or LOOPBACK_REDIRECT))
    if "--no-open" not in sys.argv:
        webbrowser.open("http://127.0.0.1:%d/" % UI_PORT)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("stopped")


if __name__ == "__main__":
    main()
