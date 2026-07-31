#!/usr/bin/env python3
"""
Fetch recent closed-trade prices from warframe.market for all tradable items
in the Foundry Tree dataset. Runs from the public foundry-tree-data repo so
price refreshes never touch the main (private) repo or trigger a site deploy.

Reads recipes.json from the LIVE site (no access to the private repo needed),
averages the last 3 days of closed-trade medians weighted by volume, and adds
live 48h sell-listing prices for relics.

Output: ./prices.json
  { generated, count,
    prices:      { uniqueName: { p: avg_plat, vol: volume, s: wm_slug } },
    relicPrices: { "Axi A20":   { p: avg_plat, vol: volume, s: wm_slug } } }

The app matches items by uniqueName -> gameRef from the warframe.market index;
`s` is the warframe.market slug so the front-end can query live orders.

Run:  python3 build_prices.py
"""
import json
import os
import sys
import time
import datetime
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "prices.json")
RECIPES_URL = "https://foundry-tree.netlify.app/data/recipes.json"
WFM_API = "https://api.warframe.market/v1/items"
WFM_V2 = "https://api.warframe.market/v2/items"
DAYS = 3
STALE_H = 24  # how long an upstream outage may stay quiet before the run fails loudly


def fetch(url, tries=1, gap=30):
    # Any single-request failure (hang, non-2xx, truncated JSON) returns None; the
    # per-item loops count it as an error and move on. A hung curl once killed the
    # whole run via an unhandled TimeoutExpired — 2026-07-06, run 28797746620.
    # `tries` > 1 is for the two index fetches only — the per-item loop must stay at
    # one attempt or a bad API day would run past the job's 30-minute timeout.
    for n in range(tries):
        if n:
            time.sleep(gap * n)
        try:
            res = subprocess.run(
                ["curl", "-sSL", "--fail", "--max-time", "45", "-H", "Accept: application/json",
                 "-H", "Platform: pc", "-H", "User-Agent: wf-tree-prices", url],
                capture_output=True, timeout=60,
            )
        except (subprocess.TimeoutExpired, OSError):
            continue
        if res.returncode != 0:
            continue
        try:
            return json.loads(res.stdout.decode("utf-8"))
        except ValueError:
            continue
    return None


def skip(msg):
    """Upstream is down, not broken.

    warframe.market goes behind a Cloudflare 5xx for half an hour at a time
    (2026-07-31, run 30619556779: 521 on every endpoint, site root included).
    Failing the run for that just mails a failure notice about someone else's
    server, so keep the last prices.json and end quietly — but only while it is
    still fresh. Past STALE_H the outage is no longer a blip and the email is
    worth having.
    """
    age = None
    try:
        with open(OUT) as f:
            gen = json.load(f)["generated"]
        age = (datetime.datetime.now(datetime.timezone.utc)
               - datetime.datetime.fromisoformat(gen)).total_seconds() / 3600
    except (OSError, KeyError, ValueError, TypeError):
        age = None
    if age is None or age > STALE_H:
        old = "no readable prices.json" if age is None else f"prices.json is {age:.0f}h old"
        print(f"ERROR: {msg} — {old}", file=sys.stderr)
        sys.exit(1)
    print(f"SKIP: {msg} — keeping prices.json ({age:.1f}h old)")
    sys.exit(0)


