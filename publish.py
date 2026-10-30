#!/usr/bin/env python3
"""
Bellows Games social poster.

Publishes posts from the queue/ folder to the Bellows Games Facebook Page and
the linked Instagram account (@bellows30) through Meta's Graph API.

Each post is one JSON file in queue/ (see queue/EXAMPLE.json.example).
A post is published once its "publish_at" time has passed. Results are written
back into the same file so nothing is ever posted twice.

Usage:
  python publish.py            publish everything that is due
  python publish.py --check    only test the token and show the connected accounts
  python publish.py --dry-run  show what would be published, without posting
  python publish.py --test-video media/x.mp4
                               let Instagram process a video as a Reel, WITHOUT publishing it

A post can have "image" (a .jpg) or "video" (an .mp4, 9:16, H.264/AAC, 3-90 s).
Video files are uploaded directly (no public URL needed) and become an Instagram Reel (also shown in the feed) and a Facebook Page video.

Settings (GitHub: Settings -> Secrets and variables -> Actions):
  META_PAGE_TOKEN  (secret)  a never-expiring Page access token for the Page
  GRAPH_VERSION    (optional variable) Graph API version, default below
"""
import glob
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

GRAPH_VERSION = os.environ.get("GRAPH_VERSION", "").strip() or "v23.0"
GRAPH = f"https://graph.facebook.com/{GRAPH_VERSION}"
ROOT = os.path.dirname(os.path.abspath(__file__))
QUEUE_DIR = os.path.join(ROOT, "queue")
PLATFORMS = ("instagram", "facebook")
MAX_ATTEMPTS = 3


# ---------------------------------------------------------------- Graph API

def token():
    t = os.environ.get("META_PAGE_TOKEN", "").strip()
    if not t:
        sys.exit("Missing META_PAGE_TOKEN. Add it under Settings -> Secrets and "
                 "variables -> Actions in the GitHub repo.")
    return t


def graph(method, path, params=None):
    """Call the Graph API. The token goes in a header, never in the URL."""
    params = params or {}
    url = f"{GRAPH}/{path}"
    data = None
    if method == "GET":
        if params:
            url += "?" + urllib.parse.urlencode(params)
    else:
        data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token()}")
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        try:
            msg = json.loads(body)["error"]["message"]
        except Exception:
            msg = body[:500]
        raise RuntimeError(f"Meta API error {e.code} on {path}: {msg}") from None


def accounts():
    """Return (page_id, page_name, ig_id, ig_username) for the token's Page."""
    me = graph("GET", "me", {"fields": "id,name,instagram_business_account{id,username}"})
    ig = me.get("instagram_business_account") or {}
    return me["id"], me.get("name", ""), ig.get("id"), ig.get("username")


# ---------------------------------------------------------------- media URLs

def public_image_url(path):
    """Images live in this repo; Meta fetches them from GitHub's public raw URL."""
    repo = os.environ.get("GITHUB_REPOSITORY")
    branch = os.environ.get("GITHUB_REF_NAME") or "main"
    if not repo:
        raise RuntimeError("GITHUB_REPOSITORY is not set (it is set automatically "
                           "inside GitHub Actions).")
    return (f"https://raw.githubusercontent.com/{repo}/{branch}/"
            + urllib.parse.quote(path.lstrip("/")))


def public_video_url(path):
    """Videos are served through jsDelivr's GitHub CDN, which sends the correct video/mp4 type."""
    repo = os.environ.get("GITHUB_REPOSITORY")
    # Pin to the exact commit when we know it: jsDelivr caches branch URLs for hours.
    ref = os.environ.get("GITHUB_SHA") or os.environ.get("GITHUB_REF_NAME") or "main"
    if not repo:
        raise RuntimeError("GITHUB_REPOSITORY is not set.")
    return f"https://cdn.jsdelivr.net/gh/{repo}@{ref}/" + urllib.parse.quote(path.lstrip("/"))


def http_post_raw(url, body, headers):
    req = urllib.request.Request(url, data=body, method="POST")
    for k, v in headers.items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Upload error {e.code} on {url.split('?')[0]}: {e.read().decode(errors='replace')[:400]}") from None


