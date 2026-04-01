#!/usr/bin/env python3
"""
Flipkart Scraper — GitHub Actions Edition
Multi-threaded, requests-only (no browser needed)
"""

import csv
import json
import random
import re
import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

# ══════════════════ CONFIG ══════════════════

INPUT_FILE   = "pids.csv"
OUTPUT_FILE  = "flipkart_data.csv"
BASE_URL     = "https://www.flipkart.com/product/p/itm?pid="
MAX_WORKERS  = 4          # slightly lower than local to be safe
TIMEOUT      = 30
FIELDS       = ["PID", "Product URL", "Title", "Selling Price",
                "Seller", "Seller Rating"]

print_lock = threading.Lock()

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


def get_headers():
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,"
                  "application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }


def safe_print(*args):
    with print_lock:
        print(*args, flush=True)


# ══════════════════ LOAD PIDs ══════════════════

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


# ══════════════════ EXTRACTORS ══════════════════

def extract_title(soup):
    from bs4 import BeautifulSoup

    title_selectors = [
        ("span", {"class": "VU-ZEz"}),
        ("span", {"class": "B_NuCI"}),
        ("h1", {"class": "_9E25nV"}),
        ("h1", {"class": "yhB1nd"}),
    ]
    for tag, attrs in title_selectors:
        el = soup.find(tag, attrs)
        if el:
            return el.get_text(strip=True)

    h1 = soup.find("h1")
    if h1:
        return h1.get_text(strip=True)

    meta = soup.find("meta", {"property": "og:title"})
    if meta and meta.get("content"):
        return meta["content"]

    return "N/A"


def clean_price(text):
    if not text:
        return "N/A"
    text = re.sub(r"[a-zA-Z]+$", "", text)
    m = re.search(r"₹\s*([\d,]+)", text)
    if m:
        return "₹" + m.group(1)
    return "N/A"


def extract_price(soup, html_text):
    # Method 1: class selectors
    price_classes = [
        "Nx9bqj CxhGGd", "Nx9bqj", "CEmiEU",
        "_30jeq3 _16Jk6d", "_30jeq3", "hl05eU",
    ]
    for cls in price_classes:
        for el in soup.find_all("div", class_=cls):
            text = el.get_text(strip=True)
            if "₹" in text:
                p = clean_price(text)
                if p != "N/A":
                    return p

    # Method 2: regex class patterns
    for pattern in ["Nx9bqj", "_30jeq3", "CEmiEU", "hl05eU"]:
        for el in soup.find_all(class_=re.compile(pattern)):
            text = el.get_text(strip=True)
            if "₹" in text:
                p = clean_price(text)
                if p != "N/A":
                    return p

    # Method 3: JSON-LD
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string)
            if isinstance(data, dict) and "offers" in data:
                offers = data["offers"]
                if isinstance(offers, dict) and "price" in offers:
                    return "₹" + str(int(float(offers["price"])))
                elif isinstance(offers, list) and offers:
                    if "price" in offers[0]:
                        return "₹" + str(int(float(offers[0]["price"])))
        except Exception:
            pass

    # Method 4: regex in raw HTML
    m = re.search(r'"price"\s*:\s*["\']?(\d+(?:\.\d+)?)["\']?', html_text)
    if m:
        return "₹" + str(int(float(m.group(1))))

    return "N/A"


def extract_seller(soup):
    # Method 1: seller div ID
    seller_div = soup.find("div", {"id": "sellerName"})
    if seller_div:
        span = seller_div.find("span")
        if span:
            text = span.get_text(strip=True)
            clean = re.sub(r"[\d.]+$", "", text).strip()
            if clean:
                return clean

    # Method 2: seller classes
    for cls in ["yeLeBC", "_1RLviY", "wHxIto"]:
        el = soup.find("span", class_=cls)
        if el:
            text = el.get_text(strip=True)
            clean = re.sub(r"[\d.]+$", "", text).strip()
            if clean:
                return clean

    # Method 3: seller section
    section = soup.find("div", id=re.compile("seller", re.I))
    if section:
        for span in section.find_all("span"):
            text = span.get_text(strip=True)
            if text and len(text) > 2 and not text.isdigit():
                clean = re.sub(r"[\d.]+$", "", text).strip()
                if clean:
                    return clean

    return "N/A"


