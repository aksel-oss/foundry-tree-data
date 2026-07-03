# foundry-tree-data

Live market-price data for **[Foundry Tree](https://foundry-tree.netlify.app)** —
a Warframe crafting tech-tree explorer.

A GitHub Action in this repo runs [`build_prices.py`](build_prices.py) every
6 hours: it reads the item dataset from the live site, pulls recent
closed-trade statistics (and live relic sell listings) from
[warframe.market](https://warframe.market), and commits the result as
[`prices.json`](prices.json).

The app fetches the file at runtime from

```
https://raw.githubusercontent.com/aksel-oss/foundry-tree-data/main/prices.json
```

so prices stay fresh **without redeploying the site**. This repo is public
only so that `raw.githubusercontent.com` serves the file with open CORS —
there is nothing personal here, just aggregated public market data.

## Format

```jsonc
{
  "generated": "2026-07-03T12:00:00+00:00",   // UTC timestamp of the scrape
  "count": 800,                                // number of item prices
  "prices":      { "<uniqueName>": { "p": 42, "vol": 17, "s": "wm_slug" } },
  "relicPrices": { "Axi A20":      { "p": 12, "vol": 6,  "s": "axi_a20_relic" } }
}
```

`p` = volume-weighted average platinum over the last 3 days of closed trades
(relics: recent live intact sell listings), `vol` = trade volume behind that
average, `s` = warframe.market URL slug.

Prices © the [warframe.market](https://warframe.market) community.
Warframe and all game assets are © Digital Extremes.
