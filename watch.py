#!/usr/bin/env python3
"""Watch used-guitar shops for new acoustic (folk) Martin and Gibson listings and push a phone
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
MAX_PAGES = 3
FAIL_AFTER = 3  # a shop must fail this many runs in a row to fail the run, so one-off outages stay quiet

GV_SITE = "https://www.guitare-village.com/website"
HM_LIST = "https://hurricanemusic.fr/s/330/guitare-occasion-nantes"
CASANOVA = "https://www.galerie-casanova.com"
BASS_N_GUITAR = "https://bassnguitar.fr"
GUITARIUM = "https://leguitarium.fr"
IM_COLLECTION = "https://italie-musique.com/collections/guitare-acoustique-occasion-paris"
CG_LIST = "https://centraleguitars.com/797-seconde-vie"
CM_BASE = "https://www.californiamusic.fr"
WOODSTORE = "https://www.woodstore.fr"
GC_LIST = "https://guitarecollection.com/boutique/guitares-acoustiques-vintage"
GUITAR_STREET = "https://guitarstreet.fr"

# Brand first, optionally after "Guitare"/"Occasion", a year or "C.F.": matches "1963 Gibson ES-175" and
# "Guitare Martin D-28", not "Carl Martin" pedals.
BRAND_RE = re.compile(r"^(?:(?:guitare|occasion)\s+)?(?:(?:19|20)\d\d\s+)?(?:c\.?\s*f\.?\s+)?(?:" + "|".join(BRANDS) + r")\b", re.I)

# Folk guitars only: some shops file nylon-string, archtop, bass and other fretted models under "acoustic".
NOT_FOLK_RE = re.compile(
    r"classi|nylon|\bcec\b|chet atkins ce\b|\bn-(?:10|20)\b|\bc-\d\b"
    r"|archtop|\bl-(?:4|5|7|10|12|30|48|50|75)(?!\d)|super ?[34]00|\bbass"
    r"|mandol|ukul|\buke\b|banjo|tiple",
    re.I)

# California Music sends Latin-1 bytes in pages read as UTF-8.
codecs.register_error("latin1_fallback", lambda e: (e.object[e.start:e.end].decode("latin-1"), e.end))


@dataclass
class Listing:
    id: str
    title: str
    price: str
    url: str
    brand: str = ""
    image: str = ""
    available: bool = True
    details: str = ""  # description, for shops that only say there whether a guitar is acoustic

    def wanted(self):
        return (self.available and bool(BRAND_RE.match(self.brand or self.title))
                and not NOT_FOLK_RE.search(self.title))


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


def euros(amount):
    return f"{amount:,.0f} €".replace(",", " ") if amount else ""


def read_pages(url_for_page, parse, known, headers=None):
    """Read a paginated listing until a page is empty, repeats this run's IDs, or holds a known ID.

    On a newest-first list the known ID means everything below was already seen.
    Pass an empty `known` to read every page of a list sorted some other way.
    """
    listings = {}
    for page in range(1, MAX_PAGES + 1):
        items = parse(fetch(url_for_page(page), headers=headers).decode("utf-8", "latin1_fallback"))
        # Past the last page some shops serve page 1 again, hence the check on this run's IDs too.
        done = not items or any(item.id in known or item.id in listings for item in items)
        for item in items:
            listings.setdefault(item.id, item)
        if done:
            break
    return list(listings.values())


# --- WooCommerce shops: public Store API, newest first --------------------------------------

def woocommerce(site, keep=lambda product: True, **params):
    query = urllib.parse.urlencode({
        "orderby": "date", "order": "desc", "per_page": 100,
        "_fields": "id,name,permalink,prices,categories,is_in_stock", **params,
    })
    listings = []
    for product in json.loads(fetch(f"{site}/wp-json/wc/store/v1/products?{query}")):
        if keep(product):
            prices = product["prices"]
            listings.append(Listing(
                id=str(product["id"]),
                title=clean(product["name"]),
                price=euros(int(prices["price"] or 0) / 10 ** prices["currency_minor_unit"]),
                url=product["permalink"],
                available=product["is_in_stock"],
                details=clean(f"{product.get('short_description') or ''} {product.get('description') or ''}"),
            ))
    return listings


def woocommerce_image(site):
    """The list is fetched without photos to stay small; this gets one for a new listing only."""
    def image(listing):
        product = json.loads(fetch(f"{site}/wp-json/wc/store/v1/products/{listing.id}?_fields=images"))
        return product["images"][0]["thumbnail"] if product["images"] else ""
    return image


def guitare_village(known):
    """One search per brand in the used acoustic category, which also holds acoustic basses."""
    no_bass = lambda product: not any("basses" in category["slug"] for category in product["categories"])
    listings = {}
    for brand in BRANDS:
        for listing in woocommerce(GV_SITE, no_bass, category="acoustiques", search=brand):
            listings[listing.id] = listing
    return list(listings.values())


def casanova(known):
    return woocommerce(CASANOVA, category="guitares-vintages-flat-top")


def bass_n_guitar(known):
    """The acoustic category mixes the Paris and Avignon shops."""
    in_paris = lambda product: any(term["name"] == "Paris"
                                   for attribute in product["attributes"] for term in attribute["terms"])
    return woocommerce(BASS_N_GUITAR, in_paris, category=46, _fields="id,name,permalink,prices,is_in_stock,attributes")


def guitarium(known):
    return woocommerce(GUITARIUM, category="folk")


def guitar_street(known):
    """The used category mixes acoustic and electric; only the description tells them apart."""
    return woocommerce(GUITAR_STREET, category="guitares-occasions",
                       _fields="id,name,permalink,prices,is_in_stock,short_description,description")


# --- Other shops ----------------------------------------------------------------------------

def hurricane(known):
    return read_pages(lambda page: f"{HM_LIST}?order=product.date_add.desc&page={page}", parse_hurricane, known)


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
            price=euros(float(price.group(1))) if price else "",
            url=link.group(1),
            brand=brand,
            image=image.group(1) if image else "",
        ))
    return items


def nantes_acoustic(listing):
    """The used list mixes acoustic and electric, and the Bordeaux shop exists: the product page tells both."""
    page = fetch(listing.url).decode("utf-8", "latin1_fallback")
    breadcrumb = re.search(r'"BreadcrumbList".*?</script>', page, re.S)
    return bool(re.search(r"Disponible Hurricane Music Nantes\s*:\s*Oui", clean(page))
                and breadcrumb and "guitare-acoustique" in breadcrumb.group(0))


def italie_musique(known):
    """Shopify: the collection's public JSON, sold guitars included."""
    products = json.loads(fetch(f"{IM_COLLECTION}/products.json?limit=250"))["products"]
    return [Listing(
        id=str(product["id"]),
        title=clean(product["title"]),
        price=euros(float(product["variants"][0]["price"])),
        url=f"https://italie-musique.com/products/{product['handle']}",
        image=product["images"][0]["src"] if product["images"] else "",
        available=any(variant["available"] for variant in product["variants"]),
    ) for product in products]


