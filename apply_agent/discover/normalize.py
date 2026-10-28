"""HTML → text, date parsing, location classification."""
from __future__ import annotations

import html
import re
from datetime import datetime, timedelta, timezone

from bs4 import BeautifulSoup


def html_to_text(s: str | None) -> str:
    if not s:
        return ""
    s = html.unescape(s)          # Greenhouse double-encodes: &lt;p&gt; → <p>
    if "<" in s:
        soup = BeautifulSoup(s, "html.parser")
        for br in soup.find_all(["br", "p", "li", "div", "h1", "h2", "h3", "h4", "tr"]):
            br.append("\n")
        s = soup.get_text()
    s = html.unescape(s)
    s = re.sub(r"[ \t\xa0]+", " ", s)
    s = re.sub(r"\n\s*\n+", "\n\n", s)
    return s.strip()


def parse_date(value, *, now: datetime | None = None) -> datetime | None:
    """Accepts epoch ms, epoch s, ISO strings, or Workday text like 'Posted 3 Days Ago'."""
    now = now or datetime.now(timezone.utc)
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        secs = value / 1000 if value > 1e11 else value
        return datetime.fromtimestamp(secs, timezone.utc)
    s = str(value).strip()
    m = re.match(r"posted\s+(today|yesterday|(\d+)\+?\s+days?\s+ago)", s, re.I)
    if m:
        if m.group(1).lower() == "today":
            days = 0
        elif m.group(1).lower() == "yesterday":
            days = 1
        else:
            days = int(m.group(2))
        d = now - timedelta(days=days)
        return d.replace(hour=0, minute=0, second=0, microsecond=0)
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    d = d.astimezone(timezone.utc)
    return min(d, now)  # never in the future


BAY_AREA = [
    "san francisco", "south san francisco", "oakland", "berkeley", "emeryville", "alameda", "san mateo",
    "foster city", "burlingame", "san bruno", "redwood city", "redwood shores", "menlo park", "palo alto",
    "stanford", "mountain view", "los altos", "sunnyvale", "santa clara", "cupertino", "san jose",
    "milpitas", "fremont", "newark, ca", "pleasanton", "dublin, ca", "walnut creek", "bay area", "san carlos",
    "belmont", "brisbane, ca", "millbrae", "campbell", "los gatos", "san ramon", "hayward",
]
_SF = re.compile(r"\bsf\b|\bsfo\b|\bsf bay\b", re.I)
_US = re.compile(r"\b(us|u\.s\.?|usa|united states|north america|americas)\b", re.I)
_NON_US = re.compile(
    r"\b(canada|uk|united kingdom|london|ireland|dublin(?!,\s*ca)|germany|berlin|india|bangalore|bengaluru|"
    r"hyderabad|singapore|australia|sydney|japan|tokyo|brazil|mexico|france|paris|netherlands|amsterdam|"
    r"spain|poland|israel|toronto|vancouver|emea|apac|latam|europe)\b", re.I)
_US_STATES = re.compile(
    r",\s*(AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|MT|NE|NV|NH|NJ|NM|NY|"
    r"NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY|DC)\b|"
    r"\b(california|new york|washington|texas|massachusetts|illinois|colorado|oregon|georgia|virginia|"
    r"new jersey|pennsylvania|north carolina|arizona|utah|florida|michigan|minnesota)\b")


def is_bay_area(loc: str) -> bool:
    l = loc.lower()
    return any(c in l for c in BAY_AREA) or bool(_SF.search(loc))


def is_remote_us(loc: str) -> bool:
    l = loc.lower()
    if "remote" not in l and "anywhere" not in l:
        return False
    if _NON_US.search(loc):
        return bool(_US.search(loc))
    return True


def is_us(loc: str) -> bool:
    if _NON_US.search(loc) and not _US.search(loc):
        return False
    return bool(_US.search(loc) or _US_STATES.search(loc) or is_bay_area(loc) or is_remote_us(loc))
