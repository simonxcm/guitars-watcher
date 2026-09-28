#!/usr/bin/env python3
"""Watch used-guitar shops for new Martin and Gibson listings and push a phone
notification through ntfy.sh. Standard library only.

    python3 watch.py                check once, notify new listings, update state.json
    python3 watch.py --dry-run      check once, print what would be sent, change nothing
    python3 watch.py --test-notify  send one sample notification and exit

The first run for a shop only records what is already listed, without notifying.
"""
import argparse
import codecs
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date
from pathlib import Path

BRANDS = ("martin", "gibson")
NTFY_SERVER = os.environ.get("NTFY_SERVER", "https://ntfy.sh")
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "")
STATE_FILE = Path(__file__).with_name("state.json")
USER_AGENT = "Mozilla/5.0 (compatible; guitar-watch/1.0; personal stock alert)"
MAX_PUSHES = 10  # more new listings than this in one run gets a single summary push

GV_API = "https://www.guitare-village.com/website/wp-json/wc/store/v1/products"
VM_BASE = "https://www.vinstagemusic.fr"
VM_LIST = VM_BASE + "/instruments-accessoires-occasion/guitares-et-basses"
VM_MAX_PAGES = 3

# Brand first, optionally after a year or "C.F.": matches "1963 Gibson ES-175", not "Carl Martin" pedals.
BRAND_RE = re.compile(r"^(?:(?:19|20)\d\d\s+)?(?:c\.?\s*f\.?\s+)?(?:" + "|".join(BRANDS) + r")\b", re.I)

# Vinstage declares UTF-8 but some bytes are Latin-1.
codecs.register_error("latin1_fallback", lambda e: (e.object[e.start:e.end].decode("latin-1"), e.end))


@dataclass
class Listing:
    id: str
    title: str
    price: str
    url: str
    brand: str = ""
    image: str = ""

    def wanted(self):
        return bool(BRAND_RE.match(self.brand or self.title))


def fetch(url, data=None, headers=None, attempts=3):
    request = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT, **(headers or {})})
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.read()
        except (urllib.error.URLError, TimeoutError):
            if attempt == attempts:
                raise
            time.sleep(5 * attempt)


def clean(text):
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", text)).split())


def guitare_village(known):
    """WooCommerce Store API: one search per brand across every used ("occasion") category."""
    listings = {}
    for brand in BRANDS:
        query = urllib.parse.urlencode({
            "category": "occasion", "search": brand, "per_page": 100,
            "orderby": "date", "order": "desc", "_fields": "id,name,permalink,prices",
        })
        for product in json.loads(fetch(f"{GV_API}?{query}")):
            prices = product["prices"]
            amount = int(prices["price"]) / 10 ** prices["currency_minor_unit"]
            listings[str(product["id"])] = Listing(
                id=str(product["id"]),
                title=clean(product["name"]),
                price=f"{amount:,.0f} €".replace(",", " "),
                url=product["permalink"],
            )
    if not any(listing.wanted() for listing in listings.values()):
        raise RuntimeError("no Martin or Gibson found at all, has the API changed?")
    return list(listings.values())


def guitare_village_image(listing):
    product = json.loads(fetch(f"{GV_API}/{listing.id}?_fields=images"))
    return product["images"][0]["thumbnail"] if product["images"] else ""


def vinstage(known):
    """Newest-first HTML listing, no brand filter in the URL: read pages until one holds a known ID."""
    listings = []
    for page in range(1, VM_MAX_PAGES + 1):
        url = VM_LIST if page == 1 else f"{VM_LIST}/{page}"
        items = parse_vinstage(fetch(url).decode("utf-8", "latin1_fallback"))
        if not items:
            raise RuntimeError(f"no listing parsed on {url}, has the page layout changed?")
        listings += items
        if any(item.id in known for item in items):
            break
    return listings


