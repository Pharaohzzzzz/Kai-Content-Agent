"""Kai Content Agent P5 command line, using the Python standard library."""
import argparse
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import NamedTemporaryFile
from time import perf_counter

from research_collect import SHANGHAI, collect_source, discovery, manual_rows, read_json
from research_topics import READY, TOPIC_STATES, WEIGHTS, daily_brief, gemini_prompt, prepare_topic, rank_topics, topic_sort_key


ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT / "workspace"
HISTORY = WORKSPACE / "research-history.jsonl"


def json_text(value):
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def atomic_write(path, contents):
    pending = None
    try:
        with NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n", dir=path.parent, delete=False) as output:
            pending = Path(output.name)
            output.write(contents)
        pending.replace(path)
    finally:
        if pending is not None:
            pending.unlink(missing_ok=True)


@contextmanager
def history_lock(history_path=HISTORY):
    history_path.parent.mkdir(parents=True, exist_ok=True)
    with history_path.with_suffix(".lock").open("a+b") as handle:
        if os.name == "nt":
            import msvcrt
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # Closing the handle releases the lock, including after an interrupted process.
        yield


def read_history(history_path=HISTORY):
    if not history_path.exists():
        return []
    with history_path.open(encoding="utf-8") as source:
        return [json.loads(line) for line in source if line.strip()]


def write_history(history, history_path=HISTORY):
    atomic_write(history_path, "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in history))


def task_path(run_id):
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", run_id):
        raise ValueError("Task name must use lowercase ASCII letters, digits and hyphens")
    return WORKSPACE / run_id


def ready_topic(topic_id):
    run_id, separator, _ = topic_id.partition(":")
    if not separator:
        raise ValueError("Use the full research topic ID: task:slug")
    path = task_path(run_id) / "topics.json"
    topic = next((item for item in read_json(path)["topics"] if item["topic_id"] == topic_id), None)
    row = next((item for item in read_history() if item["topic_id"] == topic_id), None)
    if (not topic or not row or row["status"] == "rejected" or topic["production_state"] != READY
            or not topic["handoff"]["ready_to_prepare"]):
        raise ValueError(f"Topic is missing, rejected or not ready for production: {topic_id}")
    return topic, path


def collect(args):
    task = task_path(args.task)
    if task.exists():
        raise ValueError(f"Task already exists: {task}")
    if args.hours < 1:
        raise ValueError("--hours must be positive")
    if args.offline and not args.manual:
        raise ValueError("--offline requires --manual")
    started = perf_counter()
    end = datetime.now(SHANGHAI)
    start = end - timedelta(hours=args.hours)
    manual = manual_rows(args.manual) if args.manual else []
    registered = read_json(ROOT / "config/research-sources.json")
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda source: collect_source(source, args.offline), registered))
    statuses, rows = [], list(manual)
    for status, found in results:
        statuses.append(status)
        rows.extend(found)
    for source_id in sorted({item["source_id"] for item in manual}):
        imported = [item for item in manual if item["source_id"] == source_id]
        statuses.append(dict(
            source_id=source_id, name=imported[0]["source"], method="manual", status="imported",
            count=len(imported), urls=list(dict.fromkeys(item["url"] for item in imported)),
            collected_at=imported[0]["collected_at"],
            access=list(dict.fromkeys(item["access"] for item in imported)), errors=[],
        ))
    items, duplicates, filtered = discovery(rows, start, end)
    result = dict(
        schema_version=1, task=args.task, research_date=end.date().isoformat(), timezone=SHANGHAI.tzname(None),
        hours=args.hours, window_start=start.isoformat(timespec="seconds"), window_end=end.isoformat(timespec="seconds"),
        offline=args.offline, sources=statuses, items=items, duplicates=duplicates, filtered_outside_window=filtered,
        collection_seconds=round(perf_counter() - started, 3),
    )
    task.mkdir(parents=True)
    atomic_write(task / "sources.json", json_text(result))
    print(f"{task / 'sources.json'} | items={len(items)} duplicates={len(duplicates)} seconds={result['collection_seconds']:.3f}")
    for status in statuses:
        print(f"{status['source_id']}: {status['status']} ({status['count']})")
        for error in status["errors"]:
            print(f"  {error}")


def draft_topics(task, collected):
    draft = read_json(task / "research-draft.json")
    if not isinstance(draft["topics"], list):
        raise ValueError("Draft topics must be an array")
    note = draft["note"]
    if not isinstance(note, str) or not draft["topics"] and not note.strip():
        raise ValueError("An empty draft requires an editorial note")
    topics = [prepare_topic(ROOT, task.name, row, collected["research_date"]) for row in draft["topics"]]
    if len({row["topic_id"] for row in topics}) != len(topics):
        raise ValueError("Draft topic slugs must be unique")
    return topics, note.strip()


def history_row(topic, task):
    return {key: topic[key] for key in ("topic_id", "created_date", "title", "theme", "event_url", "line", "status")} | {
        "source_urls": topic["handoff"]["source_urls"], "task": task, "new_development": topic["new_development"],
    }


