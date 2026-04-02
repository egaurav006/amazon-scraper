#!/usr/bin/env python3
"""
Flipkart Hybrid Scraper — GitHub Actions
Requests first → Playwright fallback (same as Amazon approach)
Updated for Flipkart's 2025+ page structure
"""

import csv
import random
import re
import sys
import time

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ══════════════════ CONFIG ══════════════════

INPUT_FILE  = "pids.csv"
OUTPUT_FILE = "flipkart_data.csv"
BASE_URL    = "https://www.flipkart.com/product/p/itm?pid="
FIELDS      = ["PID", "Product URL", "Title", "Selling Price",
               "Seller", "Seller Rating"]

BASE_DELAY  = 1.2
JITTER      = 0.5
BATCH_SIZE  = 35
COOLDOWN    = 10
PW_RECYCLE  = 50

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
]

EXTRACT_JS = r"""
() => {
    function txt(el) {
        return el ? (el.innerText || el.textContent || '').trim() : '';
    }

    // Title
    var title = '';
    var titleSels = ['span.VU-ZEz', 'span.B_NuCI', 'h1._9E25nV', 'h1.yhB1nd', 'h1'];
    for (var i = 0; i < titleSels.length; i++) {
        var el = document.querySelector(titleSels[i]);
        if (el && txt(el)) { title = txt(el); break; }
    }
    if (!title) {
        var meta = document.querySelector('meta[property="og:title"]');
        if (meta) title = meta.getAttribute('content') || '';
    }
    title = title || 'N/A';

    // Price
    var price = 'N/A';
    var newPriceSels = [
        'div.v1zwn21k.v1zwn20',
        'div.v1zwn21k.v1zwn2d',
        'div.v1zwn21k.v1zwn24'
    ];
    for (var i = 0; i < newPriceSels.length; i++) {
        var els = document.querySelectorAll(newPriceSels[i]);
        for (var j = 0; j < els.length; j++) {
            var t = txt(els[j]);
            if (t.indexOf('₹') >= 0 && t.length < 15) {
                var m = t.replace(/,/g, '').match(/₹\s*(\d+)/);
                if (m) { price = '₹' + m[1]; break; }
            }
        }
        if (price !== 'N/A') break;
    }
    if (price === 'N/A') {
        var oldSels = ['div.Nx9bqj.CxhGGd', 'div.Nx9bqj', 'div.CEmiEU',
                       'div._30jeq3._16Jk6d', 'div._30jeq3', 'div.hl05eU'];
        for (var i = 0; i < oldSels.length; i++) {
            var els = document.querySelectorAll(oldSels[i]);
            for (var j = 0; j < els.length; j++) {
                var t = txt(els[j]);
                if (t.indexOf('₹') >= 0) {
                    var m = t.replace(/,/g, '').match(/₹\s*(\d+)/);
                    if (m) { price = '₹' + m[1]; break; }
                }
            }
            if (price !== 'N/A') break;
        }
    }
    if (price === 'N/A') {
        var all = document.querySelectorAll('[class*="v1zwn2"]');
        for (var i = 0; i < all.length; i++) {
            var t = txt(all[i]);
            if (t.indexOf('₹') >= 0 && t.length < 15 && t.indexOf('Delivery') < 0) {
                var m = t.replace(/,/g, '').match(/₹\s*(\d+)/);
                if (m) { price = '₹' + m[1]; break; }
            }
        }
    }
    if (price === 'N/A') {
        var allDivs = document.querySelectorAll('div');
        for (var i = 0; i < allDivs.length; i++) {
            var t = txt(allDivs[i]);
            if (t.length >= 2 && t.length <= 10 && t.indexOf('₹') === 0) {
                var m = t.replace(/,/g, '').match(/^₹\s*(\d+)$/);
                if (m && parseInt(m[1]) > 0) { price = '₹' + m[1]; break; }
            }
        }
    }

    // Seller + Rating
    var seller = 'N/A';
    var rating = 'N/A';
    var allEls = document.querySelectorAll('div, span');
    for (var i = 0; i < allEls.length; i++) {
        var t = txt(allEls[i]);
        if (t.length < 200) {
            var fm = t.match(/Fulfilled\s+by\s+([A-Za-z0-9][A-Za-z0-9 &._\-]+)/i);
            if (fm) {
                seller = fm[1].trim();
                var rm = t.match(/Fulfilled\s+by\s+.+?(\d+\.?\d*)\s*[•·]/);
                if (rm && parseFloat(rm[1]) > 0 && parseFloat(rm[1]) <= 5) rating = rm[1];
                break;
            }
        }
    }
    if (seller === 'N/A') {
        for (var i = 0; i < allEls.length; i++) {
            var t = txt(allEls[i]);
            if (t.length < 200) {
                var sm = t.match(/Sold\s+by[:\s]+([A-Za-z0-9][A-Za-z0-9 &._\-]+)/i);
                if (sm) { seller = sm[1].trim(); break; }
            }
        }
    }
    if (seller === 'N/A') {
        var sd = document.getElementById('sellerName');
        if (sd) {
            var span = sd.querySelector('span');
            if (span) { var s = txt(span).replace(/[\d.]+$/, '').trim(); if (s) seller = s; }
            if (rating === 'N/A') {
                var m = txt(sd).match(/(\d+\.?\d*)\s*$/);
                if (m && parseFloat(m[1]) <= 5) rating = m[1];
            }
        }
    }

    return { title: title, price: price, seller: seller, rating: rating };
}
"""


