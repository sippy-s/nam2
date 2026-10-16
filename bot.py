#!/usr/bin/env python3
# bot.py — GitHub Actions Edition (Fixed Capture)
"""Namasha comment bot - Auto mode with robust capture."""

import asyncio
import json
import random
import re
import sys
import time
import urllib.parse
from pathlib import Path

try:
    import httpx
except ImportError:
    print("Missing httpx. Run: pip install httpx")
    sys.exit(1)

BASE = Path(__file__).parent
CAPTURE_FILE = BASE / "capture.json"
CAPTURE_BACKUP = BASE / "capture_backup.json"
COMMENT_FILE = BASE / "comment.txt"
CONFIG_FILE = BASE / "config.json"

CHARS = "ԖӜӕӗӘәӚӛӜӝӞӟӠӡӢӣӤӥӦӦӻӼӽӾӿԀԁԂԃԄԅԆԇԈԉԊԞԝԜԛԚԙԘԘԖԖԕԨԩԪԫԬԭԮԯԱՄՄՃՂՁՂՂՂՃՄՅՆՇՈՊաա՟՞՝՜՛՚ՙՖՕՔՔՓՒՑըըէէէըթժիլլխծկհձղղֆօքփւցրվվսռ֔֓֒չո"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"

RESERVED_HASHES = {
    "v", "video", "videos", "edit", "editvideo", "delete",
    "user", "users", "profile", "login", "logout", "signup",
    "search", "tag", "tags", "category", "playlist", "channel",
    "page", "home", "index", "embed", "share", "comment",
    "submitcomment", "toggleplaylist",
}

# ================ COMMENT INPUT SELECTORS ================
# لیست بزرگ selectors - از خاص به عام
COMMENT_SELECTORS = [
    # Namasha-specific
    "input.comment-input",
    "textarea.comment-input",
    ".comment-form input[name='Text']",
    ".comment-form textarea[name='Text']",
    "#commentText",
    "#comment-input",
    ".comment-box textarea",
    ".comment-box input",
    ".new-comment textarea",
    ".new-comment input",
    # Generic by name
    "input[name='Text']",
    "textarea[name='Text']",
    "input[name='text']",
    "textarea[name='text']",
    "input[name='Comment']",
    "textarea[name='Comment']",
    "input[name='comment']",
    "textarea[name='comment']",
    # Generic by id
    "textarea#Text",
    "textarea#text",
    "textarea#comment",
    "textarea#Comment",
    # By placeholder
    "textarea[placeholder*='نظر']",
    "textarea[placeholder*='کامنت']",
    "textarea[placeholder*='comment']",
    "textarea[placeholder*='Comment']",
    "input[placeholder*='نظر']",
    "input[placeholder*='کامنت']",
    "input[placeholder*='comment']",
    # By role
    "textarea[role='textbox']",
    "div[contenteditable='true']",
    # Form-based
    "form.comment-form textarea",
    "form.comment-form input",
    "form[action*='submitComment'] textarea",
    "form[action*='submitComment'] input",
]

# الگوهای مختلف برای پیدا کردن endpoint کامنت
SUBMIT_URL_PATTERNS = [
    r"submitComment\?id=(\d+)",
    r"submitComment[^?]*\?[^&]*id=(\d+)",
    r"/Comment/Submit\?id=(\d+)",
    r"/api/comment[^?]*\?[^&]*id=(\d+)",
    r"AddComment[^?]*\?[^&]*id=(\d+)",
]


# ================ CONFIG ================
def load_config():
    if not CONFIG_FILE.exists():
        print("config.json not found.")
        sys.exit(1)
    cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    cfg.setdefault("mode", "channel")
    cfg.setdefault("channel_url", "")
    cfg.setdefault("video_urls", [])
    cfg.setdefault("max_videos", 5)
    cfg.setdefault("comments_per_cookie_per_video", {})
    cfg.setdefault("concurrency", 50)
    return cfg


