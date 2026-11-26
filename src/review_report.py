"""Deterministic P8 calculations and Markdown/JSON reports."""

import hashlib
import json
import re
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from statistics import median

from research import atomic_write, json_text
from research_collect import SHANGHAI, read_json, required_text
from review_data import METRICS, timestamp


WINDOWS = {24: 6, 72: 12, 168: 24}
RATES = {"like_rate": "likes", "save_rate": "saves", "share_rate": "shares"}
COMPARABLE = {
    "view_count": "播放／浏览", "new_followers_per_10k": "万次新增粉",
    "like_rate": "点赞率", "save_rate": "收藏率", "share_rate": "分享率",
    "completion_rate": "完播率", "avg_watch_seconds": "平均观看秒",
    "manual_minutes": "人工分钟", "production_cost_cny": "制作成本",
    "qualified_inquiries": "有效咨询", "revenue_cny": "收入", "total_cost_cny": "总成本", "net_cny": "净收益",
}


def publication_hours(post, snapshot):
    collected = timestamp(snapshot["collected_at"], "collected_at")
    published = timestamp(post["published_at"], "published_at")
    return (collected - published).total_seconds() / 3600


def calculated(post, snapshot):
    def ratio(numerator, factor=1):
        views = snapshot["view_count"]
        value = snapshot[numerator]
        return value * factor / views if views and value is not None else None

    production = post["production_cost_cny"]
    additional = snapshot["additional_cost_cny"]
    revenue = snapshot["revenue_cny"]
    total = Decimal(str(production)) + Decimal(str(additional)) if production is not None and additional is not None else None
    net = Decimal(str(revenue)) - total if total is not None and revenue is not None else None
    return {
        "publication_id": post["publication_id"], "platform": post["platform"], "format": post["format"],
        "lane": post["lane"], "category": post["category"], "title": post["title"],
        "duration_seconds": post["duration_seconds"],
        "collected_at": timestamp(snapshot["collected_at"], "collected_at").astimezone(SHANGHAI).isoformat(),
        "hours_after_publish": round(publication_hours(post, snapshot), 6),
        "view_metric": snapshot["view_metric"], "metric_scope": snapshot["metric_scope"],
        **{key: snapshot[key] for key in METRICS},
        "manual_minutes": post["manual_minutes"], "production_cost_cny": production,
        "new_followers_per_10k": ratio("new_followers", 10000),
        **{key: ratio(field) for key, field in RATES.items()},
        "total_cost_cny": float(total) if total is not None else None,
        "net_cny": float(net) if net is not None else None,
    }


def medians(rows):
    result = {}
    for key in COMPARABLE:
        values = [row[key] for row in rows if row[key] is not None]
        result[key] = {"n": len(values), "value": median(values) if values else None}
    return result


def comparisons(posts, snapshots):
    grouped = defaultdict(list)
    for post in posts:
        observations = [(publication_hours(post, row), row) for row in snapshots[post["publication_id"]]]
        for window, tolerance in WINDOWS.items():
            options = [(hours, row) for hours, row in observations if abs(hours - window) <= tolerance]
            if not options:
                continue
            _, chosen = min(options, key=lambda item: (abs(item[0] - window), item[0]))
            key = post["platform"], post["format"], window, chosen["view_metric"] or "", chosen["metric_scope"]
            grouped[key].append(calculated(post, chosen))
    result = []
    for (platform, form, window, label, scope), rows in sorted(grouped.items()):
        by_category = defaultdict(list)
        for row in rows:
            by_category[row["category"]].append(row)
        hours = [row["hours_after_publish"] for row in rows]
        result.append({
            "platform": platform, "format": form, "window_hours": window, "tolerance_hours": WINDOWS[window],
            "view_metric": label or None, "metric_scope": scope, "actual_hours_min": min(hours),
            "actual_hours_max": max(hours), "sample_works": len(rows), "medians": medians(rows),
            "categories": [{"category": category, "sample_works": len(works), "medians": medians(works)}
                           for category, works in sorted(by_category.items())],
            "works": rows,
        })
    return result