# ══════════════════ HELPERS ══════════════════

def read_pids():
    pids = []
    try:
        with open(INPUT_FILE, "r", encoding="utf-8-sig") as f:
            for line in f:
                pid = line.strip()
                if pid and not pid.upper().startswith("PID"):
                    pids.append(pid)
        print(f"📋 Loaded {len(pids)} PIDs from {INPUT_FILE}")
        return pids
    except FileNotFoundError:
        print(f"❌ {INPUT_FILE} not found!")
        sys.exit(1)


def empty_result(pid):
    return {
        "PID": pid,
        "Product URL": f"{BASE_URL}{pid}",
        "Title": "FAILED TO FETCH",
        "Selling Price": "N/A",
        "Seller": "N/A",
        "Seller Rating": "N/A",
    }


# ══════════════════ REQUESTS SCRAPER ══════════════════

def create_session():
    s = requests.Session()
    ua = random.choice(USER_AGENTS)
    s.headers.update({
        "User-Agent": ua,
        "Accept": "text/html,application/xhtml+xml,"
                  "application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Cache-Control": "max-age=0",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
    })
    retry = Retry(
        total=2, backoff_factor=1.0,
        status_forcelist=[500, 502, 503, 504],
        allowed_methods=["GET"], raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s


def warmup_session(session):
    urls = [
        "https://www.flipkart.com/",
        "https://www.flipkart.com/grocery/pr?sid=eat",
    ]
    for url in urls:
        try:
            session.headers["User-Agent"] = random.choice(USER_AGENTS)
            r = session.get(url, timeout=10)
            print(f"  🔥 Warmup {url[:40]}... HTTP {r.status_code}")
            time.sleep(1)
        except Exception as e:
            print(f"  ⚠ Warmup failed: {e}")
    time.sleep(2)


def extract_price_bs(soup, html):
    import json as js

    # New classes
    for cls in ["v1zwn21k v1zwn20", "v1zwn21k v1zwn2d", "v1zwn21k v1zwn24"]:
        for el in soup.find_all("div", class_=cls):
            t = el.get_text(strip=True)
            if "₹" in t and len(t) < 15:
                m = re.search(r"₹\s*([\d,]+)", t)
                if m:
                    return "₹" + m.group(1).replace(",", "")

    # Old classes
    for cls in ["Nx9bqj CxhGGd", "Nx9bqj", "CEmiEU",
                "_30jeq3 _16Jk6d", "_30jeq3", "hl05eU"]:
        for el in soup.find_all("div", class_=cls):
            t = el.get_text(strip=True)
            if "₹" in t:
                m = re.search(r"₹\s*([\d,]+)", t)
                if m:
                    return "₹" + m.group(1).replace(",", "")

    # JSON-LD
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = js.loads(script.string)
            if isinstance(data, dict) and "offers" in data:
                offers = data["offers"]
                if isinstance(offers, dict) and "price" in offers:
                    return "₹" + str(int(float(offers["price"])))
                elif isinstance(offers, list) and offers and "price" in offers[0]:
                    return "₹" + str(int(float(offers[0]["price"])))
        except Exception:
            pass

    # Regex in HTML
    m = re.search(r'"price"\s*:\s*["\']?(\d+(?:\.\d+)?)', html)
    if m:
        return "₹" + str(int(float(m.group(1))))

    return "N/A"


def extract_seller_bs(soup):
    # "Fulfilled by" pattern in page text
    text = soup.get_text(" ", strip=True)
    fm = re.search(r"Fulfilled\s+by\s+([A-Za-z0-9][A-Za-z0-9 &._-]+)", text)
    if fm:
        seller = fm[1].strip()
        # Try to get rating too
        rm = re.search(
            r"Fulfilled\s+by\s+" + re.escape(seller) + r"\s*(\d+\.?\d*)",
            text
        )
        rating = "N/A"
        if rm and 0 < float(rm.group(1)) <= 5:
            rating = rm.group(1)
        return seller, rating

    # "Sold by"
    sm = re.search(r"Sold\s+by[:\s]+([A-Za-z0-9][A-Za-z0-9 &._-]+)", text)
    if sm:
        return sm.group(1).strip(), "N/A"

    # Old sellerName div
    sd = soup.find("div", id="sellerName")
    if sd:
        span = sd.find("span")
        if span:
            s = re.sub(r"[\d.]+$", "", span.get_text(strip=True)).strip()
            if s:
                rating = "N/A"
                m = re.search(r"(\d+\.?\d*)\s*$", sd.get_text(strip=True))
                if m and 0 < float(m.group(1)) <= 5:
                    rating = m.group(1)
                return s, rating

    return "N/A", "N/A"


def scrape_requests(pid, session):
    url = f"{BASE_URL}{pid}"
    try:
        session.headers["User-Agent"] = random.choice(USER_AGENTS)
        r = session.get(url, timeout=15)

        if r.status_code == 529 or r.status_code == 403:
            return None
        if r.status_code != 200:
            return None

        html = r.text
        if "Sorry, no results found" in html or "Page Not Found" in html:
            return {
                "PID": pid, "Product URL": url,
                "Title": "PRODUCT NOT FOUND",
                "Selling Price": "N/A",
                "Seller": "N/A", "Seller Rating": "N/A",
            }

        soup = BeautifulSoup(html, "html.parser")

        # Title
        title = "N/A"
        for tag, attrs in [
            ("span", {"class": "VU-ZEz"}), ("span", {"class": "B_NuCI"}),
            ("h1", {"class": "_9E25nV"}), ("h1", {"class": "yhB1nd"}),
        ]:
            el = soup.find(tag, attrs)
            if el:
                title = el.get_text(strip=True)
                break
        if title == "N/A":
            h1 = soup.find("h1")
            if h1:
                title = h1.get_text(strip=True)
        if title == "N/A":
            meta = soup.find("meta", {"property": "og:title"})
            if meta and meta.get("content"):
                title = meta["content"]

        if title == "N/A":
            return None  # page didn't load properly

        price = extract_price_bs(soup, html)
        seller, rating = extract_seller_bs(soup)

        return {
            "PID": pid, "Product URL": str(r.url),
            "Title": title, "Selling Price": price,
            "Seller": seller, "Seller Rating": rating,
        }

    except Exception as e:
        print(f"\n  ⚠ REQ error {pid}: {e}")
        return None


# ══════════════════ PLAYWRIGHT SCRAPER ══════════════════

_pw = _browser = _context = _page = None


def pw_start():
    global _pw, _browser
    from playwright.sync_api import sync_playwright
    _pw = sync_playwright().start()
    _browser = _pw.chromium.launch(
        headless=True,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--disable-features=IsolateOrigins,site-per-process",
        ]
    )
    pw_new_context()


