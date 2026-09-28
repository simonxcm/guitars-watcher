# Guitar watch

Sends a phone notification when a new used **acoustic Martin** or **acoustic Gibson** is listed at
[Guitare Village](https://www.guitare-village.com/website/index.php/categorie-produit/occasion/acoustiques/),
[Vinstage Music](https://www.vinstagemusic.fr/instruments-accessoires-occasion/guitares-et-basses/guitares-acoustiques) or
[Hurricane Music](https://hurricanemusic.fr/s/330/guitare-occasion-nantes) (Nantes shop only).
Electric guitars and basses are ignored.
Each alert shows the title, the price and a photo, and opens the listing when tapped.

Everything is free: a Python script with no dependencies, run every 5 minutes by GitHub Actions,
with notifications through [ntfy.sh](https://ntfy.sh) (no account needed).

## How it checks each shop

| Shop | Source | Brand filter |
|---|---|---|
| Guitare Village | WooCommerce's public JSON API, one search per brand in the used "Acoustiques" category (acoustic basses skipped) | Title starts with the brand |
| Vinstage Music | The used acoustic guitars page, newest first. Page 2–3 are read only if page 1 is entirely new | The shop's own brand label on each listing |
| Hurricane Music (Nantes) | The Nantes used guitars page sorted newest first, read the same way. It mixes acoustic and electric | The shop's own brand label. Before alerting, the product page must be filed under "Guitare Acoustique" and say "Disponible Hurricane Music Nantes : Oui", so electrics and Bordeaux stock are never sent |

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