def review_period(args):
    now = datetime.now(SHANGHAI)
    as_of = timestamp(args.as_of, "as_of").astimezone(SHANGHAI) if args.as_of else now.replace(microsecond=0)
    if (args.start is None) != (args.through is None):
        raise ValueError("Provide both --start and --through, or use this week")
    if args.start is not None:
        start, through = date.fromisoformat(args.start), date.fromisoformat(args.through)
    else:
        start = as_of.date() - timedelta(days=as_of.weekday())
        through = start + timedelta(days=6)
    if start > through:
        raise ValueError("Start date must not be after through date")
    return start, through, as_of


def build_report(operations, start, through, as_of):
    _, _, all_records = operations.publications()
    records = [record for record, _ in all_records.values()]
    posts = []
    for row in records:
        if row["status"] != "published":
            continue
        published = timestamp(row["published_at"], "published_at").astimezone(SHANGHAI)
        if start <= published.date() <= through and published <= as_of:
            posts.append(row)
    posts.sort(key=lambda row: (row["platform"], row["format"], row["published_at"], row["publication_id"]))
    by_post = {row["publication_id"]: [] for row in posts}
    for row in operations.load(operations.snapshots):
        identity = row["publication_id"]
        if identity in by_post and timestamp(row["collected_at"], "collected_at") <= as_of:
            by_post[identity].append(row)
    latest = [calculated(post, max(by_post[post["publication_id"]],
                                   key=lambda row: timestamp(row["collected_at"], "collected_at")))
              for post in posts if by_post[post["publication_id"]]]
    facts = {
        "pending_records": sum(row["status"] == "pending" for row in records),
        "published_in_period": len(posts), "sample_works": len(latest),
        "eligible_snapshots": sum(map(len, by_post.values())),
    }
    data = {
        "schema_version": 1, "data_mode": operations.mode,
        "period": {"start": start.isoformat(), "through": through.isoformat(), "timezone": "Asia/Shanghai"},
        "facts": facts, "publications": posts,
        "observations": latest, "comparisons": comparisons(posts, by_post),
    }
    fingerprint = hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    data["period"]["as_of"] = as_of.isoformat()
    data["basis_sha256"] = fingerprint
    return data


def editor_notes(path, data):
    if path is None:
        return []
    notes = read_json(path)
    if notes["data_mode"] != data["data_mode"] or notes["basis_sha256"] != data["basis_sha256"]:
        raise ValueError("Editorial notes do not match this report's data mode and basis_sha256")
    eligible = {row["publication_id"] for row in data["observations"]}
    result = []
    for item in notes["notes"]:
        refs = item["publication_ids"]
        if not isinstance(refs, list) or not refs or not set(refs) <= eligible:
            raise ValueError("Editorial notes must reference publications with eligible observations")
        result.append({"section": required_text(item["section"], "section"),
                       "text": required_text(item["text"], "text"), "publication_ids": refs})
    return result


def display(value, key=""):
    if value is None:
        return "缺失"
    if key in (*RATES, "completion_rate"):
        return f"{value:.2%}"
    if key.endswith("_cny"):
        return f"{value:,.2f}"
    if isinstance(value, float):
        return f"{value:,.2f}".rstrip("0").rstrip(".")
    if isinstance(value, int):
        return f"{value:,}"
    return str(value).replace("|", "｜").replace("\n", " ").replace("\r", " ")