# ================ HELPERS ================
def make_random(n=2048):
    return "".join(random.choice(CHARS) for _ in range(n))


def get_comment_text():
    if COMMENT_FILE.exists():
        t = COMMENT_FILE.read_text(encoding="utf-8").strip()
        if t:
            return t + make_random(2048 - len(t)) if len(t) < 2048 else t
    return make_random(2048)


def find_cookie_files(max_n=10):
    files = []
    base = BASE / "cookies.json"
    if base.exists():
        files.append(base)
    for i in range(1, max_n + 1):
        p = BASE / f"cookies{i}.json"
        if p.exists():
            files.append(p)
    return files


def load_cookies_from(path):
    p = Path(path)
    if not p.exists():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if isinstance(raw, dict) and "cookies" in raw:
        raw = raw["cookies"]
    return {c["name"]: str(c["value"]) for c in raw if c.get("name")}


def cookies_to_pw(raw):
    out = []
    for c in raw:
        item = {
            "name": c["name"], "value": str(c["value"]),
            "domain": c["domain"], "path": c.get("path", "/"),
        }
        exp = c.get("expires") or c.get("expirationDate")
        if exp:
            if exp > 1e12:
                exp /= 1000
            item["expires"] = exp
        if c.get("httpOnly"):
            item["httpOnly"] = True
        if c.get("secure"):
            item["secure"] = True
        ss = str(c.get("sameSite", "")).lower()
        if ss in ("lax", "strict", "none"):
            item["sameSite"] = ss.capitalize()
        out.append(item)
    return out


def _load_token_from_backup():
    if CAPTURE_BACKUP.exists():
        try:
            old = json.loads(CAPTURE_BACKUP.read_text(encoding="utf-8"))
            body = old.get("body") or ""
            m = re.search(r"__RequestVerificationToken=([^&]*)", body)
            if m and m.group(1):
                return m.group(1)
        except Exception:
            pass
    return ""


def is_valid_hash(h):
    if not h or len(h) < 6:
        return False
    return h.lower() not in RESERVED_HASHES


# ================ VALIDATE capture.json ================
def validate_capture(cap):
    """چک کن capture.json واقعاً مربوط به submitComment هست یا نه"""
    if not cap:
        return False, "empty"
    url = cap.get("url", "")
    if "submitComment" not in url and "SubmitComment" not in url and "AddComment" not in url:
        return False, f"URL doesn't contain submitComment: {url}"
    if "yektanet" in url.lower() or "google-analytics" in url.lower():
        return False, f"URL is a tracker, not namasha: {url}"
    if "namasha.com" not in url:
        return False, f"URL is not on namasha.com: {url}"
    if not cap.get("body"):
        return False, "no body"
    return True, "ok"


# ================ BODY BUILDER ================
def build_body(template, new_text):
    if not template:
        return json.dumps({"Text": new_text}, ensure_ascii=False), "application/json"
    try:
        data = json.loads(template)
        if isinstance(data, dict):
            replaced = False
            for k, v in list(data.items()):
                if isinstance(v, str) and "test_capture" in v.lower():
                    data[k] = new_text
                    replaced = True
                    break
            if not replaced:
                for k in ("Text", "text", "comment", "Comment", "message", "content", "body"):
                    if k in data:
                        data[k] = new_text
                        replaced = True
                        break
            if not replaced:
                data["Text"] = new_text
            return json.dumps(data, ensure_ascii=False), "application/json"
    except json.JSONDecodeError:
        pass
    try:
        params = urllib.parse.parse_qs(template, keep_blank_values=True)
        replaced = False
        for k in list(params.keys()):
            v = params[k]
            if v and "test_capture" in v[0].lower():
                params[k] = [new_text]
                replaced = True
                break
        if not replaced:
            for k in ("Text", "text", "comment", "message"):
                if k in params:
                    params[k] = [new_text]
                    replaced = True
                    break
        return urllib.parse.urlencode(params, doseq=True), "application/x-www-form-urlencoded"
    except Exception:
        pass
    return json.dumps({"Text": new_text}, ensure_ascii=False), "application/json"


