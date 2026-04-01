#!/usr/bin/env python3
"""
Amazon Hybrid Scraper — GitHub Actions Edition
Primary: requests + BeautifulSoup (fast)
Fallback: Playwright headless Chromium (when blocked)
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

INPUT_FILE   = "asins.csv"
OUTPUT_FILE  = "amazon_data.csv"
FIELDS       = ["ASIN", "Product URL", "Title", "Price",
                "Seller", "Buy Box Active", "Quantity"]

BASE_DELAY   = 1.2
JITTER       = 0.5
BATCH_SIZE   = 35
COOLDOWN     = 10
PW_RECYCLE   = 50

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
    function isStrike(el) {
        if (!el) return false;
        let p = el.closest('.a-price');
        if (!p) return false;
        return (p.outerHTML || '').toLowerCase().includes('a-text-price');
    }
    function pickPrice() {
        const sels = [
            '#corePriceDisplay_desktop_feature_div .a-price .a-offscreen',
            '#apex_desktop .a-price .a-offscreen',
            '#corePrice_feature_div .a-price .a-offscreen',
            '#desktop_buybox .a-price .a-offscreen',
            '#newBuyBoxPrice', '#price_inside_buybox'
        ];
        for (const sel of sels) {
            for (const node of document.querySelectorAll(sel)) {
                if (isStrike(node)) continue;
                let t = txt(node).replace(/,/g, '');
                let m = t.match(/(\d+(\.\d+)?)/);
                if (m) {
                    let v = parseFloat(m[1]).toFixed(2).replace(/\.?0+$/, '');
                    return '\u20B9 ' + v;
                }
            }
        }
        return 'Not Found';
    }

    let title = txt(document.getElementById('productTitle')) || 'Not Found';
    let price = pickPrice();

    let seller = txt(document.getElementById('sellerProfileTriggerId'));
    if (!seller) {
        let mi = document.getElementById('merchant-info');
        if (mi) {
            let t = txt(mi).replace('Sold by', '').split('and')[0].trim();
            if (t) seller = t;
        }
    }
    seller = seller || 'Not Found';

    let buybox = (document.getElementById('add-to-cart-button') ||
                  document.getElementById('buy-now-button')) ? 'Yes' : 'No';

    let qty = '1';
    let qp = document.querySelector('#selectQuantity .a-dropdown-prompt');
    if (qp && /^\d+$/.test(txt(qp))) qty = txt(qp);

    return { title, price, seller, buybox, qty };
}
"""


# ══════════════════ HELPERS ══════════════════

def extract_price(text):
    if not text:
        return "Not Found"
    t = text.replace(",", "")
    m = re.search(r"(\d+(\.\d+)?)", t)
    if not m:
        return "Not Found"
    val = float(m.group(1))
    s = f"₹ {val:.2f}".rstrip("0").rstrip(".")
    return s


def looks_blocked(html):
    if not html:
        return False
    h = html[:5000].lower()
    return "captcha" in h or "robot check" in h or "automated access" in h


def load_asins():
    asins, seen = [], set()
    pattern = re.compile(r"^[A-Z0-9]{10}$")
    try:
        with open(INPUT_FILE, "r", encoding="utf-8-sig") as f:
            for row in csv.reader(f):
                if not row:
                    continue
                a = row[0].strip().upper()
                if pattern.match(a) and a not in seen:
                    seen.add(a)
                    asins.append(a)
    except FileNotFoundError:
        print(f"❌ {INPUT_FILE} not found!")
        sys.exit(1)
    return asins


def empty_result(asin):
    return {
        "ASIN": asin,
        "Product URL": f"https://www.amazon.in/dp/{asin}?psc=1",
        "Title": "Not Found", "Price": "Not Found",
        "Seller": "Not Found", "Buy Box Active": "No",
        "Quantity": "1",
    }


# ══════════════════ REQUESTS SCRAPER ══════════════════

