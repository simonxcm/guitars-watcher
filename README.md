# Guitar watch

Sends a phone notification when a new used **acoustic (folk) Martin or Gibson** is listed at one of
9 shops that sell guitars from private sellers (consignment or buy-back). Electric, classical,
archtop and bass guitars are ignored, and so is sold stock.
Each alert shows the title, the price and a photo, and opens the listing when tapped.

Everything is free: a Python script with no dependencies, run every 5 minutes by GitHub Actions,
with notifications through [ntfy.sh](https://ntfy.sh) (no account needed).

## Shops and how each is read

| Shop | What is read | How it stays folk / used / local |
|---|---|---|
| [Guitare Village](https://www.guitare-village.com/website/index.php/categorie-produit/occasion/acoustiques/) | WooCommerce JSON API, one search per brand in the used "Acoustiques" category | Acoustic basses skipped |
| [Vinstage Music](https://www.vinstagemusic.fr/instruments-accessoires-occasion/guitares-et-basses/guitares-acoustiques) | Used acoustic page, newest first | Brand from the shop's own label |
| [Hurricane Music](https://hurricanemusic.fr/s/330/guitare-occasion-nantes), Nantes | Nantes used guitars page, newest first | Product page must be filed under "Guitare Acoustique" and say "Disponible Hurricane Music Nantes : Oui" |
| [Galerie Casanova](https://www.galerie-casanova.com/produits/guitares-acoustiques-vintages/), Paris 1er | WooCommerce JSON API, "flat-top" category | Classical and archtop guitars are in other categories |
| [Bass N Guitar](https://bassnguitar.fr/categorie/guitares-acoustiques/), Paris 19e | WooCommerce JSON API, acoustic category | Paris stock only, not the Avignon shop |
| [Le Guitarium](https://leguitarium.fr/categorie-produit/guitares-acoustiques/), Paris 9e | WooCommerce JSON API, "folk" category | |
| [Italie Musique](https://italie-musique.com/collections/guitare-acoustique-occasion-paris), Paris 13e | Shopify JSON of the used acoustic collection | |
| [Centrale Guitars "Seconde Vie"](https://centraleguitars.com/797-seconde-vie), Paris 9e | Used section as JSON, newest first | Electrics and basses skipped using the type in the product URL |
| [California Music](https://www.californiamusic.fr/guitares-acoustiques/5--1-fr), Essonne | Acoustic page, every page (sorted by price) | Only guitars with the "okaz" (used) badge |

For every shop, the title must start with Martin or Gibson (so "Carl Martin" pedals don't count),
and titles naming a classical, nylon, archtop or bass model (Chet Atkins CEC, L-5, Super 400…) are skipped.
On newest-first lists, page 2–3 are read only if page 1 is entirely new.

`state.json` stores the IDs already seen. A listing is notified once, the first time it appears.
The very first run for a shop only records what is already listed.

## Setup

1. **Phone**: install the ntfy app ([iOS](https://apps.apple.com/app/ntfy/id1625396347),
   [Android](https://play.google.com/store/apps/details?id=io.heckel.ntfy)) and subscribe to a
   topic with a hard-to-guess name. Anyone who knows the name can read it.
   To generate one: `python3 -c "import secrets; print('guitars-' + secrets.token_urlsafe(9))"`
2. **Test the push** from this folder:
   `NTFY_TOPIC=<your-topic> python3 watch.py --test-notify`
3. **Create a public GitHub repository** and push this folder to it:
   ```sh
   git init && git add -A && git commit -m "Guitar watch"
   git branch -M main
   git remote add origin git@github.com:<you>/guitar-watch.git
   git push -u origin main
   ```
4. In the repository, go to **Settings → Secrets and variables → Actions → New repository secret**.
   Name it `NTFY_TOPIC` and set your topic as the value.
5. Go to **Actions → Watch guitars → Run workflow** to start a first run by hand. It should turn green.
   After that it runs every 5 minutes.

**Public or private repository?** On a public repository, GitHub Actions is free with no limit.
A private one gets 2,000 free minutes a month, and each run counts as a full minute.
If you make it private, change the cron in `.github/workflows/watch.yml` to `*/30 * * * *`.

## Everyday use

- **Run locally**: `NTFY_TOPIC=<your-topic> python3 watch.py`, or `python3 watch.py --dry-run`
  to see what would be sent without sending or saving anything.
- **Change the brands**: edit `BRANDS` at the top of `watch.py`, e.g. `("martin", "gibson", "guild")`.
- **If a shop changes its website**: the run fails and GitHub emails you. The other shops are still checked.
- **Flood guard**: if more than 10 new listings appear at once, you get a single summary push instead.
- **Keep-alive**: `state.json` records the date of the last check. That makes one commit a day,
  which stops GitHub from pausing the schedule after 60 days without activity.