def centrale(known):
    """PrestaShop answers in JSON when asked like its own JavaScript does."""
    return read_pages(lambda page: f"{CG_LIST}?order=product.date_add.desc&page={page}", parse_centrale, known,
                      headers={"X-Requested-With": "XMLHttpRequest", "Accept": "application/json"})


def parse_centrale(page):
    items = []
    for product in json.loads(page)["products"]:
        brand = clean(product["manufacturer_name"] or "")
        items.append(Listing(
            id=str(product["id_product"]),
            title=f"{brand} {clean(product['name'])}".strip(),
            price=euros(product["price_amount"]),
            url=product["url"],
            brand=brand,
            image=((product.get("cover") or {}).get("medium") or {}).get("url", ""),
        ))
    return items


def centrale_acoustic(listing):
    """The used section also holds electrics and basses; recent URLs name the type ("guitare-electrique-…")."""
    path = urllib.parse.urlsplit(listing.url).path
    return "/guitares-electriques/" not in path and not re.search(r"electrique|basse", path.rsplit("/", 1)[-1])


def california(known):
    """Sorted by price, not date, so every page is read."""
    return read_pages(lambda page: f"{CM_BASE}/guitares-acoustiques/5--{page}-fr", parse_california, known=())


def parse_california(page):
    items = []
    for block in page.split('<table class="listing_vert">')[1:]:
        link = re.search(r'<h2><a href="([^"]*produit_(\d+)_fr\.html)" title="([^"]*)"', block)
        if not link or 'class="okaz"' not in block or "classi" in link.group(1):
            continue  # used guitars carry the "okaz" badge, new stock doesn't
        price = re.search(r'<span class="prix">(.*?)</span>', block, re.S)
        image = re.search(r'<img src="(images_produits/[^"]+)"', block)
        title = clean(link.group(3))
        items.append(Listing(
            id=link.group(2),
            title=re.sub(r"\s*-\s*EN STOCK$", "", title),
            price=clean(price.group(1)) if price else "",
            url=f"{CM_BASE}/{link.group(1)}",
            image=f"{CM_BASE}/{image.group(1)}" if image else "",
            available=title.endswith("EN STOCK"),
        ))
    return items


def acoustic_description(listing):
    text = listing.details.lower()
    return bool(re.search(r"acousti|folk", text)) and not re.search(
        r"guitare\s+[ée]lectrique|electric guitar|solid ?body|demi-caisse|semi-hollow|hollow ?body", text)


def woodstore(known):
    """Squarespace: the RSS feed holds the 20 newest guitars, far lighter than the store's JSON."""
    feed = fetch(f"{WOODSTORE}/guitares?format=rss").decode("utf-8", "latin1_fallback")
    feed = feed.replace("<![CDATA[", "").replace("]]>", "")
    items = []
    for item in re.findall(r"<item>(.*?)</item>", feed, re.S):
        link = re.search(r"<link>(.*?)</link>", item, re.S)
        title = re.search(r"<title>(.*?)</title>", item, re.S)
        if not (link and title):
            continue
        guid = re.search(r"<guid[^>]*>(.*?)</guid>", item, re.S)
        description = re.search(r"<description>(.*?)</description>", item, re.S)
        image = re.search(r'<media:content[^>]+url="([^"]+)"', item)
        items.append(Listing(
            id=clean((guid or link).group(1)),
            title=clean(title.group(1)),
            price="",
            url=clean(link.group(1)),
            image=image.group(1) if image else "",
            details=clean(html.unescape(description.group(1))) if description else "",
        ))
    return items


