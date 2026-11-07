"""One category vocabulary across venues, so suggestion rules can compare like with like.

Polymarket: event tags (slugs such as "crypto", "fed-rates", "politics").
Kalshi:     event `category` strings ("Crypto", "Economics", "Elections", ...).
Order matters: the first matching category wins, and crypto is checked first so a "bitcoin ETF
politics" market still counts as crypto.
"""
from __future__ import annotations

import re

CATEGORIES: list[tuple[str, tuple[str, ...]]] = [
    ("crypto", ("crypto", "bitcoin", "ethereum", "solana", "xrp", "dogecoin", "crypto-prices", "stablecoins",
                "defi", "memecoins", "airdrops", "nft")),
    ("economics", ("economics", "economy", "fed", "fed-rates", "fomc", "inflation", "cpi", "jobs", "gdp",
                   "interest-rates", "recession", "financials", "finance", "stocks", "commodities", "earnings",
                   "business", "companies", "tariffs", "ipo", "treasuries")),
    ("politics", ("politics", "elections", "us-politics", "world-elections", "global-elections", "geopolitics",
                  "world", "government", "trump", "congress", "senate", "supreme-court", "middle-east",
                  "ukraine", "foreign-policy", "us-presidential-election")),
    ("sports", ("sports", "soccer", "nba", "nfl", "mlb", "nhl", "tennis", "golf", "f1", "ufc", "boxing",
                "cricket", "esports", "games", "olympics")),
    ("science_tech", ("science-and-technology", "science", "tech", "technology", "ai", "space", "openai")),
    ("climate_weather", ("climate-and-weather", "climate", "weather", "hurricanes", "temperature")),
    ("culture", ("culture", "pop-culture", "entertainment", "movies", "music", "awards", "tv", "celebrities",
                 "social-media", "mentions")),
    ("health", ("health", "covid", "pandemics", "measles")),
    ("other", ("other",)),
]
_SLUG = re.compile(r"[^a-z0-9]+")

CRYPTO_ASSETS: dict[str, tuple[str, ...]] = {
    "btc": ("bitcoin", "btc"), "eth": ("ethereum", "eth", "ether"), "sol": ("solana", "sol"),
    "xrp": ("xrp", "ripple"), "doge": ("dogecoin", "doge"), "bnb": ("bnb", "binance coin"),
    "ada": ("cardano", "ada"), "hype": ("hyperliquid", "hype"), "ltc": ("litecoin", "ltc"),
}


def slug(s: str) -> str:
    return _SLUG.sub("-", (s or "").lower()).strip("-")


def canonical(labels: list[str] | str | None) -> str | None:
    """Venue tags / category string -> one of the CATEGORIES names, or None if nothing matched."""
    if not labels:
        return None
    items = [labels] if isinstance(labels, str) else labels
    slugs = {slug(x) for x in items if x}
    for name, keys in CATEGORIES:
        if slugs & set(keys):
            return name
    return None


def crypto_asset(text: str, tags: list[str] | None = None) -> str | None:
    """Which coin a crypto market is about (btc, eth, ...), or None if it cannot tell or names several."""
    words = set(re.findall(r"[a-z0-9]+", (text or "").lower())) | {slug(t) for t in (tags or [])}
    found = {asset for asset, names in CRYPTO_ASSETS.items() if words & set(names)}
    return found.pop() if len(found) == 1 else None