def pw_new_context():
    global _context, _page
    _context = _browser.new_context(
        user_agent=random.choice(USER_AGENTS),
        viewport={"width": 1920, "height": 1080},
        locale="en-IN",
        timezone_id="Asia/Kolkata",
    )
    _page = _context.new_page()
    _page.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3]});
        Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
        window.chrome = { runtime: {} };
    """)
    _page.route(
        re.compile(r"\.(png|jpg|jpeg|gif|svg|ico|webp|woff2?|ttf)$"),
        lambda route: route.abort(),
    )


def pw_recycle():
    global _context, _page
    try:
        _page.close()
    except Exception:
        pass
    try:
        _context.close()
    except Exception:
        pass
    pw_new_context()


def pw_stop():
    for obj in (_page, _context, _browser, _pw):
        try:
            if hasattr(obj, "close"):
                obj.close()
            elif hasattr(obj, "stop"):
                obj.stop()
        except Exception:
            pass


def scrape_playwright(pid):
    url = f"{BASE_URL}{pid}"
    for attempt in range(2):
        try:
            _page.goto(url, wait_until="domcontentloaded", timeout=25000)
            _page.wait_for_selector(
                "span.VU-ZEz, span.B_NuCI, h1, "
                "div[class*='v1zwn'], div.OmE16y, div.Nx9bqj",
                timeout=12000,
            )

            content = _page.content()[:4000]
            if "Sorry, no results found" in content or "Page Not Found" in content:
                return {
                    "PID": pid, "Product URL": url,
                    "Title": "PRODUCT NOT FOUND",
                    "Selling Price": "N/A",
                    "Seller": "N/A", "Seller Rating": "N/A",
                }

            data = _page.evaluate(EXTRACT_JS)
            if not data or data.get("title") in (None, "", "N/A"):
                if attempt == 0:
                    time.sleep(2)
                continue

            return {
                "PID": pid, "Product URL": _page.url,
                "Title": data.get("title", "N/A"),
                "Selling Price": data.get("price", "N/A"),
                "Seller": data.get("seller", "N/A"),
                "Seller Rating": data.get("rating", "N/A"),
            }
        except Exception as e:
            print(f"\n  ⚠ PW error {pid} (attempt {attempt+1}): {e}")
            if attempt == 0:
                time.sleep(3)
    return None


# ══════════════════ MAIN ══════════════════

def main():
    pids = read_pids()
    if not pids:
        print("❌ No PIDs found!")
        return

    total = len(pids)

    # ── Try requests first ──
    session = create_session()
    print("\n🔥 Warming up session...")
    warmup_session(session)

    # Quick test: can requests work?
    print("  🧪 Testing requests method...")
    test = scrape_requests(pids[0], session)
    requests_works = test is not None
    if requests_works:
        print(f"  ✅ Requests works! (title: {test.get('Title','?')[:30]})")
    else:
        print("  ❌ Requests blocked. Will use Playwright for all.")

    # ── Start Playwright if needed ──
    pw_started = False
    if not requests_works:
        print("\n  🌐 Starting Playwright browser...")
        pw_start()
        pw_started = True
        try:
            _page.goto("https://www.flipkart.com/", timeout=20000)
            time.sleep(3)
        except Exception:
            pass
        print("  ✅ Browser ready!")

    print(f"\n🚀 Scraping {total} Flipkart products...\n")

    results = []
    pw_uses = 0
    stats = {"REQ": 0, "PW": 0, "FAIL": 0}
    start = time.time()

    # If requests test already got first PID, use that result
    start_index = 0
    if requests_works and test:
        results.append(test)
        stats["REQ"] += 1
        price_s = test.get("Selling Price", "N/A")[:12]
        print(f"  [1/{total}] REQ  | {price_s:<12} | {test.get('Title','?')[:30]}...")
        start_index = 1
        if total > 1:
            time.sleep(BASE_DELAY + random.random() * JITTER)

    try:
        for idx in range(start_index, total):
            pid = pids[idx]
            i = idx + 1
            result = None
            method = "FAIL"

            # Try requests if it worked before
            if requests_works:
                result = scrape_requests(pid, session)
                if result:
                    method = "REQ"

            # Playwright fallback
            if not result:
                if not pw_started:
                    print("\n  🌐 Starting Playwright browser...")
                    pw_start()
                    pw_started = True
                    try:
                        _page.goto("https://www.flipkart.com/", timeout=20000)
                        time.sleep(3)
                    except Exception:
                        pass

                result = scrape_playwright(pid)
                method = "PW" if result else "FAIL"
                pw_uses += 1

                if pw_uses >= PW_RECYCLE:
                    print("\n  🔄 Recycling browser...")
                    pw_recycle()
                    pw_uses = 0

            if not result:
                result = empty_result(pid)
                method = "FAIL"

            results.append(result)
            stats[method] += 1

            pct = int(100 * i / total)
            price_s = result.get("Selling Price", "N/A")[:12]
            seller_s = result.get("Seller", "N/A")[:12]
            elapsed = time.time() - start
            rate = i / elapsed if elapsed > 0 else 0
            eta = (total - i) / rate if rate > 0 else 0

            print(
                f"\r  [{pct:3d}%] {i}/{total} | {method:4s} | "
                f"{price_s:<12} | {seller_s:<12} | ETA {eta:.0f}s  ",
                end="", flush=True,
            )

            if i < total:
                time.sleep(BASE_DELAY + random.random() * JITTER)

            if i % BATCH_SIZE == 0 and i < total:
                print(f"\n  ⏳ Cooldown {COOLDOWN}s (batch {i // BATCH_SIZE})...")
                time.sleep(COOLDOWN)

    except KeyboardInterrupt:
        print("\n\n🛑 Interrupted!")
    except Exception as e:
        print(f"\n\n❌ Error: {e}")
    finally:
        if pw_started:
            pw_stop()

    # Save
    print(f"\n\n💾 Writing {len(results)} results to {OUTPUT_FILE}...")
    with open(OUTPUT_FILE, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(results)

    elapsed_min = (time.time() - start) / 60
    done = sum(stats.values())
    success = sum(1 for r in results if r["Selling Price"] != "N/A")

    print(f"\n{'═'*55}")
    print(f"  ✅ Done: {done} products in {elapsed_min:.1f} min")
    print(f"  ⚡ Requests:   {stats['REQ']}")
    print(f"  🌐 Playwright: {stats['PW']}")
    print(f"  ❌ Failed:     {stats['FAIL']}")
    print(f"  💰 Price found: {success}/{done}")
    print(f"  📁 Output:      {OUTPUT_FILE}")
    print(f"{'═'*55}")

    # Write summary for email
    with open("run_summary.txt", "w") as f:
        f.write(f"{done}\n")
        f.write(f"{elapsed_min:.1f}\n")
        f.write(f"{success}\n")
        f.write(f"0\n")
        f.write(f"{done - success}\n")


if __name__ == "__main__":
    main()