def ig_reel_container(ig_id, path, caption):
    """Create a Reel container and upload the video file straight to Instagram (resumable upload)."""
    container = graph("POST", f"{ig_id}/media",
                      {"media_type": "REELS", "upload_type": "resumable",
                       "caption": caption, "share_to_feed": "true"})["id"]
    with open(os.path.join(ROOT, path), "rb") as f:
        data = f.read()
    http_post_raw(f"https://rupload.facebook.com/ig-api-upload/{GRAPH_VERSION}/{container}", data,
                  {"Authorization": f"OAuth {token()}", "offset": "0", "file_size": str(len(data))})
    return container


def fb_video_upload(page_id, path, description):
    """Upload the video file straight to the Facebook Page (multipart)."""
    boundary = "----bellows" + str(int(time.time() * 1000))
    with open(os.path.join(ROOT, path), "rb") as f:
        data = f.read()
    parts = [f"--{boundary}\r\nContent-Disposition: form-data; name=\"description\"\r\n\r\n".encode()
             + description.encode("utf-8") + b"\r\n",
             f"--{boundary}\r\nContent-Disposition: form-data; name=\"source\"; filename=\"{os.path.basename(path)}\"\r\n"
             f"Content-Type: video/mp4\r\n\r\n".encode() + data + b"\r\n",
             f"--{boundary}--\r\n".encode()]
    res = http_post_raw(f"https://graph-video.facebook.com/{GRAPH_VERSION}/{page_id}/videos", b"".join(parts),
                        {"Authorization": f"Bearer {token()}",
                         "Content-Type": f"multipart/form-data; boundary={boundary}"})
    return res.get("id")


def ig_publish(ig_id, container, tries=4, delay=8):
    """media_publish sometimes answers 'Media ID is not available' right after FINISHED; retry a few times."""
    for i in range(tries):
        try:
            return ig_publish(ig_id, container)
        except RuntimeError as e:
            if "not available" not in str(e) or i == tries - 1:
                raise
            time.sleep(delay)


def wait_for_container(container, tries=60, delay=5):
    for _ in range(tries):
        status = graph("GET", container, {"fields": "status_code,status"})
        code = status.get("status_code")
        if code == "FINISHED":
            return
        if code in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"Instagram could not process the media ({code}): {status.get('status', '')}")
        time.sleep(delay)
    raise RuntimeError("Instagram took too long to process the media.")


# ---------------------------------------------------------------- publishing

def publish_facebook(page_id, post):
    caption = post.get("caption", "")
    if post.get("video"):
        return fb_video_upload(page_id, post["video"], caption)
    if post.get("image"):
        res = graph("POST", f"{page_id}/photos",
                    {"url": public_image_url(post["image"]), "message": caption})
        return res.get("post_id") or res["id"]
    if not caption:
        raise RuntimeError("A Facebook post needs a caption or an image.")
    return graph("POST", f"{page_id}/feed", {"message": caption})["id"]


def publish_instagram(ig_id, post):
    if post.get("video"):
        container = ig_reel_container(ig_id, post["video"], post.get("caption", ""))
        wait_for_container(container)
        return ig_publish(ig_id, container)
    image = post.get("image")
    if not image:
        raise RuntimeError("Instagram posts need an image or a video.")
    if not image.lower().endswith((".jpg", ".jpeg")):
        raise RuntimeError("Instagram's API only accepts JPEG images (.jpg).")
    container = graph("POST", f"{ig_id}/media",
                      {"image_url": public_image_url(image),
                       "caption": post.get("caption", "")})["id"]
    wait_for_container(container, tries=20, delay=3)
    return ig_publish(ig_id, container)


# ---------------------------------------------------------------- queue

def parse_time(value):
    t = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if t.tzinfo is None:
        raise ValueError("publish_at needs a timezone, e.g. 2026-10-27T18:00:00+01:00")
    return t