def guitare_collection(known):
    """Available guitars come first, then reserved and sold ones."""
    return read_pages(lambda page: f"{GC_LIST}?page={page}", parse_guitare_collection, known)


def parse_guitare_collection(page):
    items = []
    for record in page.split('<article class="record">')[1:]:
        link = re.search(r'href="(https://guitarecollection\.com/produit/[^"]+)"', record)
        title = re.search(r"<h2>(.*?)</h2>", record, re.S)
        if not (link and title):
            continue
        status = re.search(r"Disponibilit.*?<strong>(.*?)</strong>", record, re.S)
        image = re.search(r'data-src="([^"]+)"', record)
        items.append(Listing(
            id=link.group(1).rsplit("/", 1)[-1],
            title=clean(title.group(1)),
            price="",
            url=link.group(1),
            image=image.group(1) if image else "",
            available=bool(status) and clean(status.group(1)).lower().startswith("dispo"),
        ))
    return items


@dataclass
class Source:
    key: str  # name in state.json
    shop: str
    url: str  # listing page, opened from a summary push
    listings: object  # function(known IDs) -> [Listing]
    image: object = None  # function(Listing) -> photo URL, for shops whose list has none
    keep: object = None  # function(Listing) -> bool, last check on a new Martin/Gibson before notifying


SOURCES = [
    Source("guitare-village", "Guitare Village", f"{GV_SITE}/index.php/categorie-produit/occasion/acoustiques/",
           guitare_village, image=woocommerce_image(GV_SITE)),
    Source("hurricane", "Hurricane Music (Nantes)", HM_LIST, hurricane, keep=nantes_acoustic),
    Source("galerie-casanova", "Galerie Casanova (Paris 1er)", f"{CASANOVA}/produits/guitares-acoustiques-vintages/",
           casanova, image=woocommerce_image(CASANOVA)),
    Source("bass-n-guitar", "Bass N Guitar (Paris 19e)", f"{BASS_N_GUITAR}/categorie/guitares-acoustiques/",
           bass_n_guitar, image=woocommerce_image(BASS_N_GUITAR)),
    Source("le-guitarium", "Le Guitarium (Paris 9e)", f"{GUITARIUM}/categorie-produit/guitares-acoustiques/",
           guitarium, image=woocommerce_image(GUITARIUM)),
    Source("italie-musique", "Italie Musique (Paris 13e)", IM_COLLECTION, italie_musique),
    Source("centrale-guitars", "Centrale Guitars (Paris 9e)", CG_LIST, centrale, keep=centrale_acoustic),
    Source("california-music", "California Music (Essonne)", f"{CM_BASE}/guitares-acoustiques/5--1-fr", california),
    Source("woodstore", "Woodstore (Paris 9e)", f"{WOODSTORE}/guitares", woodstore, keep=acoustic_description),
    Source("guitare-collection", "Guitare Collection (Paris 9e)", GC_LIST, guitare_collection),
    Source("guitar-street", "Guitar Street (Paris 9e)", f"{GUITAR_STREET}/categorie-produit/guitares-occasions/",
           guitar_street, image=woocommerce_image(GUITAR_STREET), keep=acoustic_description),
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
    failures = state.setdefault("failures", {})
    for source in SOURCES:
        seen = state["sources"].get(source.key)
        try:
            listings = source.listings(set(seen or ()))
            if not listings:
                raise RuntimeError("nothing listed at all, has the website changed?")
            new = [l for l in listings if l.id not in (seen or {})]
            wanted = [l for l in new if seen is not None and l.wanted() and (not source.keep or source.keep(l))]
        except Exception as error:
            failures[source.key] = failures.get(source.key, 0) + 1
            print(f"{source.key}: FAILED ({failures[source.key]} in a row): {error}")
            continue
        failures.pop(source.key, None)
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

    # Forget shops removed from SOURCES.
    configured = {source.key for source in SOURCES}
    state["sources"] = {key: seen for key, seen in state["sources"].items() if key in configured}
    for key in set(failures) - configured:
        del failures[key]

    # A dated heartbeat commits at least once a day, so GitHub never pauses the schedule for inactivity.
    state["last_check"] = date.today().isoformat()
    if not args.dry_run:
        save_state(state)
    broken = [key for key, count in failures.items() if count >= FAIL_AFTER]
    if broken:
        print(f"Failing the run: {', '.join(broken)} failed {FAIL_AFTER}+ runs in a row, the website may have changed.")
    sys.exit(1 if broken else 0)


if __name__ == "__main__":
    main()
