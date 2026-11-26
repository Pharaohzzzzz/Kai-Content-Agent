"""Collect public discovery metadata without requesting article bodies."""
import json
import re
import unicodedata
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from time import perf_counter
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit
from urllib.request import Request, urlopen


SHANGHAI = timezone(timedelta(hours=8), "Asia/Shanghai")
HN = "https://hacker-news.firebaseio.com/v0"
USER_AGENT = "KaiContentAgent/1.0 (public research metadata)"
TRACKING = {"fbclid", "gclid", "igshid", "mc_cid", "mc_eid"}


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def required_text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be non-empty text")
    return value.strip()


def checked_url(value):
    value = required_text(value, "URL")
    parts = urlsplit(value)
    if (
        parts.scheme not in {"http", "https"} or not parts.hostname
        or parts.username is not None or parts.password is not None
        or any(char.isspace() or ord(char) < 32 for char in value)
    ):
        raise ValueError(f"Expected a public HTTP(S) URL: {value}")
    return value


def url_key(value):
    url = urlsplit(checked_url(value))
    query = sorted(
        (key, val) for key, val in parse_qsl(url.query, keep_blank_values=True)
        if not (key.lower().startswith("utm_") or key.lower() in TRACKING)
    )
    return urlunsplit(("", url.netloc.lower(), url.path.rstrip("/") or "/", urlencode(query), ""))


def title_key(value):
    return "".join(char for char in unicodedata.normalize("NFKC", value).casefold() if char.isalnum())


def aihot_link(value):
    host = urlsplit(value).hostname or ""
    return any(host == domain or host.endswith("." + domain) for domain in ("aihot.news", "aihot.virxact.com"))


def publication(value):
    if value is None or value == "":
        return None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return date.fromisoformat(value).isoformat()
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        stamp = parsedate_to_datetime(value)
    return stamp.isoformat() if stamp.tzinfo is not None else None


def within_window(value, start, end):
    if len(value) == 10:
        # Publisher gives only a calendar date. Keep overlapping local dates.
        return start.date() <= date.fromisoformat(value) <= end.date()
    stamp = datetime.fromisoformat(value)
    return start <= stamp <= end


