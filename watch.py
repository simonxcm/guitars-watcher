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
BRAND_LABEL = "/".join(brand.title() for brand in BRANDS)
NTFY_SERVER = os.environ.get("NTFY_SERVER", "https://ntfy.sh")
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "")
STATE_FILE = Path(__file__).with_name("state.json")
USER_AGENT = "Mozilla/5.0 (compatible; guitar-watch/1.0; personal stock alert)"
MAX_PUSHES = 10  # more new listings than this in one run gets a single summary push

GV_API = "https://www.guitare-village.com/website/wp-json/wc/store/v1/products"
VM_BASE = "https://www.vinstagemusic.fr"
VM_LIST = VM_BASE + "/instruments-accessoires-occasion/guitares-et-basses"
HM_LIST = "https://hurricanemusic.fr/s/330/guitare-occasion-nantes"
MAX_PAGES = 3

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


def newest_first(url_for_page, parse, known):
    """Read a newest-first listing page by page, until a page holds an ID already seen."""
    listings = {}
    for page in range(1, MAX_PAGES + 1):
        items = parse(fetch(url_for_page(page)).decode("utf-8", "latin1_fallback"))
        if page == 1 and not items:
            raise RuntimeError(f"no listing parsed on {url_for_page(1)}, has the page layout changed?")
        # Past the last page some shops serve page 1 again, hence the check on this run's IDs too.
        done = not items or any(item.id in known or item.id in listings for item in items)
        for item in items:
            listings.setdefault(item.id, item)
        if done:
            break
    return list(listings.values())


def vinstage(known):
    return newest_first(lambda page: VM_LIST if page == 1 else f"{VM_LIST}/{page}", parse_vinstage, known)


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


def hurricane(known):
    return newest_first(lambda page: f"{HM_LIST}?order=product.date_add.desc&page={page}", parse_hurricane, known)


def parse_hurricane(page):
    items = []
    for block in page.split('class="product-miniature js-product-miniature"')[1:]:
        product_id = re.search(r'data-id-product="(\d+)"', block)
        link = re.search(r'class="h3 product-title"><a href="([^"]+)"[^>]*>(.*?)</a>', block, re.S)
        if not (product_id and link):
            continue
        brand = re.search(r'class="manufacturer-name[^"]*">(.*?)</h2>', block, re.S)
        price = re.search(r'itemprop="price" content="([\d.]+)"', block)
        image = re.search(r'<img[^>]+src="([^"]+)"', block)
        brand = clean(brand.group(1)) if brand else ""
        items.append(Listing(
            id=product_id.group(1),
            title=f"{brand} {clean(link.group(2))}".strip(),
            price=f"{float(price.group(1)):,.0f} €".replace(",", " ") if price else "",
            url=link.group(1),
            brand=brand,
            image=image.group(1) if image else "",
        ))
    return items


def in_nantes(listing):
    """The page only lists the Nantes shop; the product page confirms it in case that filter ever changes."""
    page = clean(fetch(listing.url).decode("utf-8", "latin1_fallback"))
    return bool(re.search(r"Disponible Hurricane Music Nantes\s*:\s*Oui", page))


@dataclass
class Source:
    key: str  # name in state.json
    shop: str
    url: str  # listing page, opened from a summary push
    listings: object  # function(known IDs) -> [Listing]
    image: object = None  # function(Listing) -> photo URL, for shops whose list has none
    keep: object = None  # function(Listing) -> bool, last check on a new Martin/Gibson before notifying


SOURCES = [
    Source("guitare-village", "Guitare Village",
           "https://www.guitare-village.com/website/index.php/categorie-produit/occasion/",
           guitare_village, image=guitare_village_image),
    Source("vinstage", "Vinstage Music", VM_LIST, vinstage),
    Source("hurricane", "Hurricane Music (Nantes)", HM_LIST, hurricane, keep=in_nantes),
]


def push(payload, dry_run):
    if dry_run:
        print("  would push:", json.dumps(payload, ensure_ascii=False))
        return
    body = json.dumps({"topic": NTFY_TOPIC, **payload}).encode()
    fetch(NTFY_SERVER, data=body, headers={"Content-Type": "application/json"})


def notify(source, listings, dry_run):
    shop = source.shop
    if len(listings) > MAX_PUSHES:
        push({"title": f"{len(listings)} new {BRAND_LABEL} listings at {shop}",
              "message": "\n".join(f"{l.title} · {l.price}" for l in listings[:20]),
              "click": source.url, "tags": ["guitar"], "priority": 4}, dry_run)
        return
    for listing in listings:
        if source.image and not listing.image:
            try:
                listing.image = source.image(listing)
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
    for source in SOURCES:
        seen = state["sources"].get(source.key)
        try:
            listings = source.listings(set(seen or ()))
            new = [l for l in listings if l.id not in (seen or {})]
            wanted = [l for l in new if seen is not None and l.wanted() and (not source.keep or source.keep(l))]
        except Exception as error:
            print(f"{source.key}: FAILED: {error}")
            failed = True
            continue
        if seen is None:
            print(f"{source.key}: first run, recorded {len(listings)} listings without notifying")
            seen = {}
        else:
            print(f"{source.key}: {len(listings)} listings checked, {len(new)} new, {len(wanted)} {BRAND_LABEL}")
            for l in wanted:
                print(f"  NEW {l.title} · {l.price} · {l.url}")
            if wanted:
                notify(source, wanted, args.dry_run)
        seen.update({l.id: l.title for l in new})
        state["sources"][source.key] = seen

    # A dated heartbeat commits at least once a day, so GitHub never pauses the schedule for inactivity.
    state["last_check"] = date.today().isoformat()
    if not args.dry_run:
        save_state(state)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
