"""Cricbuzz Live & Recent Toss Outcomes Provider.

Scrapes real-time toss results directly from Cricbuzz live-scores, recent-scores, and match facts pages.
Uses concurrent fetching so resolving 15-20 matches takes < 3 seconds.
"""

import time
import re
import json
import urllib.request
import ssl
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Any, Optional

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

_CACHE: Dict[str, Any] = {"timestamp": 0, "tosses": []}
CACHE_TTL = 60

_TOSS_STATUS_RE = re.compile(
    r'([A-Za-z0-9\s\.\-&]+?)\s+(?:won\s+the\s+toss\s+and\s+)?(?:opt|opted|elected|chose|decided)\s+to\s+(bat|bowl|field)',
    re.IGNORECASE,
)


def _normalize_decision(dec: str) -> str:
    d = (dec or "Bowl").strip().lower()
    return "Bat" if "bat" in d else "Bowl"


def _fetch_html(url: str, timeout: float = 5.0) -> str:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as resp:
        return resp.read().decode("utf-8", errors="ignore")


def _parse_facts_page(mid: str, slug: str) -> Optional[Dict[str, Any]]:
    slug_parts = slug.split("-vs-")
    s1 = slug_parts[0].replace("-", " ").strip() if len(slug_parts) >= 2 else ""
    raw_s2 = slug_parts[1] if len(slug_parts) >= 2 else ""
    s2_clean = re.split(r'-(?:\d+(?:st|nd|rd|th)|match|t20|odi|test|qualifier|eliminator|final|semi)', raw_s2, maxsplit=1)[0]
    s2 = s2_clean.replace("-", " ").strip()

    for page_url in (
        f"https://m.cricbuzz.com/cricket-match-facts/{mid}/{slug}",
        f"https://m.cricbuzz.com/live-cricket-scores/{mid}/{slug}",
    ):
        try:
            f_html = _fetch_html(page_url, timeout=4.0)
            candidates_txt = []
            toss_m = re.search(r'<div[^>]*>\s*Toss\s*</div>\s*<div[^>]*>(.*?)</div>', f_html, re.DOTALL | re.IGNORECASE)
            if toss_m:
                candidates_txt.append(re.sub(r'<[^>]+>', ' ', toss_m.group(1)).strip())
            # Fallback: scan stripped text / JSON status on live-cricket-scores page
            stripped = re.sub(r'<[^>]+>', ' ', f_html)
            candidates_txt.append(re.sub(r'\s+', ' ', stripped))

            for toss_text in candidates_txt:
                p_win = _TOSS_STATUS_RE.search(toss_text)
                if not p_win:
                    p_win = re.search(r'([A-Za-z0-9\s\.\-&]{2,40}?)\s+won\s+(?:the\s+)?toss.*?(bat|bowl|field)', toss_text, re.IGNORECASE)
                if p_win:
                    w = p_win.group(1).strip()
                    dec = _normalize_decision(p_win.group(2))
                    return {
                        "match_id": mid,
                        "team_a": s1,
                        "team_b": s2,
                        "winner": w,
                        "decision": dec,
                        "raw_text": toss_text[:200],
                        "source": "cricbuzz_facts",
                    }
        except Exception:
            continue
    return None


def fetch_cricbuzz_toss_outcomes(force_refresh: bool = False) -> List[Dict[str, Any]]:
    """Scrape live & recent toss outcomes across Cricbuzz live-scores and recent-scores."""
    global _CACHE
    now = time.time()
    if not force_refresh and (now - _CACHE["timestamp"]) < CACHE_TTL and _CACHE["tosses"]:
        return _CACHE["tosses"]

    pages_html: List[str] = []
    for url in (
        "https://m.cricbuzz.com/cricket-match/live-scores",
        "https://m.cricbuzz.com/cricket-match/recent-scores",
    ):
        try:
            pages_html.append(_fetch_html(url, timeout=6.0))
        except Exception:
            continue

    if not pages_html:
        return _CACHE["tosses"]

    tosses: List[Dict[str, Any]] = []
    seen = set()
    seen_ids = set()
    uniq_links = []

    for html_text in pages_html:
        # 1. Schema.org ld+json
        for s in re.findall(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html_text, re.DOTALL):
            try:
                d = json.loads(s)
                for it in d.get("mainEntity", {}).get("itemListElement", []):
                    status = it.get("eventStatus", "")
                    comps = [c.get("name", "") for c in it.get("competitor", [])]
                    if len(comps) >= 2 and status:
                        t1, t2 = comps[0], comps[1]
                        m_opt = _TOSS_STATUS_RE.search(status)
                        if m_opt:
                            w_raw = m_opt.group(1).strip()
                            dec = _normalize_decision(m_opt.group(2))
                            key = f"{t1.lower()}_{t2.lower()}"
                            if key not in seen:
                                seen.add(key)
                                tosses.append({
                                    "team_a": t1,
                                    "team_b": t2,
                                    "winner": w_raw,
                                    "decision": dec,
                                    "raw_text": status,
                                    "source": "cricbuzz_ldjson",
                                })
            except Exception:
                pass

        # 2. Match links for concurrent facts-page scraping
        for _, mid, slug in re.findall(r'href="(/(?:live-cricket-scores|cricket-scores)/(\d+)/([a-z0-9\-]+))"', html_text):
            if mid not in seen_ids:
                seen_ids.add(mid)
                uniq_links.append((mid, slug))

    if uniq_links:
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(_parse_facts_page, mid, slug) for mid, slug in uniq_links[:20]]
            for fut in as_completed(futures):
                res = fut.result()
                if not res:
                    continue
                key = f"{res['team_a'].lower()}_{res['team_b'].lower()}_{res['match_id']}"
                if key not in seen:
                    seen.add(key)
                    tosses.append(res)

    if tosses:
        _CACHE["timestamp"] = now
        _CACHE["tosses"] = tosses
    return tosses
