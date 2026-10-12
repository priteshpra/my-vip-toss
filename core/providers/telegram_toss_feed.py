"""Telegram Live Toss Results Harvester from public channel @KINGTOSSLINE111.

Fetches real-time toss outcomes published publicly on https://t.me/s/KINGTOSSLINE111
and matches them against pending/completed matches to automatically resolve toss winners.
"""

import re
import urllib.request
from typing import List, Dict, Any, Optional

CHANNEL_URL = "https://t.me/s/KINGTOSSLINE111"


def fetch_telegram_toss_feed(pages: int = 3) -> List[Dict[str, str]]:
    """Fetch and parse live toss winners from @KINGTOSSLINE111 web preview across multiple pages."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9"
    }

    all_html = []
    current_url = CHANNEL_URL

    for _ in range(max(1, pages)):
        try:
            req = urllib.request.Request(current_url, headers=headers)
            with urllib.request.urlopen(req, timeout=10) as resp:
                h = resp.read().decode("utf-8", errors="ignore")
            all_html.append(h)

            posts = re.findall(r'data-post="KINGTOSSLINE111/(\d+)"', h)
            if posts:
                min_id = posts[0]
                current_url = f"{CHANNEL_URL}?before={min_id}"
            else:
                break
        except Exception as e:
            print(f"Error fetching Telegram feed page: {e}")
            break

    pattern = re.compile(
        r'([A-Za-z0-9\s\-\&]+?)\s+WON\s+THE\s+TOSS\s+AND\s+DECIDED\s+TO\s+(BAT|BOWL)',
        re.IGNORECASE
    )

    results = []
    seen = set()

    for h in all_html:
        messages = re.findall(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', h, re.DOTALL)
        for m in messages:
            # Strip html tags
            clean = re.sub(r'<[^>]+>', ' ', m)
            # Strip non-alphanumeric except hyphen, ampersand, space
            clean = re.sub(r'[^\w\s\-\&]', '', clean).strip()
            clean = re.sub(r'\s+', ' ', clean)

            match = pattern.search(clean)
            if match:
                raw_winner = match.group(1).strip()
                decision = match.group(2).strip().capitalize()
                # Clean winner name flags / icons
                winner_clean = re.sub(r'^(🇮🇳|🇱🇰|🇿🇲|📺|🇦🇺|🇲🇳|🇹🇭|🇰🇼)\s*', '', raw_winner).strip()
                key = f"{winner_clean.lower()}_{decision.lower()}"
                if key not in seen:
                    seen.add(key)
                    results.append({
                        "winner_raw": winner_clean,
                        "decision": decision
                    })

    return results


def match_team_with_tg_winner(team_name: str, tg_winner: str) -> bool:
    """Strict and token match team name from GamingHelper with Telegram winner text.
    
    Prevents false matches across age groups (U19/U23/Senior), genders, and distinctions.
    """
    t_clean = team_name.strip().lower()
    w_clean = tg_winner.strip().lower()

    if t_clean == w_clean:
        return True

    # 1. Gender check
    is_t_wmn = bool(re.search(r'\b(women|wmn|w)\b', t_clean))
    is_w_wmn = bool(re.search(r'\b(women|wmn|w)\b', w_clean))
    if is_t_wmn != is_w_wmn:
        return False

    # 2. 'A' team check vs Champion / Legends / Masters
    is_t_a = bool(re.search(r'\b(a)\b', t_clean))
    is_w_a = bool(re.search(r'\b(a)\b', w_clean))
    if is_t_a != is_w_a and any(k in w_clean for k in ("champion", "legend", "master")):
        return False

    # Stop words
    stop = {"under-19s", "under-19", "u19", "under-23s", "under-23", "u23", "women", "wmn", "cricket", "club", "cc", "vs", "the"}
    t_toks = {w for w in re.split(r'[\s\-_]+', t_clean) if w and w not in stop and len(w) > 2}
    w_toks = {w for w in re.split(r'[\s\-_]+', w_clean) if w and w not in stop and len(w) > 2}

    if not t_toks or not w_toks:
        return False

    diff = (t_toks ^ w_toks)
    distinguishers = {
        "bank", "television", "kingsmen", "warriors", "riders", "strikers",
        "knights", "association", "corporation", "limited", "telecom",
        "champion", "champions", "legend", "legends", "master", "masters",
        "reliance", "aseb", "blasters", "panthers", "dragons", "wanderers"
    }
    if any(d in distinguishers for d in diff):
        return False

    inter = t_toks & w_toks
    return (inter == t_toks or inter == w_toks)