class TextOnly(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden += 1
        elif tag in {"p", "div", "br", "li"}:
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def plain_text(value):
    parser = TextOnly()
    parser.feed(value or "")
    return " ".join("".join(parser.parts).split())


def brief_text(value):
    result = plain_text(value)
    return result if len(result) <= 280 else result[:279].rstrip() + "…"


def fetch(url):
    request = Request(checked_url(url), headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=8) as response:
        limit = 6 * 1024 * 1024
        data = response.read(limit + 1)
        if len(data) > limit:
            raise ValueError("Public feed exceeded the 6 MiB response limit")
        return data


def rss_entries(url):
    root = ET.fromstring(fetch(url))
    entries = root.findall(".//{*}item") or root.findall("{*}entry")
    if root.tag.rsplit("}", 1)[-1] not in {"rss", "feed", "RDF"}:
        raise ValueError("Response is not RSS or Atom")
    for entry in entries:
        link = None
        for element in entry.findall("{*}link"):
            if element.get("rel", "alternate") == "alternate":
                link = element.get("href") or element.text
                if link:
                    break
        if not link:
            continue
        title = plain_text(entry.findtext("{*}title"))
        if not title:
            continue
        raw_date = entry.findtext("{*}pubDate") or entry.findtext("{*}published") or entry.findtext("{*}date")
        try:
            published_at = publication(raw_date.strip() if raw_date else None)
        except (TypeError, ValueError, OverflowError):
            published_at = None
        summary = entry.findtext("{*}description") or entry.findtext("{*}summary")
        try:
            link = checked_url(urljoin(url, link.strip()))
        except ValueError:
            continue
        yield dict(title=title, url=link, published_at=published_at, summary=brief_text(summary))


def hn_entries(url):
    ids = json.loads(fetch(url))
    if not isinstance(ids, list) or not all(type(item) is int and item > 0 for item in ids[:10]):
        raise ValueError("Hacker News API returned an invalid top story list")
    rows, errors = [], []

    def load_item(item_id):
        item = json.loads(fetch(f"{HN}/item/{item_id}.json"))
        if not item or item.get("type") != "story" or item.get("dead") or item.get("deleted"):
            return None
        linked_url = checked_url(item["url"]) if item.get("url") else None
        return dict(
            title=plain_text(item["title"]), url=f"https://news.ycombinator.com/item?id={item_id}",
            linked_url=linked_url, published_at=datetime.fromtimestamp(item["time"], timezone.utc).isoformat(),
            summary="", engagement={"points": item.get("score"), "comments": item.get("descendants")},
            publication_scope="hn_submission",
        )

    with ThreadPoolExecutor(max_workers=4) as executor:
        tasks = [executor.submit(load_item, item_id) for item_id in ids[:10]]
        for item_id, task in zip(ids, tasks):
            try:
                row = task.result()
                if row and row["title"]:
                    rows.append(row)
            except (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError) as error:
                errors.append(f"item {item_id}: {error}")
    return rows, errors


def collect_source(source, offline):
    now = datetime.now(SHANGHAI).isoformat(timespec="seconds")
    status = dict(source, collected_at=now, count=0, errors=[])
    if source.get("enabled") is False or source.get("authorization_status") == "pending":
        return dict(status, status="disabled"), []
    if source["access_status"] == "unavailable":
        return dict(status, status="unavailable", errors=[source["access_note"]]), []
    if offline:
        return dict(status, status="offline"), []
    started = perf_counter()
    try:
        if source["method"] == "rss":
            rows, errors = list(rss_entries(source["url"])), []
        elif source["method"] == "hn_api":
            rows, errors = hn_entries(source["url"])
        else:
            raise ValueError(f"Unsupported collection method: {source['method']}")
        status.update(status="partial" if rows and errors else "failed" if errors else "ok", errors=errors)
    except (OSError, ValueError, ET.ParseError) as error:
        rows = []
        status.update(status="failed", errors=[f"{type(error).__name__}: {error}"])
    status.update(count=len(rows), seconds=round(perf_counter() - started, 3))
    rows = [
        dict(row, source_id=source["source_id"], source=source["name"], category=source["category"],
             collected_at=now, access="metadata_only", first_hand=source["first_hand"])
        for row in rows
    ]
    return status, rows


def manual_rows(path):
    rows = []
    inputs = read_json(path)
    if not isinstance(inputs, list):
        raise ValueError("Manual input must be a JSON array")
    for raw in inputs:
        url = checked_url(raw["url"])
        source = required_text(raw["source"], "source")
        if aihot_link(url) or title_key(source) == "aihot" or raw.get("source_id") == "aihot":
            raise ValueError("AIHOT material is disabled pending written authorization")
        access = raw.get("access", "link_only")
        supplied_by = raw.get("supplied_by", "user")
        if access not in {"opened", "link_only"} or supplied_by not in {"user", "codex"}:
            raise ValueError("Invalid manual access or supplied_by value")
        first_hand = raw.get("first_hand", False)
        if type(first_hand) is not bool:
            raise ValueError("Manual first_hand must be a boolean")
        pub = publication(raw.get("published_at"))
        rows.append(dict(
            title=required_text(raw["title"], "title"), url=url, published_at=pub,
            source=source, source_id=f"manual:{source}", category=required_text(raw["category"], "category"),
            summary=brief_text(raw.get("summary")), collected_at=datetime.now(SHANGHAI).isoformat(timespec="seconds"),
            first_hand=first_hand, access=access, supplied_by=supplied_by,
        ))
    return rows


def discovery(rows, start, end):
    items, by_url, by_title, filtered = {}, {}, {}, 0
    duplicates = []
    # Prefer source material to community submissions, and read manual evidence to feed metadata.
    ordered = sorted(rows, key=lambda row: (not row["first_hand"], row["access"] != "opened"))
    for index, row in enumerate(ordered):
        pub = row["published_at"]
        if pub is not None and not within_window(pub, start, end):
            filtered += 1
            continue
        if aihot_link(row["url"]) or (row.get("linked_url") and aihot_link(row["linked_url"])):
            continue
        urls = [url_key(row["url"])]
        if row.get("linked_url"):
            urls.append(url_key(row["linked_url"]))
        title = title_key(row["title"])
        hits = [by_url[key] for key in urls if key in by_url]
        if title and title in by_title:
            hits.append(by_title[title])
        group = min(hits) if hits else index
        if not hits:
            items[group] = dict(row, also_seen=[])
        else:
            hit = items[group]
            hit["also_seen"].append(row)
            duplicates.append({"kept_url": hit["url"], "duplicate_url": row["url"], "source": row["source"]})
            if hit["published_at"] is None and pub and "publication_scope" not in row and urls[0] == url_key(hit["url"]):
                hit["published_at"] = pub
            for other in set(hits) - {group}:
                merged = items.pop(other)
                hit["also_seen"].extend([dict(merged, also_seen=[]), *merged["also_seen"]])
                duplicates.append({"kept_url": hit["url"], "duplicate_url": merged["url"], "source": merged["source"]})
                for lookup in (by_url, by_title):
                    for key, value in lookup.items():
                        if value == other:
                            lookup[key] = group
        for key in urls:
            by_url[key] = group
        if title:
            by_title[title] = group
    for item in items.values():
        item["source_count"] = len({item["source"], *(row["source"] for row in item["also_seen"])})

    def discovery_key(item):
        pub = item["published_at"]
        moment = datetime.fromisoformat(pub) if pub else None
        if moment and moment.tzinfo is None:
            moment = moment.replace(tzinfo=SHANGHAI)
        points = max((row.get("engagement", {}).get("points") or 0 for row in [item, *item["also_seen"]]))
        return pub is not None, item["source_count"], points, moment.timestamp() if moment else 0

    ranked = sorted(
        items.values(), key=discovery_key,
        reverse=True,
    )
    return ranked, duplicates, filtered
