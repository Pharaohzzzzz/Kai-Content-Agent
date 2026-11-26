"""Local publication records and per-work cumulative metric snapshots."""

import csv
import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from research import HISTORY, ROOT, WORKSPACE, atomic_write, history_lock, read_history, write_history
from research_collect import SHANGHAI, checked_url, required_text, url_key
from research_topics import TOPIC_STATES


PLATFORMS = ("douyin", "xiaohongshu")
FORMATS = ("video", "note")
COUNTS = ("view_count", "likes", "comments", "saves", "shares", "new_followers",
          "impressions", "qualified_inquiries")
MONEY = ("revenue_cny", "additional_cost_cny")
METRICS = (*COUNTS, "completion_rate", "avg_watch_seconds", *MONEY)
METADATA = ("publication_id", "collected_at", "view_metric", "metric_scope")
CSV_FIELDS = ("publication_id", "collected_at", "view_metric", "view_count",
              "likes", "comments", "saves", "shares", "new_followers", "completion_rate",
              "avg_watch_seconds", "impressions", "qualified_inquiries", *MONEY, "metric_scope")


def timestamp(value, label):
    try:
        stamp = datetime.fromisoformat(required_text(value, label))
        if stamp.utcoffset() is None:
            raise ValueError()
    except ValueError as error:
        raise ValueError(f"{label} requires ISO 8601 with a timezone offset") from error
    return stamp.astimezone(timezone.utc)


def optional_text(value, label):
    return None if value is None or isinstance(value, str) and not value.strip() else required_text(value, label)


def number(value, label, *, integer=False, money=False, rate=False):
    if value is None or isinstance(value, str) and not value.strip():
        return None
    try:
        raw = str(value).strip()
        percent = rate and raw.endswith("%")
        parsed = Decimal(raw[:-1] if percent else raw)
        ceiling = 10**12 if money else 10**15
        if not parsed.is_finite() or not 0 <= parsed <= ceiling:
            raise InvalidOperation()
        if percent:
            parsed /= 100
        if (rate and parsed > 1 or integer and parsed != parsed.to_integral_value()
                or money and parsed != parsed.quantize(Decimal("0.01"))):
            raise InvalidOperation()
    except InvalidOperation as error:
        unit = "a fraction 0–1 or a percentage" if rate else "a non-negative number"
        raise ValueError(f"{label} must be {unit} with the expected precision") from error
    return int(parsed) if integer else float(parsed)


