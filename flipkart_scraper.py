#!/usr/bin/env python3
"""
Flipkart Scraper — GitHub Actions Edition
Uses Playwright (headless browser) since Flipkart blocks raw requests.
Sequential with delays to avoid detection.
"""

import csv
import json
import random
import re
import sys
import time

# ══════════════════ CONFIG ══════════════════

INPUT_FILE  = "pids.csv"
OUTPUT_FILE = "flipkart_data.csv"
BASE_URL    = "https://www.flipkart.com/product/p/itm?pid="
FIELDS      = ["PID", "Product URL", "Title", "Selling Price",
               "Seller", "Seller Rating"]

BASE_DELAY  = 1.0
JITTER      = 0.5
BATCH_SIZE  = 40
COOLDOWN    = 8
RECYCLE     = 60

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
]

EXTRACT_JS = r"""
() => {
    function txt(el) {
        return el ? (el.innerText || el.textContent || '').trim() : '';
    }

    // Title
    let title = '';
    for (const sel of ['span.VU-ZEz', 'span.B_NuCI', 'h1._9E25nV', 'h1.yhB1nd', 'h1']) {
        let el = document.querySelector(sel);
        if (el && txt(el)) { title = txt(el); break; }
    }
    if (!title) {
        let meta = document.querySelector('meta[property="og:title"]');
        if (meta) title = meta.getAttribute('content') || '';
    }
    title = title || 'N/A';

    // Price
    let price = 'N/A';
    let priceClasses = ['Nx9bqj.CxhGGd', 'Nx9bqj', 'CEmiEU', '_30jeq3._16Jk6d', '_30jeq3', 'hl05eU'];
    for (const cls of priceClasses) {
        let els = document.querySelectorAll('div.' + cls.replace(/\s+/g, '.'));
        for (let el of els) {
            let t = txt(el);
            if (t.includes('₹')) {
                let m = t.replace(/,/g, '').match(/₹\s*(\d+)/);
                if (m) { price = '₹' + m[1]; break; }
            }
        }
        if (price !== 'N/A') break;
    }

    // Fallback: JSON-LD
    if (price === 'N/A') {
        let scripts = document.querySelectorAll('script[type="application/ld+json"]');
        for (let s of scripts) {
            try {
                let data = JSON.parse(s.textContent);
                if (data.offers) {
                    let o = Array.isArray(data.offers) ? data.offers[0] : data.offers;
                    if (o && o.price) { price = '₹' + Math.round(o.price); break; }
                }
            } catch(e) {}
        }
    }

    // Seller
    let seller = 'N/A';
    let sellerDiv = document.getElementById('sellerName');
    if (sellerDiv) {
        let span = sellerDiv.querySelector('span');
        if (span) {
            let t = txt(span).replace(/[\d.]+$/, '').trim();
            if (t) seller = t;
        }
    }
    if (seller === 'N/A') {
        for (const cls of ['yeLeBC', '_1RLviY', 'wHxIto']) {
            let el = document.querySelector('span.' + cls);
            if (el) {
                let t = txt(el).replace(/[\d.]+$/, '').trim();
                if (t) { seller = t; break; }
            }
        }
    }

    // Seller Rating
    let rating = 'N/A';
    if (sellerDiv) {
        let t = txt(sellerDiv);
        let m = t.match(/(\d+\.?\d*)\s*$/);
        if (m && parseFloat(m[1]) <= 5) rating = m[1];
    }
    if (rating === 'N/A') {
        for (const cls of ['_1cPkYt', 'uA5CGE']) {
            let el = document.querySelector('.' + cls);
            if (el) {
                let m = txt(el).match(/(\d+\.?\d*)/);
                if (m && parseFloat(m[1]) <= 5) { rating = m[1]; break; }
            }
        }
    }

    return { title, price, seller, rating };
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


# ══════════════════ PLAYWRIGHT ══════════════════

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


def scrape_product(pid, index, total):
    url = f"{BASE_URL}{pid}"

    for attempt in range(2):
        try:
            _page.goto(url, wait_until="domcontentloaded", timeout=20000)

            # Wait for title or price to appear
            _page.wait_for_selector(
                "span.VU-ZEz, span.B_NuCI, h1, div.Nx9bqj, div._30jeq3",
                timeout=10000,
            )

            # Check not found
            content = _page.content()[:3000]
            if "Sorry, no results found" in content or "Page Not Found" in content:
                print(f"\r  [{index}/{total}] ✗ {pid} — NOT FOUND",
                      flush=True)
                return {
                    "PID": pid, "Product URL": url,
                    "Title": "PRODUCT NOT FOUND",
                    "Selling Price": "N/A",
                    "Seller": "N/A", "Seller Rating": "N/A",
                }

            data = _page.evaluate(EXTRACT_JS)
            if not data:
                continue

            result = {
                "PID": pid,
                "Product URL": _page.url,
                "Title": data.get("title", "N/A"),
                "Selling Price": data.get("price", "N/A"),
                "Seller": data.get("seller", "N/A"),
                "Seller Rating": data.get("rating", "N/A"),
            }

            icon = "✓" if result["Selling Price"] != "N/A" else "⚠"
            price_s = result["Selling Price"][:12]
            title_s = result["Title"][:30]
            print(
                f"\r  [{index}/{total}] {icon} {pid} | "
                f"{price_s:<12} | {title_s}...",
                flush=True,
            )
            return result

        except Exception as e:
            if attempt == 0:
                time.sleep(2)
            else:
                print(
                    f"\r  [{index}/{total}] ✗ {pid} — {str(e)[:50]}",
                    flush=True,
                )

    return empty_result(pid)


# ══════════════════ MAIN ══════════════════

def main():
    pids = read_pids()
    if not pids:
        print("❌ No PIDs found!")
        return

    total = len(pids)
    print(f"🚀 Scraping {total} Flipkart products...\n")

    pw_start()

    # Warmup
    print("  🔥 Warming up browser...")
    try:
        _page.goto("https://www.flipkart.com/", timeout=15000)
        time.sleep(2)
    except Exception:
        pass
    print("  ✅ Ready!\n")

    results = []
    pw_uses = 0
    start = time.time()

    try:
        for i, pid in enumerate(pids, 1):
            result = scrape_product(pid, i, total)
            results.append(result)
            pw_uses += 1

            if pw_uses >= RECYCLE:
                print(f"\n  🔄 Recycling browser context...")
                pw_recycle()
                pw_uses = 0

            if i < total:
                time.sleep(BASE_DELAY + random.random() * JITTER)

            if i % BATCH_SIZE == 0 and i < total:
                print(f"\n  ⏳ Cooldown {COOLDOWN}s (batch {i // BATCH_SIZE})...")
                time.sleep(COOLDOWN)

    except KeyboardInterrupt:
        print("\n🛑 Interrupted!")
    except Exception as e:
        print(f"\n❌ Error: {e}")
    finally:
        pw_stop()

    # Save
    print(f"\n💾 Writing {len(results)} results to {OUTPUT_FILE}...")
    with open(OUTPUT_FILE, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(results)

    # Stats
    elapsed = time.time() - start
    elapsed_min = elapsed / 60
    success = sum(1 for r in results if r["Selling Price"] != "N/A")

    print(f"\n{'═'*50}")
    print(f"  ✅ Done: {total} products in {elapsed_min:.1f} min")
    print(f"  💰 Price found:   {success} ({success/total*100:.1f}%)")
    print(f"  ❌ Price missing: {total - success}")
    print(f"  📁 Output:        {OUTPUT_FILE}")
    print(f"{'═'*50}")

    with open("run_summary.txt", "w") as f:
        f.write(f"{total}\n")
        f.write(f"{elapsed_min:.1f}\n")
        f.write(f"{success}\n")
        f.write(f"0\n")
        f.write(f"{total - success}\n")


if __name__ == "__main__":
    main()