def main():
    # 1) Load recipes from the live site to know which uniqueNames we care about
    print(f"Fetching {RECIPES_URL} ...")
    recipes = fetch(RECIPES_URL, tries=3)
    if not recipes or "items" not in recipes:
        skip("could not fetch recipes.json from the live site")
    our_items = recipes.get("items", {})
    our_recipes = recipes.get("recipes", {})

    # Collect all uniqueNames we display (roots + tradable components)
    our_unames = set()
    for name, info in our_items.items():
        our_unames.add(info["uniqueName"])
    for uname, rec in our_recipes.items():
        for comp in rec.get("components", []):
            if comp.get("isPart") and comp.get("drops"):
                our_unames.add(comp["uniqueName"])

    # Collect relic base names for separate price lookup
    relic_map = recipes.get("relicMap", {})

    # 2) Fetch warframe.market item index and build gameRef -> slug mapping
    print("Fetching warframe.market item index...")
    idx = fetch(WFM_V2, tries=3)
    if not idx or "data" not in idx:
        skip("could not fetch the warframe.market item index")

    ref_to_slug = {}
    for item in idx["data"]:
        ref = item.get("gameRef", "")
        if ref:
            ref_to_slug[ref] = item["slug"]

    # 3) Find which of our items have market listings
    to_fetch = {}
    for uname in our_unames:
        slug = ref_to_slug.get(uname)
        # WFCD uses *Component for warframe parts, warframe.market uses *Blueprint
        if not slug and uname.endswith("Component"):
            slug = ref_to_slug.get(uname[:-len("Component")] + "Blueprint")
        if slug:
            to_fetch[uname] = slug

    print(f"  {len(to_fetch)} of {len(our_unames)} items found on warframe.market")

    # 4) Fetch statistics for each, extract recent closed-trade prices
    prices = {}
    errors = 0
    for i, (uname, slug) in enumerate(sorted(to_fetch.items(), key=lambda x: x[1])):
        if i > 0 and i % 3 == 0:
            time.sleep(0.35)
        data = fetch(f"{WFM_API}/{slug}/statistics")
        if not data:
            errors += 1
            continue
        try:
            days90 = data["payload"]["statistics_closed"]["90days"]
        except (KeyError, TypeError):
            errors += 1
            continue

        recent = days90[-DAYS:] if len(days90) >= DAYS else days90
        if not recent:
            continue

        total_vol = sum(d.get("volume", 0) for d in recent)
        if total_vol == 0:
            continue

        weighted_sum = sum(d.get("median", 0) * d.get("volume", 0) for d in recent)
        avg = round(weighted_sum / total_vol)

        prices[uname] = {"p": avg, "vol": total_vol, "s": slug}

        if (i + 1) % 50 == 0:
            print(f"  fetched {i + 1}/{len(to_fetch)}...")

    # 5) Fetch relic prices (slug = "axi_a20_relic" etc.)
    relic_prices = {}
    slug_to_base = {base.lower().replace(" ", "_") + "_relic": base for base in relic_map}
    relic_slugs = sorted(slug_to_base.keys())
    print(f"  fetching {len(relic_slugs)} relic prices...")
    relic_errors = 0
    for i, slug in enumerate(relic_slugs):
        if i > 0 and i % 3 == 0:
            time.sleep(0.35)
        data = fetch(f"{WFM_API}/{slug}/statistics")
        if not data:
            relic_errors += 1
            continue
        try:
            live = data["payload"]["statistics_live"]["48hours"]
        except (KeyError, TypeError):
            relic_errors += 1
            continue
        sells = [d for d in live if d.get("order_type") == "sell" and d.get("subtype", "intact") == "intact"]
        recent = sells[-6:] if len(sells) >= 6 else sells
        if not recent:
            continue
        total_vol = sum(d.get("volume", 0) for d in recent)
        if total_vol == 0:
            continue
        weighted_sum = sum(d.get("median", 0) * d.get("volume", 0) for d in recent)
        avg = round(weighted_sum / total_vol)
        relic_prices[slug_to_base[slug]] = {"p": avg, "vol": total_vol, "s": slug}
        if (i + 1) % 100 == 0:
            print(f"  relics: {i + 1}/{len(relic_slugs)}...")
    print(f"  {len(relic_prices)} relic prices fetched")

    # Sanity floor: a near-empty result means the API died mid-run — keep the old file
    if len(prices) < 100:
        skip(f"only {len(prices)} prices fetched — refusing to overwrite")

    # 6) Write output
    payload = {
        "generated": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "count": len(prices),
        "prices": prices,
        "relicPrices": relic_prices,
    }
    with open(OUT, "w") as f:
        json.dump(payload, f, separators=(",", ":"))

    size_kb = os.path.getsize(OUT) // 1024
    print(f"  wrote prices.json: {len(prices)} prices + {len(relic_prices)} relic prices, {size_kb} KB")
    if errors or relic_errors:
        print(f"  ({errors} item + {relic_errors} relic fetch errors)")


if __name__ == "__main__":
    main()