def parse_vinstage(page):
    items = []
    for block in re.findall(r'<div class="produit">(.*?)</div>', page, re.S):
        link = re.search(r'<a href="([^"]*/(\d+))" class="libelle">(.*?)</a>', block, re.S)
        if not link:
            continue
        brand = re.search(r'<span class="marque">(.*?)</span>', block, re.S)
        price = re.search(r'<span class="prix">(.*?)</span>', block, re.S)
        image = re.search(r"url\(([^)]+)\)", block)
        items.append(Listing(
            id=link.group(2),
            title=clean(link.group(3)),
            price=clean(price.group(1)) if price else "",
            url=urllib.parse.urljoin(VM_BASE, link.group(1)),
            brand=clean(brand.group(1)) if brand else "",
            image=urllib.parse.urljoin(VM_BASE, image.group(1)) if image else "",
        ))
    return items


# (state key, shop name, listing page for summaries, fetch function, image lookup for new listings)
SOURCES = [
    ("guitare-village", "Guitare Village",
     "https://www.guitare-village.com/website/index.php/categorie-produit/occasion/",
     guitare_village, guitare_village_image),
    ("vinstage", "Vinstage Music", VM_LIST, vinstage, None),
]


def push(payload, dry_run):
    if dry_run:
        print("  would push:", json.dumps(payload, ensure_ascii=False))
        return
    body = json.dumps({"topic": NTFY_TOPIC, **payload}).encode()
    fetch(NTFY_SERVER, data=body, headers={"Content-Type": "application/json"})


def notify(shop, shop_url, listings, find_image, dry_run):
    if len(listings) > MAX_PUSHES:
        push({"title": f"{len(listings)} new Martin/Gibson listings at {shop}",
              "message": "\n".join(f"{l.title} · {l.price}" for l in listings[:20]),
              "click": shop_url, "tags": ["guitar"], "priority": 4}, dry_run)
        return
    for listing in listings:
        if find_image and not listing.image:
            try:
                listing.image = find_image(listing)
            except Exception as error:  # a missing photo should never block the alert
                print(f"  no image for {listing.id}: {error}")
        payload = {"title": " · ".join(filter(None, [listing.title, listing.price])),
                   "message": f"New listing at {shop}",
                   "click": listing.url, "tags": ["guitar"], "priority": 4}
        if listing.image:
            payload["attach"] = listing.image
        push(payload, dry_run)


def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"sources": {}}


def save_state(state):
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1, ensure_ascii=False) + "\n")
    tmp.replace(STATE_FILE)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="print pushes instead of sending, don't save state")
    parser.add_argument("--test-notify", action="store_true", help="send one sample notification and exit")
    args = parser.parse_args()

    if not NTFY_TOPIC and not args.dry_run:
        sys.exit("Set NTFY_TOPIC (your ntfy topic name), or use --dry-run.")
    if args.test_notify:
        push({"title": "Guitar watch is set up", "message": "Martin and Gibson alerts will land here.",
              "tags": ["guitar"]}, dry_run=False)
        print("Test notification sent.")
        return

    state = load_state()
    failed = False
    for key, shop, shop_url, fetch_listings, find_image in SOURCES:
        seen = state["sources"].get(key)
        try:
            listings = fetch_listings(set(seen or ()))
        except Exception as error:
            print(f"{key}: FAILED: {error}")
            failed = True
            continue
        new = [l for l in listings if l.id not in (seen or {})]
        wanted = [l for l in new if l.wanted()]
        if seen is None:
            print(f"{key}: first run, recorded {len(listings)} listings without notifying")
            seen = {}
        else:
            print(f"{key}: {len(listings)} listings checked, {len(new)} new, {len(wanted)} Martin/Gibson")
            for l in wanted:
                print(f"  NEW {l.title} · {l.price} · {l.url}")
            if wanted:
                notify(shop, shop_url, wanted, find_image, args.dry_run)
        seen.update({l.id: l.title for l in new})
        state["sources"][key] = seen

    # A dated heartbeat commits at least once a day, so GitHub never pauses the schedule for inactivity.
    state["last_check"] = date.today().isoformat()
    if not args.dry_run:
        save_state(state)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
