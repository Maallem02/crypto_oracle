"""Reachability + format of the news feeds the dashboard would use."""
import json
import xml.etree.ElementTree as ET
from collections import Counter

import requests

H = {"User-Agent": "Mozilla/5.0 CryptoOracle"}
for url in ("https://nfs.faireconomy.media/ff_calendar_thisweek.json",
            "https://nfs.faireconomy.media/ff_calendar_nextweek.json"):
    try:
        r = requests.get(url, timeout=10, headers=H)
        print(f"{url.rsplit('/', 1)[1]}: HTTP {r.status_code}, {len(r.content)} bytes, "
              f"type {r.headers.get('content-type')}")
        ev = r.json()
        print(f"   {len(ev)} events; impacts {dict(Counter(e.get('impact') for e in ev))}")
        print(f"   countries {dict(Counter(e.get('country') for e in ev).most_common(8))}")
        hi = [e for e in ev if e.get("impact") == "High"][:3]
        for e in hi:
            print("   sample:", json.dumps(e)[:220])
    except Exception as e:
        print(f"{url}: FAILED {e}")

for url in ("https://www.coindesk.com/arc/outboundfeeds/rss/",
            "https://cointelegraph.com/rss",
            "https://www.fxstreet.com/rss/news"):
    try:
        r = requests.get(url, timeout=10, headers=H)
        root = ET.fromstring(r.content)
        items = root.findall(".//item")
        print(f"\n{url}: HTTP {r.status_code}, {len(items)} items")
        for it in items[:3]:
            print("   -", (it.findtext("title") or "")[:90], "|", it.findtext("pubDate"))
    except Exception as e:
        print(f"\n{url}: FAILED {type(e).__name__}: {str(e)[:120]}")