def create_session():
    s = requests.Session()
    ua = random.choice(USER_AGENTS)
    s.headers.update({
        "User-Agent": ua,
        "Accept": "text/html,application/xhtml+xml,"
                  "application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.7",
        "Accept-Encoding": "gzip, deflate, br",
        "Upgrade-Insecure-Requests": "1",
        "Connection": "keep-alive",
        "Cache-Control": "max-age=0",
    })
    retry = Retry(
        total=3, backoff_factor=1.0,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"], raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s


def warmup_session(session):
    """Visit Amazon homepage to get cookies before scraping."""
    warmup_urls = [
        "https://www.amazon.in/",
        "https://www.amazon.in/gp/bestsellers/",
    ]
    for url in warmup_urls:
        try:
            session.headers["User-Agent"] = random.choice(USER_AGENTS)
            r = session.get(url, timeout=10)
            print(f"  🔥 Warmup {url[:40]}... HTTP {r.status_code}")
            time.sleep(1)
        except Exception as e:
            print(f"  ⚠ Warmup failed: {e}")
    # small pause after warmup
    time.sleep(2)


def _is_strike(el):
    parent = el
    for _ in range(4):
        if not parent:
            break
        cls = parent.get("class") or []
        if "a-text-price" in cls:
            return True
        parent = getattr(parent, "parent", None)
    return False


def scrape_requests(asin, session):
    url = f"https://www.amazon.in/dp/{asin}?psc=1"
    try:
        session.headers["User-Agent"] = random.choice(USER_AGENTS)
        r = session.get(url, timeout=15)
        if r.status_code != 200 or looks_blocked(r.text):
            return None

        soup = BeautifulSoup(r.text, "html.parser")

        tag = soup.find(id="productTitle")
        title = tag.get_text(strip=True) if tag else "Not Found"
        if title == "Not Found":
            return None

        price = "Not Found"
        for el in soup.select(
            "#corePriceDisplay_desktop_feature_div .a-price .a-offscreen,"
            "#apex_desktop .a-price .a-offscreen,"
            "#corePrice_feature_div .a-price .a-offscreen,"
            "#newBuyBoxPrice, #price_inside_buybox,"
            "#desktop_buybox .a-price .a-offscreen"
        ):
            if getattr(el, "name", "") == "span" and _is_strike(el):
                continue
            p = extract_price(el.get_text(" ", strip=True))
            if p != "Not Found":
                price = p
                break

        seller = "Not Found"
        stag = soup.select_one("#sellerProfileTriggerId")
        if stag and stag.get_text(strip=True):
            seller = stag.get_text(strip=True)
        else:
            mi = soup.select_one("#merchant-info")
            if mi:
                t = (mi.get_text(" ", strip=True)
                     .replace("Sold by", "").split("and")[0].strip())
                if t:
                    seller = t

        buybox = ("Yes" if soup.select_one(
            "#add-to-cart-button, #buy-now-button") else "No")

        qty = "1"
        q = soup.select_one("#selectQuantity .a-dropdown-prompt")
        if q and q.get_text(strip=True).isdigit():
            qty = q.get_text(strip=True)
        else:
            opt = soup.select_one("select#quantity option[selected]")
            if opt and opt.get("value", "").strip().isdigit():
                qty = opt["value"].strip()

        return {"ASIN": asin, "Product URL": url, "Title": title,
                "Price": price, "Seller": seller,
                "Buy Box Active": buybox, "Quantity": qty}

    except Exception as e:
        print(f"\n  ⚠ REQ error {asin}: {e}")
        return None


# ══════════════════ PLAYWRIGHT SCRAPER ══════════════════

_pw = _browser = _context = _page = None


def pw_start():
    global _pw, _browser
    from playwright.sync_api import sync_playwright
    _pw = sync_playwright().start()
    _browser = _pw.chromium.launch(headless=True)
    pw_new_context()


def pw_new_context():
    global _context, _page
    _context = _browser.new_context(
        user_agent=random.choice(USER_AGENTS),
        viewport={"width": 1920, "height": 1080},
        locale="en-US",
    )
    _page = _context.new_page()
    _page.add_init_script(
        "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
    )
    _page.route(
        re.compile(r"\.(png|jpg|jpeg|gif|svg|ico|webp|css|woff2?|ttf)$"),
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


def scrape_playwright(asin):
    url = f"https://www.amazon.in/dp/{asin}?psc=1"
    for attempt in range(2):
        try:
            _page.goto(url, wait_until="domcontentloaded", timeout=20000)
            _page.wait_for_selector(
                "#productTitle, .a-price .a-offscreen", timeout=10000
            )
            if looks_blocked(_page.content()[:3000]):
                time.sleep(4)
                continue

            data = _page.evaluate(EXTRACT_JS)
            if not data or data.get("title") in (None, "", "Not Found"):
                if attempt == 0:
                    time.sleep(2)
                continue

            return {
                "ASIN": asin, "Product URL": url,
                "Title": data["title"],
                "Price": data.get("price", "Not Found"),
                "Seller": data.get("seller", "Not Found"),
                "Buy Box Active": data.get("buybox", "No"),
                "Quantity": data.get("qty", "1"),
            }
        except Exception as e:
            print(f"\n  ⚠ PW error {asin} (attempt {attempt+1}): {e}")
            if attempt == 0:
                time.sleep(3)
    return None


# ══════════════════ MAIN ══════════════════

def main():
    asins = load_asins()
    total = len(asins)
    print(f"📋 Loaded {total} ASINs from {INPUT_FILE}")

    if not asins:
        print("❌ No valid ASINs found!")
        return

    session = create_session()

    # ── Warm up session (get cookies from Amazon) ──
    print("\n🔥 Warming up session...")
    warmup_session(session)
    print("✅ Session ready!\n")

    pw_started = False
    pw_uses = 0

    results = []
    stats = {"REQ": 0, "PW": 0, "FAIL": 0}
    start = time.time()

    try:
        for i, asin in enumerate(asins, 1):
            result = scrape_requests(asin, session)
            method = "REQ"

            if not result:
                if not pw_started:
                    print("\n  🌐 Starting Playwright browser...")
                    pw_start()
                    pw_started = True
                    try:
                        _page.goto("https://www.amazon.in/", timeout=15000)
                        time.sleep(2)
                    except Exception:
                        pass

                result = scrape_playwright(asin)
                method = "PW" if result else "FAIL"
                pw_uses += 1

                if pw_uses >= PW_RECYCLE:
                    print("\n  🔄 Recycling browser context...")
                    pw_recycle()
                    pw_uses = 0

            if not result:
                result = empty_result(asin)
                method = "FAIL"

            results.append(result)
            stats[method] += 1

            pct = int(100 * i / total)
            price_s = result.get("Price", "N/A")[:14]
            elapsed = time.time() - start
            rate = i / elapsed if elapsed > 0 else 0
            eta = (total - i) / rate if rate > 0 else 0

            print(
                f"\r  [{pct:3d}%] {i}/{total} | {method:4s} | "
                f"{price_s:<14} | ETA {eta:.0f}s  ",
                end="", flush=True,
            )

            if i < total:
                time.sleep(BASE_DELAY + random.random() * JITTER)

            if i % BATCH_SIZE == 0 and i < total:
                print(f"\n  ⏳ Cooldown {COOLDOWN}s "
                      f"(batch {i // BATCH_SIZE})...")
                time.sleep(COOLDOWN)

    except KeyboardInterrupt:
        print("\n\n🛑 Interrupted!")
    except Exception as e:
        print(f"\n\n❌ Error: {e}")
    finally:
        print(f"\n\n💾 Writing {len(results)} results to {OUTPUT_FILE}...")
        with open(OUTPUT_FILE, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            w.writeheader()
            w.writerows(results)

        if pw_started:
            pw_stop()

    elapsed_min = (time.time() - start) / 60
    done = sum(stats.values())
    print(f"\n{'═'*50}")
    print(f"  ✅ Done: {done} ASINs in {elapsed_min:.1f} min")
    print(f"  ⚡ Requests:   {stats['REQ']}")
    print(f"  🌐 Playwright: {stats['PW']}")
    print(f"  ❌ Failed:     {stats['FAIL']}")
    print(f"  📁 Output:     {OUTPUT_FILE}")
    print(f"{'═'*50}")

    with open("run_summary.txt", "w") as f:
        f.write(f"{done}\n")
        f.write(f"{elapsed_min:.1f}\n")
        f.write(f"{stats['REQ']}\n")
        f.write(f"{stats['PW']}\n")
        f.write(f"{stats['FAIL']}\n")


if __name__ == "__main__":
    main()