# ================ PROBE ================
async def probe_namasha():
    print("\nProbing namasha.com...")
    headers = {"User-Agent": UA, "Accept-Language": "fa-IR,fa;q=0.9"}
    try:
        async with httpx.AsyncClient(headers=headers, timeout=15.0, follow_redirects=True) as client:
            r = await client.get("https://www.namasha.com/")
            print(f"   Status: {r.status_code}")
            text_lower = r.text.lower()
            if "cloudflare" in text_lower or "just a moment" in text_lower:
                print("   Cloudflare challenge - IP blocked")
                return False
            if r.status_code in (403, 429):
                print("   Blocked")
                return False
            if r.status_code == 200:
                print("   namasha.com reachable")
                return True
            return False
    except Exception as e:
        print(f"   Probe failed: {e}")
        return False


# ================ VIDEO ID RESOLVER ================
async def resolve_video_id(video_url, cookie_files):
    cookies = load_cookies_from(cookie_files[0]) if cookie_files else {}
    headers = {
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    try:
        async with httpx.AsyncClient(cookies=cookies, headers=headers, timeout=30.0, follow_redirects=True) as client:
            r = await client.get(video_url)
            if r.status_code != 200:
                return None
            for pat in SUBMIT_URL_PATTERNS:
                m = re.search(pat, r.text)
                if m:
                    return m.group(1)
            # fallback patterns
            for pat in [r"stats\.namasha\.com/visit\?id=(\d+)",
                        r'"videoId"\s*:\s*"?(\d+)"?',
                        r'"VideoId"\s*:\s*"?(\d+)"?',
                        r'data-video-id="(\d+)"',
                        r'data-videoid="(\d+)"']:
                m = re.search(pat, r.text)
                if m:
                    return m.group(1)
    except Exception:
        pass
    return None


# ================ CHANNEL SCRAPER ================
async def fetch_channel_hashes(channel_url, cookie_files, max_pages=10):
    cookies = load_cookies_from(cookie_files[0]) if cookie_files else {}
    headers = {
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    found = {}
    async with httpx.AsyncClient(cookies=cookies, headers=headers, timeout=30.0, follow_redirects=True) as client:
        for page in range(1, max_pages + 1):
            params = {} if page == 1 else {"page": page}
            try:
                r = await client.get(channel_url, params=params)
                if r.status_code != 200:
                    print(f"   Page {page}: {r.status_code}")
                    break
                html = r.text
            except Exception as e:
                print(f"   Page {page}: {e}")
                break
            before = len(found)
            for m in re.finditer(r'/v/([A-Za-z0-9_-]{6,})', html):
                h = m.group(1)
                if is_valid_hash(h) and h not in found:
                    found[h] = True
            new = len(found) - before
            print(f"   Page {page}: +{new} (total {len(found)})")
            if new == 0:
                break
            await asyncio.sleep(0.5)
    return list(found.keys())


# ================ FETCH TOKEN ================
async def fetch_token_for_user(cookie_file, video_url):
    cookies = load_cookies_from(cookie_file)
    if not cookies:
        return None
    patterns = [
        r'name="__RequestVerificationToken"\s+type="hidden"\s+value="([^"]+)"',
        r'name="__RequestVerificationToken"\s+value="([^"]+)"',
        r'__RequestVerificationToken["\']\s*[:=]\s*["\']([^"\']+)',
        r'<input[^>]*name="__RequestVerificationToken"[^>]*value="([^"]+)"',
    ]
    headers = {"User-Agent": UA, "Accept": "text/html,*/*"}
    try:
        async with httpx.AsyncClient(cookies=cookies, headers=headers, timeout=30.0, follow_redirects=True) as client:
            r = await client.get(video_url)
            if r.status_code != 200:
                return None
            for pat in patterns:
                m = re.search(pat, r.text)
                if m:
                    return m.group(1)
    except Exception:
        pass
    return None


# ================ AUTO CAPTURE (Improved) ================
async def capture_auto(cfg):
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("Missing playwright.")
        sys.exit(1)

    # اگه capture.json از قبل موجوده و معتبره، ازش استفاده کن
    if CAPTURE_FILE.exists():
        try:
            existing = json.loads(CAPTURE_FILE.read_text(encoding="utf-8"))
            ok, reason = validate_capture(existing)
            if ok:
                print(f"✅ Using existing capture.json: {existing['url'][:80]}")
                return
            else:
                print(f"⚠️  Existing capture.json invalid ({reason}), re-capturing...")
        except Exception:
            print("⚠️  Existing capture.json unreadable, re-capturing...")

    # تعیین URL
    url = None
    if cfg["mode"] == "channel" and cfg["channel_url"]:
        print("Fetching channel...")
        hashes = await fetch_channel_hashes(cfg["channel_url"], find_cookie_files(), max_pages=1)
        if hashes:
            url = f"https://www.namasha.com/v/{hashes[0]}"
    elif cfg["mode"] in ("video", "videos") and cfg["video_urls"]:
        url = cfg["video_urls"][0]
    if not url:
        print("No URL to capture.")
        sys.exit(1)

    print(f"Capturing from: {url}")
    cookie_files = find_cookie_files()
    if not cookie_files:
        print("No cookie files.")
        sys.exit(1)
    chosen = cookie_files[0]
    raw = json.loads(chosen.read_text(encoding="utf-8"))
    if isinstance(raw, dict) and "cookies" in raw:
        raw = raw["cookies"]
    pw_cookies = cookies_to_pw(raw)

    async with async_playwright() as p:
        print("Launching Chromium...")
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
        )
        ctx = await browser.new_context(user_agent=UA)
        await ctx.add_cookies(pw_cookies)
        page = await ctx.new_page()
        captured = []

        async def on_req(req):
            if req.method in ("POST", "PUT"):
                try:
                    body = req.post_data
                except Exception:
                    body = None
                entry = {"url": req.url, "method": req.method,
                         "headers": dict(req.headers), "body": body}
                captured.append(entry)
                # فقط log اگه مربوط به کامنت باشه
                if "submitComment" in req.url or "Comment" in req.url:
                    print(f"  🎯 {req.method} {req.url}")
                else:
                    print(f"  📡 {req.method} {req.url[:80]}")

        page.on("request", on_req)
        print(f"Opening {url} ...")
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            print(f"goto failed: {e}")
            await browser.close()
            sys.exit(2)
        await page.wait_for_timeout(5000)

        # ذخیره HTML برای debug
        try:
            html_content = await page.content()
            (BASE / "page_debug.html").write_text(html_content, encoding="utf-8")
            print(f"   📄 Saved page_debug.html ({len(html_content)} bytes)")
        except Exception as e:
            print(f"   ⚠️  couldn't save HTML: {e}")

        page_token = None
        try:
            page_token = await page.evaluate("""() => {
                const el = document.querySelector('input[name="__RequestVerificationToken"]');
                return el ? el.value : null;
            }""")
        except Exception:
            pass
        if page_token:
            print(f"   🔑 Token: {page_token[:40]}...")
        else:
            print("   ⚠️  No CSRF token found in HTML")

        # جستجوی input کامنت با تمام selectors
        found_selector = None
        for sel in COMMENT_SELECTORS:
            try:
                el = await page.query_selector(sel)
                if el:
                    found_selector = sel
                    print(f"   ✅ Found comment input: {sel}")
                    break
            except Exception:
                continue

        if not found_selector:
            print("   ❌ No comment input found with any selector!")
            print("   💡 Possible reasons:")
            print("      - Not logged in (cookies expired)")
            print("      - Commenting disabled on this video")
            print("      - Page structure changed")
            print("   💡 Saved page_debug.html for inspection")

        if found_selector:
            try:
                await page.fill(found_selector, "test_capture_please_ignore", timeout=5000)
                print("   ✍️  Filled comment input")
                await page.wait_for_timeout(800)
                # تلاش برای submit با Enter
                try:
                    await page.press(found_selector, "Enter")
                except Exception:
                    pass
                # تلاش برای پیدا کردن دکمه submit
                for btn_sel in ["button[type='submit']", "input[type='submit']",
                                ".comment-submit", ".submit-comment", "button:has-text('ارسال')",
                                "button:has-text('ثبت')"]:
                    try:
                        btn = await page.query_selector(btn_sel)
                        if btn:
                            await btn.click()
                            print(f"   🖱️  Clicked submit: {btn_sel}")
                            break
                    except Exception:
                        continue
                await page.wait_for_timeout(6000)
            except Exception as e:
                print(f"   ⚠️  auto-submit failed: {e}")
                await page.wait_for_timeout(8000)
        else:
            # منتظر بمون شاید با کلیک روی textarea ظاهر بشه
            print("   ⏳ Waiting 10s for user interaction simulation...")
            await page.wait_for_timeout(10000)

        # اسکرین‌شات برای debug
        try:
            await page.screenshot(path=str(BASE / "debug_screenshot.png"), full_page=False)
            print("   📸 Saved debug_screenshot.png")
        except Exception:
            pass

        await browser.close()

    # پیدا کردن submitComment در captured
    best = None
    for r in captured:
        if "submitComment" in r["url"] or "SubmitComment" in r["url"]:
            best = r
            print(f"   🎯 Found submitComment: {r['url']}")
            break

    if not best:
        # دنبال هر چیز که به کامنت مربوط باشه
        for r in captured:
            if "/Comment" in r["url"] or "comment" in r["url"].lower():
                if "namasha.com" in r["url"]:
                    best = r
                    print(f"   🎯 Found comment-related POST: {r['url']}")
                    break

    if best and page_token and best.get("body"):
        best["body"] = re.sub(
            r"__RequestVerificationToken=[^&]*",
            f"__RequestVerificationToken={page_token}",
            best["body"],
        )

    if not best:
        # تلاش برای ساخت capture.json به صورت دستی از روی video ID
        print("\n   ⚠️  No submitComment captured!")
        print("   🔧 Attempting to synthesize capture.json from video ID...")

        vid = await resolve_video_id(url, cookie_files)
        if not vid:
            print("   ❌ Could not resolve video ID either. Giving up.")
            sys.exit(1)

        token_value = page_token or _load_token_from_backup()
        if not token_value:
            print("   ❌ No CSRF token available. Cannot synthesize.")
            print("   💡 Please create capture.json manually. See instructions.")
            sys.exit(1)

        print(f"   ✅ Video ID: {vid}")
        print(f"   ✅ CSRF token: {token_value[:30]}...")
        body = f"__RequestVerificationToken={token_value}&Text=test_capture_please_ignore&ParentId="
        best = {
            "url": f"https://www.namasha.com/submitComment?id={vid}",
            "method": "POST",
            "headers": {
                "referer": url,
                "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
                "x-requested-with": "XMLHttpRequest",
                "user-agent": UA,
                "accept": "*/*",
                "origin": "https://www.namasha.com",
            },
            "body": body,
            "_synthesized": True,
        }
        print(f"   ✅ Synthesized: {best['url']}")

    # ذخیره backup و اصلی
    try:
        CAPTURE_BACKUP.write_text(json.dumps(best, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    CAPTURE_FILE.write_text(json.dumps(best, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n✅ Saved capture.json")
    print(f"   URL: {best['url'][:100]}")
    print(f"   Body: {(best.get('body') or '')[:100]}")


# ================ SEND ================
async def run_ultimate(cookie_file, total, cap, concurrency):
    cookies = load_cookies_from(cookie_file)
    if not cookies:
        print(f"[{cookie_file.name}] no cookies")
        return 0, 0

    # اعتبارسنجی capture
    ok, reason = validate_capture(cap)
    if not ok:
        print(f"❌ Invalid capture.json: {reason}")
        return 0, 0

    endpoint = cap["url"]
    parsed = urllib.parse.urlparse(endpoint)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    video_url = (cap.get("headers") or {}).get("referer") or "https://www.namasha.com/"

    print(f"[{cookie_file.name}] fetching token...")
    fresh_token = await fetch_token_for_user(cookie_file, video_url)
    body_tmpl = cap.get("body") or ""
    if fresh_token:
        body_tmpl = re.sub(r"__RequestVerificationToken=[^&]*",
                          f"__RequestVerificationToken={fresh_token}", body_tmpl)
        print(f"   ✅ Token: {fresh_token[:30]}...")
    else:
        print("   ⚠️  using captured token")

    headers = {
        "User-Agent": UA, "Accept": "application/json, text/plain, */*",
        "Accept-Language": "fa-IR,fa;q=0.9,en;q=0.8",
        "Origin": origin, "Referer": video_url,
        "X-Requested-With": "XMLHttpRequest",
    }
    csrf = cookies.get(".csrf") or cookies.get("csrf") or cookies.get("XSRF-TOKEN")
    if csrf:
        headers["X-CSRF-Token"] = csrf
        headers["X-CSRFToken"] = csrf

    comment = get_comment_text()
    body_str, ctype = build_body(body_tmpl, comment)
    body_bytes = body_str.encode("utf-8")
    post_headers = {"Content-Type": ctype}

    print(f"[{cookie_file.name}] {total} comments @ conc={concurrency}")
    success = 0
    fail = 0
    done = 0
    lock = asyncio.Lock()
    sem = asyncio.Semaphore(concurrency)
    start = time.monotonic()
    first_status = {"code": None}
    first_body = {"text": None}

    timeout = httpx.Timeout(20.0, connect=5.0)
    limits = httpx.Limits(max_connections=concurrency + 50, max_keepalive_connections=concurrency)

    async with httpx.AsyncClient(cookies=cookies, headers=headers, timeout=timeout,
                                  limits=limits, http2=False, follow_redirects=False) as client:
        async def do_one(_):
            nonlocal success, fail, done
            async with sem:
                try:
                    r = await client.post(endpoint, content=body_bytes, headers=post_headers)
                    ok = r.status_code in (200, 201, 202, 204)
                    if first_status["code"] is None:
                        first_status["code"] = r.status_code
                        first_body["text"] = r.text[:300]
                except Exception as e:
                    ok = False
                    if first_status["code"] is None:
                        first_status["code"] = f"EXC:{str(e)[:60]}"
                async with lock:
                    if ok:
                        success += 1
                    else:
                        fail += 1
                    done += 1
                    if done % 100 == 0 or done == total:
                        el = time.monotonic() - start
                        rate = done / el if el > 0 else 0
                        print(f"  [{cookie_file.name}] {done}/{total} ✅{success} ❌{fail} {rate:.0f}/s")

        await asyncio.gather(*(do_one(i) for i in range(total)))

    el = time.monotonic() - start
    print(f"[{cookie_file.name}] ✅{success} ❌{fail} in {el:.1f}s first={first_status['code']}")
    if first_body["text"] and success == 0:
        print(f"   📋 First response body: {first_body['text'][:200]}")
    return success, fail


# ================ AUTO ================
async def auto():
    cfg = load_config()
    print("=" * 60)
    print("Namasha Bot Auto")
    print("=" * 60)
    print(json.dumps(cfg, indent=2, ensure_ascii=False))
    print("=" * 60)

    cookie_files = find_cookie_files()
    if not cookie_files:
        print("No cookie files.")
        sys.exit(1)
    print(f"Cookies: {[f.name for f in cookie_files]}")

    if not await probe_namasha():
        print("\nIP blocked. Exiting to save resources.")
        sys.exit(3)

    if not CAPTURE_FILE.exists():
        print("\nNo capture.json - running capture...")
        await capture_auto(cfg)
    else:
        # اعتبارسنجی capture.json موجود
        try:
            existing = json.loads(CAPTURE_FILE.read_text(encoding="utf-8"))
            ok, reason = validate_capture(existing)
            if not ok:
                print(f"\n⚠️  capture.json invalid ({reason}), re-capturing...")
                await capture_auto(cfg)
            else:
                print(f"\n✅ capture.json valid: {existing['url'][:80]}")
        except Exception as e:
            print(f"\n⚠️  capture.json unreadable ({e}), re-capturing...")
            await capture_auto(cfg)

    cap = json.loads(CAPTURE_FILE.read_text(encoding="utf-8"))
    ok, reason = validate_capture(cap)
    if not ok:
        print(f"\n❌ capture.json still invalid: {reason}")
        print("   See page_debug.html artifact to inspect the page.")
        sys.exit(1)

    video_urls = []
    if cfg["mode"] == "channel":
        if not cfg["channel_url"]:
            print("channel_url missing.")
            sys.exit(1)
        print(f"\nFetching channel: {cfg['channel_url']}")
        hashes = await fetch_channel_hashes(cfg["channel_url"], cookie_files)
        if not hashes:
            print("No videos found.")
            sys.exit(1)
        n = min(cfg["max_videos"], len(hashes))
        video_urls = [f"https://www.namasha.com/v/{h}" for h in hashes[:n]]
    elif cfg["mode"] == "video":
        video_urls = cfg["video_urls"][:1]
    elif cfg["mode"] == "videos":
        video_urls = cfg["video_urls"][:cfg["max_videos"]]

    if not video_urls:
        print("No video URLs.")
        sys.exit(1)

    print(f"\nResolving {len(video_urls)} video IDs...")
    resolved = []
    for u in video_urls:
        vid = await resolve_video_id(u, cookie_files)
        if vid:
            resolved.append((u, vid))
            print(f"   ✅ {u} -> {vid}")
        else:
            print(f"   ❌ {u} skipped")

    if not resolved:
        print("Nothing resolved.")
        sys.exit(1)

    jobs = []
    for cf in cookie_files:
        n = cfg["comments_per_cookie_per_video"].get(cf.name, 0)
        if n > 0:
            jobs.append((cf, n))

    if not jobs:
        print("No jobs.")
        sys.exit(1)

    print(f"\nPLAN: {len(resolved)} videos x {len(jobs)} users")
    total_s = 0
    total_f = 0

    async def process_video(video_url, video_id):
        nonlocal total_s, total_f
        cap_v = dict(cap)
        cap_v["url"] = f"https://www.namasha.com/submitComment?id={video_id}"
        cap_v["headers"] = dict(cap.get("headers") or {})
        cap_v["headers"]["referer"] = video_url
        print(f"\n{'=' * 60}\n  🎬 {video_url} (id={video_id})\n{'=' * 60}")
        tasks = [run_ultimate(cf, n, cap_v, cfg["concurrency"]) for cf, n in jobs]
        results = await asyncio.gather(*tasks)
        s = sum(r[0] for r in results)
        f = sum(r[1] for r in results)
        total_s += s
        total_f += f

    await asyncio.gather(*(process_video(u, v) for u, v in resolved))
    print(f"\n{'=' * 60}")
    print(f"DONE | ✅ {total_s} | ❌ {total_f}")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    args = sys.argv[1:] if len(sys.argv) > 1 else ["auto"]
    cmd = args[0]
    if cmd == "auto":
        asyncio.run(auto())
    elif cmd == "capture":
        asyncio.run(capture_auto(load_config()))
    elif cmd == "probe":
        asyncio.run(probe_namasha())
    else:
        print(f"Unknown: {cmd}")
        sys.exit(1)