def markdown(data):
    simulated = data["data_mode"] == "simulated"
    facts = data["facts"]
    period = data["period"]
    lines = ["# Kai 运营复盘" + ("（模拟数据）" if simulated else ""), "",
             f"发布批次 {period['start']} 至 {period['through']}，北京时间。数据截至 {period['as_of']}。", "",
             "## 记录事实", "",
             f"台账待发布 {facts['pending_records']} 条。本期确认发布 {facts['published_in_period']} 条，"
             f"其中 {facts['sample_works']} 条有指标，符合时间范围的累计快照共 {facts['eligible_snapshots']} 条。"]
    sampled_ids = {row["publication_id"] for row in data["observations"]}
    unsampled = [post for post in data["publications"]
                 if post["publication_id"] not in sampled_ids]
    if not facts["sample_works"]:
        lines += ["", "暂无有效运营样本。" if not simulated else "暂无有效模拟运营样本。"]
    if unsampled:
        lines += ["", "### 尚无指标的发布记录", ""]
        for row in unsampled:
            lines.append(f"- {row['publication_id']} · {display(row['title'])} · "
                         f"人工 {display(row['manual_minutes'])} 分钟 · 制作成本 {display(row['production_cost_cny'], 'production_cost_cny')}")
    if not facts["sample_works"]:
        return "\n".join(lines) + "\n"

    lines += ["", "## 最新快照及计算结果", "",
              "每条作品显示截至复盘时间的最新一笔累计快照。工时按作品录入。"
              "总成本由制作成本及额外成本组成，净收益须同时有收入和两项成本。财务单位为人民币元。"]
    for (platform, form) in dict.fromkeys((row["platform"], row["format"]) for row in data["observations"]):
        lines += ["", f"### {platform} / {form}", "",
                  "| 作品 | 发布后小时 | 播放／浏览 | 万次新增粉 | 点赞率 | 收藏率 | 分享率 | 完播率 | 平均观看秒／片长秒 | 人工分钟 | 有效咨询 | 收入／制作成本／额外成本／总成本／净收益 |",
                  "| --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | --- | ---: | ---: | --- |"]
        for row in data["observations"]:
            if (row["platform"], row["format"]) != (platform, form):
                continue
            cells = [
                f"{display(row['title'])} · {row['publication_id']}", display(row["hours_after_publish"]),
                f"{display(row['view_count'])} · {display(row['view_metric'])}",
                *(display(row[key], key) for key in ("new_followers_per_10k", *RATES, "completion_rate")),
                f"{display(row['avg_watch_seconds'])}／{display(row['duration_seconds'])}",
                display(row["manual_minutes"]), display(row["qualified_inquiries"]),
                "／".join(display(row[key], key) for key in ("revenue_cny", "production_cost_cny",
                                                           "additional_cost_cny", "total_cost_cny", "net_cny")),
            ]
            lines.append("| " + " | ".join(cells) + " |")

    lines += ["", "## 同平台、同形式、同观察窗口比较", "",
              "24、72、168 小时分别允许偏差 6、12、24 小时。每个窗口每条作品选最近一次快照。"
              "括号内为该指标的有效作品数。"]
    if not data["comparisons"]:
        lines += ["", "现有快照未落在预设观察窗口。"]
    for group in data["comparisons"]:
        low, high = display(group["actual_hours_min"]), display(group["actual_hours_max"])
        hours = low if low == high else f"{low}–{high}"
        lines += ["", f"### {group['platform']} / {group['format']} / {group['window_hours']} 小时", "",
                  f"{display(group['view_metric'])} · {display(group['metric_scope'])} · "
                  f"实际 {hours} 小时 · {group['sample_works']} 条作品", ""]
        for category in group["categories"]:
            values = [f"{label} {display(stat['value'], key)}（{stat['n']}）"
                      for key, label in COMPARABLE.items()
                      if (stat := category["medians"][key])["n"]]
            lines.append(f"- {display(category['category'])}（{category['sample_works']} 条）"
                         + (" · " + "，".join(values) if values else " · 暂无可计算中位数"))
    lines += ["", "## 编辑判断", ""]
    if data["editorial_notes"]:
        for note in data["editorial_notes"]:
            lines += [f"- **{display(note['section'])}**　{note['text']}（依据 {'、'.join(note['publication_ids'])}）"]
    else:
        lines.append("待 Codex 按作品和观察窗口解读。")
    lines.append("少量样本只用于描述当期表现，后续验证仍需积累同口径样本。")
    return "\n".join(lines) + "\n"


def report(args, operations):
    start, through, as_of = review_period(args)
    name = args.name or f"review-{start}-to-{through}"
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", name):
        raise ValueError("Report name must use lowercase ASCII letters, digits and hyphens")
    data = build_report(operations, start, through, as_of)
    data["editorial_notes"] = editor_notes(args.editorial, data)
    dest = operations.directory / "reports" / name
    dest.mkdir(parents=True, exist_ok=True)
    atomic_write(dest / "review.json", json_text(data))
    atomic_write(dest / "review.md", markdown(data))
    return dest