def read_jsonl(path):
    rows = []
    with path.open(encoding="utf-8-sig") as source:
        for index, line in enumerate(source, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError("expected an object")
            except ValueError as error:
                raise ValueError(f"{path.name}, line {index}: {error}") from error
            rows.append((index, row))
    return rows


def write_jsonl(path, records):
    atomic_write(path, "".join(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n"
                               for row in records))


class Operations:
    def __init__(self, simulate):
        self.mode = "simulated" if simulate else "real"
        self.directory = WORKSPACE / ("p8-simulation" if simulate else "p8-operations")
        self.standalone = self.directory / "publications.jsonl"
        self.snapshots = self.directory / "snapshots.jsonl"
        self.history = self.directory / "research-history.jsonl" if simulate else HISTORY
        self.lock = history_lock(self.history)

    def load(self, path):
        rows = [row for _, row in read_jsonl(path)] if path.exists() else []
        if any(row.get("data_mode") != self.mode for row in rows):
            raise ValueError(f"{path.name} contains a record from the wrong data mode")
        return rows

    def publications(self):
        history = read_history(self.history)
        standalone = self.load(self.standalone)
        by_id = {row["publication_id"]: (row, None) for row in standalone}
        for topic in history:
            for row in topic.get("publications", []):
                if row.get("data_mode") != self.mode:
                    raise ValueError("Research history contains a publication from the wrong data mode")
                by_id[row["publication_id"]] = (row, topic)
        return history, standalone, by_id

    def save(self, history, standalone, topic):
        if topic is not None:
            write_history(history, self.history)
        else:
            self.directory.mkdir(parents=True, exist_ok=True)
            write_jsonl(self.standalone, standalone)


def candidates(args, operations):
    path = args.manifest.resolve()
    base = ROOT / "output" if operations.mode == "real" else operations.directory
    if not path.is_relative_to(base) or (operations.mode == "real" and path.name != "publish_manifest.json"):
        raise ValueError(f"Use a {operations.mode} publication manifest under {base}")
    raw = path.read_bytes()
    manifest = json.loads(raw.decode("utf-8-sig"))
    if manifest.get("data_mode", "real") != operations.mode:
        raise ValueError("Publication manifest has the wrong data mode")
    if manifest.get("schema_version") != 1 or manifest.get("status") != "awaiting_human_review":
        raise ValueError("Use a complete P7 package marked awaiting_human_review")
    manifest_sha = hashlib.sha256(raw).hexdigest()
    source = manifest["source"]
    if operations.mode == "real" and source.get("synthetic_test"):
        raise ValueError("Synthetic test material cannot enter the real publication ledger")
    lane = source["lane"]
    topic_id = optional_text(source.get("topic_id"), "topic_id")
    clip_id = optional_text(source.get("clip_id"), "clip_id")
    if not (lane == "A" and clip_id or lane == "B" and topic_id):
        raise ValueError("Manifest must identify the A-line clip or B-line topic")
    cost = number(args.production_cost_cny, "production_cost_cny", money=True)
    minutes = number(args.manual_minutes, "manual_minutes")
    records = []
    for platform in PLATFORMS:
        if args.platform and platform != args.platform:
            continue
        package = manifest["platforms"].get(platform, {})
        cards = package.get("cards", {})
        artifacts = {"video": [package["files"]["video"]]} if package.get("files", {}).get("video") else {}
        if cards.get("status") == "packaged" and cards.get("files"):
            artifacts["note"] = cards["files"]
        for form, files in artifacts.items():
            if args.format and form != args.format:
                continue
            duration = number(source["media"]["duration_seconds"], "duration_seconds") if form == "video" else None
            if form == "video" and not duration:
                raise ValueError("Video duration must be positive")
            prefix = "pub" if operations.mode == "real" else "sim"
            records.append({
                "schema_version": 1, "data_mode": operations.mode,
                "publication_id": f"{prefix}-{platform}-{form}-{manifest_sha[:16]}",
                "status": "pending", "topic_id": topic_id, "clip_id": clip_id,
                "lane": lane, "category": required_text(args.category, "category"),
                "render_manifest": source["render"], "publish_manifest": {
                    "path": path.relative_to(ROOT).as_posix(), "sha256": manifest_sha,
                }, "artifacts": [{key: file[key] for key in ("path", "sha256")} for file in files],
                "platform": platform, "format": form, "duration_seconds": duration,
                "manual_minutes": minutes, "production_cost_cny": cost,
                "title": None, "published_at": None, "work_id": None, "url": None, "confirmed_at": None,
            })
    if not records:
        raise ValueError("The package does not contain the requested platform or format")
    if len(records) > 1 and (minutes is not None or cost is not None):
        raise ValueError("Allocate time and cost separately using --platform and --format for each publication")
    return records


def fill_production(record, values):
    for key in ("manual_minutes", "production_cost_cny"):
        value = values.get(key)
        if value is None:
            continue
        if record[key] is not None and record[key] != value:
            raise ValueError(f"{key} is already recorded for {record['publication_id']}")
        record[key] = value


def register(args, operations):
    records = candidates(args, operations)
    history, standalone, by_id = operations.publications()
    topic_id = records[0]["topic_id"]
    lane = records[0]["lane"]
    topic = next((row for row in history if topic_id and row["topic_id"] == topic_id), None)
    if operations.mode == "real" and lane == "B" and topic is None:
        raise ValueError(f"Unknown B-line topic in P5 history: {topic_id}")
    if topic is not None and topic["line"] != lane:
        raise ValueError("Publication lane differs from the P5 topic lane")
    target = topic.setdefault("publications", []) if topic else standalone
    result = []
    changed = False
    for row in records:
        identity = row["publication_id"]
        if identity in by_id:
            prior, owner = by_id[identity]
            if (owner is not topic or prior["category"] != row["category"]
                    or prior["publish_manifest"]["sha256"] != row["publish_manifest"]["sha256"]):
                raise ValueError(f"Publication already registered with different metadata: {identity}")
            previous = prior.copy()
            fill_production(prior, row)
            changed |= prior != previous
            result.append(prior)
        else:
            target.append(row)
            result.append(row)
            changed = True
    if changed:
        operations.save(history, standalone, topic)
    return result


def confirm(args, operations):
    if not args.confirmed_published:
        raise ValueError("Explicit user confirmation of actual publication is required: --confirmed-published")
    history, standalone, by_id = operations.publications()
    if args.publication_id not in by_id:
        raise ValueError(f"Unknown {operations.mode} publication: {args.publication_id}")
    row, topic = by_id[args.publication_id]
    stamp = timestamp(args.published_at, "published_at")
    now = datetime.now(timezone.utc)
    if operations.mode == "real" and stamp > now:
        raise ValueError("Actual publication time cannot be in the future")
    work_id = optional_text(args.work_id, "work_id")
    url = checked_url(args.url) if args.url is not None else None
    if work_id is None and url is None:
        raise ValueError("Provide a platform work ID or URL")
    for prior, _ in by_id.values():
        if prior is row or prior["platform"] != row["platform"] or prior["status"] != "published":
            continue
        if (work_id and prior["work_id"] == work_id
                or url and prior["url"] and url_key(url) == url_key(prior["url"])):
            raise ValueError(f"Platform work already recorded as {prior['publication_id']}")
    details = {
        "title": required_text(args.title, "actual title"),
        "published_at": stamp.isoformat(), "work_id": work_id, "url": url,
    }
    previous = row.copy()
    if row["status"] == "published":
        for key, value in details.items():
            if value is not None and row[key] not in (None, value):
                raise ValueError(f"Confirmed publication already has a different {key}")
    if topic is not None:
        state = topic["status"]
        if state != "published" and "published" not in TOPIC_STATES[state]:
            raise ValueError(f"Record production in P5 history before confirming publication: {state}")
        if state != "published":
            topic.update(status="published", status_note=f"P8 已确认实际发布 {row['publication_id']}",
                         status_at=now.astimezone(SHANGHAI).isoformat(timespec="seconds"))
    row.update({key: value for key, value in details.items() if value is not None})
    row.update(status="published", confirmed_at=row["confirmed_at"] or now.isoformat(timespec="seconds"))
    fill_production(row, {
        "manual_minutes": number(args.manual_minutes, "manual_minutes"),
        "production_cost_cny": number(args.production_cost_cny, "production_cost_cny", money=True),
    })
    if row != previous:
        operations.save(history, standalone, topic)
    return row


def input_rows(path):
    if path.suffix.lower() == ".jsonl":
        yield from read_jsonl(path)
    elif path.suffix.lower() == ".csv":
        with path.open(encoding="utf-8-sig", newline="") as source:
            reader = csv.DictReader(source, strict=True)
            names = reader.fieldnames or []
            if len(names) != len(set(names)) or not {"publication_id", "collected_at"} <= set(names):
                raise ValueError("CSV requires unique headers including publication_id and collected_at")
            if set(names) - set(CSV_FIELDS):
                raise ValueError(f"Unknown CSV columns: {', '.join(sorted(set(names) - set(CSV_FIELDS)))}")
            for row in reader:
                yield reader.line_num, row
    else:
        raise ValueError("Use a .csv or .jsonl file")


def snapshot(raw, operations, by_id, now):
    if set(raw) - {*METADATA, *METRICS, "data_mode"}:
        raise ValueError("Unknown metrics fields or extra CSV columns")
    if raw.get("data_mode", operations.mode) != operations.mode:
        raise ValueError("Snapshot has the wrong data mode")
    identity = required_text(raw.get("publication_id"), "publication_id")
    if identity.startswith("sim-") and operations.mode == "real":
        raise ValueError("Cannot import a simulated publication into real history")
    if identity not in by_id or by_id[identity][0]["status"] != "published":
        raise ValueError(f"Snapshot requires a confirmed publication: {identity}")
    post = by_id[identity][0]
    time = timestamp(raw.get("collected_at"), "collected_at")
    if time < timestamp(post["published_at"], "published_at") or operations.mode == "real" and time > now:
        raise ValueError("Snapshot must be collected between actual publication and now")
    values = {name: number(raw.get(name), name, integer=name in COUNTS,
                           money=name in MONEY, rate=name == "completion_rate") for name in METRICS}
    if all(value is None for value in values.values()):
        raise ValueError("Supply at least one known metric")
    view_metric = optional_text(raw.get("view_metric"), "view_metric")
    if values["view_count"] is not None and view_metric is None:
        raise ValueError("Enter the platform's metric label as view_metric alongside view_count")
    if post["format"] != "video" and any(values[name] is not None for name in ("completion_rate", "avg_watch_seconds")):
        raise ValueError("Completion rate and average watch time are video metrics")
    return {
        "schema_version": 1, "data_mode": operations.mode,
        "publication_id": identity, "collected_at": time.isoformat(),
        "view_metric": view_metric,
        "metric_scope": optional_text(raw.get("metric_scope"), "metric_scope") or "作品累计",
        **values,
    }


def import_metrics(args, operations):
    _, _, by_id = operations.publications()
    rows = operations.load(operations.snapshots)
    indexed = {(row["publication_id"], row["collected_at"]): row for row in rows}
    added = 0
    unchanged = 0
    now = datetime.now(timezone.utc)
    for line, raw in input_rows(args.file):
        try:
            row = snapshot(raw, operations, by_id, now)
            key = row["publication_id"], row["collected_at"]
            if key in indexed:
                if indexed[key] != row:
                    raise ValueError(f"Conflicting snapshot for {key[0]} at {key[1]}")
                unchanged += 1
                continue
            indexed[key] = row
            added += 1
        except (ValueError, TypeError) as error:
            raise ValueError(f"{args.file.name}, line {line}: {error}") from error
    if added:
        operations.directory.mkdir(parents=True, exist_ok=True)
        write_jsonl(operations.snapshots, indexed.values())
    return {"data_mode": operations.mode, "imported": added, "unchanged": unchanged}