def load_queue():
    posts = []
    for path in sorted(glob.glob(os.path.join(QUEUE_DIR, "*.json"))):
        with open(path, encoding="utf-8") as f:
            posts.append((path, json.load(f)))
    return posts


def save(path, post):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(post, f, ensure_ascii=False, indent=2)
        f.write("\n")


def pending_platforms(post):
    done = post.get("published", {})
    attempts = post.get("attempts", {})
    return [p for p in post.get("platforms", PLATFORMS)
            if p in PLATFORMS and p not in done and attempts.get(p, 0) < MAX_ATTEMPTS]


def validate(post):
    problems = []
    if not post.get("publish_at"):
        problems.append("missing publish_at")
    else:
        try:
            parse_time(post["publish_at"])
        except ValueError as e:
            problems.append(str(e))
    unknown = [p for p in post.get("platforms", []) if p not in PLATFORMS]
    if unknown:
        problems.append(f"unknown platforms {unknown}")
    image, video = post.get("image"), post.get("video")
    if image and not os.path.exists(os.path.join(ROOT, image)):
        problems.append(f"image not found: {image}")
    if video:
        if not os.path.exists(os.path.join(ROOT, video)):
            problems.append(f"video not found: {video}")
        elif not video.lower().endswith(".mp4"):
            problems.append("video must be .mp4")
    elif "instagram" in post.get("platforms", PLATFORMS):
        if not image:
            problems.append("instagram needs an image or a video")
        elif not image.lower().endswith((".jpg", ".jpeg")):
            problems.append("instagram needs a .jpg image")
    return problems


def main():
    args = set(sys.argv[1:])
    if "--check" in args:
        page_id, page_name, ig_id, ig_user = accounts()
        print(f"Facebook Page: {page_name} ({page_id})")
        print(f"Instagram:     @{ig_user} ({ig_id})" if ig_id
              else "Instagram:     NOT linked to this Page")
        print("Connection OK." if ig_id else "Link Instagram to the Page, then check again.")
        return 0 if ig_id else 1

    if "--test-video" in args:
        path = sys.argv[sys.argv.index("--test-video") + 1]
        _, _, ig_id, _ = accounts()
        print("Testing (not publishing):", path)
        container = ig_reel_container(ig_id, path, "test")
        wait_for_container(container)
        print("Instagram accepted and processed the video. Container NOT published:", container)
        return 0

    dry = "--dry-run" in args
    now = datetime.now(timezone.utc)
    queue = load_queue()
    due = []
    for path, post in queue:
        name = os.path.basename(path)
        problems = validate(post)
        if problems:
            print(f"SKIP {name}: " + "; ".join(problems))
            continue
        todo = pending_platforms(post)
        if not todo:
            continue
        when = parse_time(post["publish_at"])
        if when > now:
            print(f"wait {name}: due {when.isoformat()} ({', '.join(todo)})")
            continue
        due.append((path, post, todo))

    if not due:
        print("Nothing due right now.")
        return 0
    if dry:
        for path, post, todo in due:
            print(f"WOULD POST {os.path.basename(path)} to {', '.join(todo)}: "
                  f"{post.get('caption', '')[:80]!r}")
        return 0

    page_id, _, ig_id, _ = accounts()
    failures = 0
    for path, post, todo in due:
        name = os.path.basename(path)
        for platform in todo:
            try:
                if platform == "facebook":
                    media_id = publish_facebook(page_id, post)
                else:
                    if not ig_id:
                        raise RuntimeError("Instagram is not linked to the Page.")
                    media_id = publish_instagram(ig_id, post)
                post.setdefault("published", {})[platform] = {
                    "id": media_id, "at": datetime.now(timezone.utc).isoformat()}
                post.get("errors", {}).pop(platform, None)
                print(f"POSTED {name} -> {platform} ({media_id})")
            except Exception as e:
                failures += 1
                post.setdefault("attempts", {})[platform] = post.get("attempts", {}).get(platform, 0) + 1
                post.setdefault("errors", {})[platform] = str(e)
                print(f"FAILED {name} -> {platform}: {e}")
            save(path, post)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