def finalize(args):
    started = perf_counter()
    task = task_path(args.task)
    collected = read_json(task / "sources.json")
    topics, note = draft_topics(task, collected)
    max_topics = read_json(ROOT / "config/kai-content.json")["max_candidates"]
    with history_lock():
        if (task / "topics.json").exists():
            raise ValueError("Task has already been finalized")
        history = read_history()
        # A run interrupted after saving history can safely publish the same task on retry.
        prior = [row for row in history if row["task"] != task.name]
        kept, duplicates = rank_topics(topics, prior, max_topics)
        recommendation = next((row["topic_id"] for row in kept if row["production_state"] == READY), None)
        if not recommendation and not note:
            note = "候选存在事实或素材缺口，或已在历史记录中出现。"
        result = dict(
            schema_version=1, task=task.name, research_date=collected["research_date"], note=note,
            scoring_weights_percent=WEIGHTS, recommended_topic_id=recommendation, topics=kept, duplicates=duplicates,
        )
        atomic_write(task / "daily-brief.md", daily_brief(result, collected))
        write_history(prior + [history_row(row, task.name) for row in kept])
        result["finalization_seconds"] = round(perf_counter() - started, 3)
        atomic_write(task / "topics.json", json_text(result))
    print(f"{task / 'topics.json'} | candidates={len(kept)} recommended={recommendation or 'none'} "
          f"seconds={result['finalization_seconds']:.3f}")


def validate(args):
    task = task_path(args.task)
    collected = read_json(task / "sources.json")
    result = read_json(task / "topics.json")
    topics, _ = draft_topics(task, collected)
    expected = {row["topic_id"]: row for row in topics}
    saved = result["topics"]
    for rank, topic in enumerate(saved, start=1):
        if topic["rank"] != rank or any(topic[key] != value for key, value in expected[topic["topic_id"]].items()):
            raise ValueError(f"Topic doesn't match the validated draft: {topic['topic_id']}")
    if saved != sorted(saved, key=topic_sort_key):
        raise ValueError("Invalid topic ranking")
    recommendation = next((row["topic_id"] for row in saved if row["production_state"] == READY), None)
    if result["scoring_weights_percent"] != WEIGHTS or result["recommended_topic_id"] != recommendation:
        raise ValueError("Invalid scoring weights or recommended topic")
    if (task / "daily-brief.md").read_text(encoding="utf-8") != daily_brief(result, collected):
        raise ValueError("Brief does not match topics.json")
    with history_lock():
        recorded = {row["topic_id"]: row for row in read_history() if row["task"] == task.name}
    if recorded.keys() != {topic["topic_id"] for topic in saved}:
        raise ValueError("History entries do not match finalized topics")
    for topic in saved:
        record = recorded[topic["topic_id"]]
        expected_record = history_row(topic, task.name)
        if record["status"] not in TOPIC_STATES or any(
            record[key] != value for key, value in expected_record.items() if key != "status"
        ):
            raise ValueError(f"Invalid history entry: {topic['topic_id']}")
    print(f"VALID | topics={len(saved)} history={len(recorded)} recommendation={recommendation or 'none'}")


def history_status(args):
    if not args.topic_id:
        for row in read_history():
            print(json.dumps(row, ensure_ascii=False))
        return
    if not args.status or not args.note or not args.note.strip():
        raise ValueError("Updating history requires a status and a note recording the actual progress")
    if args.status == "published":
        raise ValueError("Confirm the actual platform publication with src/review.py confirm")
    with history_lock():
        history = read_history()
        row = next((row for row in history if row["topic_id"] == args.topic_id), None)
        if row is None:
            raise ValueError(f"Unknown topic: {args.topic_id}")
        if not (task_path(row["task"]) / "topics.json").is_file():
            raise ValueError("Finalize the research task before updating a topic status")
        if args.status not in TOPIC_STATES[row["status"]]:
            raise ValueError(f"Invalid status transition: {row['status']} to {args.status}")
        row.update(status=args.status, status_note=args.note.strip(),
                   status_at=datetime.now(SHANGHAI).isoformat(timespec="seconds"))
        write_history(history)
    print(f"{args.topic_id}: {args.status}")


def write_gemini_prompt(args):
    task = task_path(args.task)
    path = task / "gemini-prompt.md"
    result = read_json(task / "topics.json")
    if not result["topics"]:
        raise ValueError("No candidates to review")
    with path.open("x", encoding="utf-8", newline="\n") as output:
        output.write(gemini_prompt(result))
    print(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prep = commands.add_parser("collect", help="Start a new research task")
    prep.add_argument("task")
    prep.add_argument("--hours", type=int, default=72)
    prep.add_argument("--manual", type=Path, help="Import public links and short summaries as JSON")
    prep.add_argument("--offline", action="store_true", help="Only use manually provided links")
    prep.set_defaults(handler=collect)
    for name, handler in [("finalize", finalize), ("validate", validate), ("gemini-prompt", write_gemini_prompt)]:
        cmd = commands.add_parser(name)
        cmd.add_argument("task")
        cmd.set_defaults(handler=handler)
    history = commands.add_parser("history", help="List records, or record a verified status change")
    history.add_argument("topic_id", nargs="?")
    history.add_argument("status", nargs="?", choices=TOPIC_STATES)
    history.add_argument("--note")
    history.set_defaults(handler=history_status)
    args = parser.parse_args()
    try:
        args.handler(args)
    except (OSError, ValueError, KeyError, AttributeError, TypeError) as error:
        print(f"Research error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