def extract_seller_rating(soup):
    # Method 1: from seller div
    seller_div = soup.find("div", {"id": "sellerName"})
    if seller_div:
        text = seller_div.get_text(strip=True)
        m = re.search(r"(\d+\.?\d*)\s*$", text)
        if m:
            r = float(m.group(1))
            if 0 < r <= 5:
                return str(r)

    # Method 2: rating classes
    for cls in ["_1cPkYt", "uA5CGE"]:
        el = soup.find(class_=cls)
        if el:
            text = el.get_text(strip=True)
            m = re.search(r"(\d+\.?\d*)", text)
            if m:
                r = float(m.group(1))
                if 0 < r <= 5:
                    return str(r)

    return "N/A"


# ══════════════════ SCRAPE ONE ══════════════════

def scrape_product(pid, index, total):
    url = f"{BASE_URL}{pid}"
    session = requests.Session()

    for attempt in range(3):
        try:
            time.sleep(random.uniform(0.5, 1.5))

            r = session.get(url, headers=get_headers(), timeout=TIMEOUT)
            r.raise_for_status()

            html = r.text

            # Import here to avoid issues at module level
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(r.content, "html.parser")

            # Not found check
            if "Sorry, no results found" in html or "Page Not Found" in html:
                safe_print(f"  [{index}/{total}] ✗ {pid} — NOT FOUND")
                return {
                    "PID": pid, "Product URL": url,
                    "Title": "PRODUCT NOT FOUND",
                    "Selling Price": "N/A",
                    "Seller": "N/A", "Seller Rating": "N/A",
                }

            title = extract_title(soup)
            price = extract_price(soup, html)
            seller = extract_seller(soup)
            rating = extract_seller_rating(soup)

            result = {
                "PID": pid,
                "Product URL": str(r.url),
                "Title": title,
                "Selling Price": price,
                "Seller": seller,
                "Seller Rating": rating,
            }

            icon = "✓" if price != "N/A" else "⚠"
            safe_print(
                f"  [{index}/{total}] {icon} {pid} | "
                f"{price} | {seller} | {title[:35]}..."
            )
            return result

        except requests.exceptions.HTTPError as e:
            if attempt == 2:
                safe_print(f"  [{index}/{total}] ✗ {pid} — HTTP {e}")
        except requests.exceptions.ConnectionError:
            if attempt == 2:
                safe_print(f"  [{index}/{total}] ✗ {pid} — Connection Error")
            time.sleep(2)
        except Exception as e:
            if attempt == 2:
                safe_print(
                    f"  [{index}/{total}] ✗ {pid} — {str(e)[:50]}"
                )

        time.sleep(attempt + 1)

    return {
        "PID": pid, "Product URL": url,
        "Title": "FAILED TO FETCH", "Selling Price": "N/A",
        "Seller": "N/A", "Seller Rating": "N/A",
    }


# ══════════════════ MAIN ══════════════════

def main():
    pids = read_pids()
    if not pids:
        print("❌ No PIDs found!")
        return

    total = len(pids)
    print(f"🚀 Scraping {total} Flipkart products "
          f"({MAX_WORKERS} workers)...\n")

    start = time.time()
    results = []

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {
            executor.submit(scrape_product, pid, i, total): pid
            for i, pid in enumerate(pids, 1)
        }
        for future in as_completed(futures):
            try:
                results.append(future.result())
            except Exception as e:
                safe_print(f"  ❌ Thread error: {e}")

    # Sort results back to original PID order
    pid_order = {pid: i for i, pid in enumerate(pids)}
    results.sort(key=lambda r: pid_order.get(r["PID"], 999999))

    # Save CSV
    print(f"\n💾 Writing {len(results)} results to {OUTPUT_FILE}...")
    with open(OUTPUT_FILE, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(results)

    # Stats
    elapsed = time.time() - start
    success = sum(1 for r in results if r["Selling Price"] != "N/A")

    print(f"\n{'═'*50}")
    print(f"  ✅ Done: {total} products in {elapsed:.1f}s")
    print(f"  💰 Price found:   {success} ({success/total*100:.1f}%)")
    print(f"  ❌ Price missing: {total - success}")
    print(f"  ⚡ Speed:         {total/elapsed:.1f} products/sec")
    print(f"  📁 Output:        {OUTPUT_FILE}")
    print(f"{'═'*50}")

    # Write summary for email
    elapsed_min = elapsed / 60
    with open("run_summary.txt", "w") as f:
        f.write(f"{total}\n")
        f.write(f"{elapsed_min:.1f}\n")
        f.write(f"{success}\n")
        f.write(f"0\n")
        f.write(f"{total - success}\n")


if __name__ == "__main__":
    main()
