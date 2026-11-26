"""Record and resume the local stages coordinated by the Kai Content Agent skill."""

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from analysis_plan import read_json, require
from media import sha256
from research import ROOT, WORKSPACE, atomic_write, history_lock, json_text, ready_topic, task_path
from review_data import Operations, number


MODES = ("daily", "research", "produce", "review")
WORK = {"A": ("ingest", "analysis", "render"), "B": ("storyboard", "render")}
BASE = WORKSPACE / "kai-tasks"


def now():
    return datetime.now(timezone.utc)


def relative(path):
    return path.resolve().relative_to(ROOT).as_posix()


def files(paths):
    return {relative(path): sha256(path) for path in paths}


def media_identity(path, rights, ai_label, source, synthetic):
    path = path.resolve(strict=True)
    require(rights in ("owned", "authorized") and path.is_file(), "Use a local file with confirmed rights.")
    return dict(path=str(path), sha256=sha256(path), rights=rights, ai_label=ai_label,
                source=source or path.as_uri(), synthetic_test=synthetic)


def topic_identity(topic_id, selected_from=None):
    topic, path = ready_topic(topic_id)
    require(selected_from is None or path.resolve() == selected_from.resolve(),
            "Select a topic from this daily task's completed research.")
    identity = {"topic_id": topic_id, "line": topic["line"], "selection": files([path])}
    if topic["line"] == "A":
        item = topic["production"]["interview"]
        identity["media"] = media_identity(ROOT / item["local_path"], item["rights"], item["ai_label"],
                                           item["media_url"], False)
    return identity


def changed(records):
    for path, digest in records.items():
        try:
            if sha256(ROOT / path) == digest:
                continue
        except OSError:
            pass
        return path
    return None


def route(task, identity):
    mode = task["mode"]
    if mode == "review":
        return ("review",)
    stages = ("collect", "research") if mode in ("daily", "research") else ()
    if mode == "research":
        return stages
    if mode == "daily":
        stages += ("select",)
    return stages + (*WORK[identity["line"]], "package", "register") if identity else stages


def resume(task):
    identity = task["input"]
    if identity:
        require(not (stale := changed(identity.get("selection", {}))), f"Original task selection changed: {stale}")
    valid = {}
    stale = None
    for record in task["checkpoints"]:
        if stale := changed(record["files"]):
            break
        valid[record["stage"]] = record
        if record["stage"] == "select":
            identity = record["selection"]
    if identity:
        if media := identity.get("media"):
            require(not changed({media["path"]: media["sha256"]}), "Task source media changed. Restore it or start a new task.")
        if identity.get("topic_id"):
            ready_topic(identity["topic_id"])
    stages = route(task, identity)
    return valid, identity, next((stage for stage in stages if stage not in valid), None), stale


def primary(records, stage):
    return ROOT / records[stage]["artifact"]


def matching_ref(ref, path):
    require((ROOT / ref["path"]).resolve() == path.resolve() and sha256(path) == ref["sha256"],
            f"Manifest and checkpoint disagree: {path}")


def checked_files(base, records):
    paths = []
    for name, digest in records.items():
        path = (base / name).resolve()
        require(path.is_relative_to(base) and sha256(path) == digest, f"Artifact hash mismatch: {path}")
        paths.append(path)
    return paths


def checkpoint_files(stage, path, identity, previous):
    data = read_json(path)
    base = path.parent
    linked = []
    details = {}
    if stage == "collect":
        require(path.name == "sources.json", "Checkpoint the P5 collection sources.json.")
    elif stage == "research":
        require(path == primary(previous, "collect").with_name("topics.json"), "Research must match the collected task.")
        require(isinstance(data["topics"], list), "Use finalized P5 topics.")
        linked = [base / "daily-brief.md"]
    elif stage == "ingest":
        media = identity["media"]
        require(path.name == "media.json" and Path(data["source_media"]).resolve() == Path(media["path"])
                and data["rights_status"] == media["rights"] and data["ai_label"] == media["ai_label"],
                "P1 input differs from the task media, rights or AI label.")
        require("source_sha256" not in data or media["sha256"] == data["source_sha256"], "P1 original media hash differs.")
        synthetic = data.get("synthetic_test", False) or "synthetic test" in data["source"].lower()
        require(synthetic == media["synthetic_test"], "P1 test status differs from the task.")
        details["import_identity"] = "verified" if "source_sha256" in data else "legacy_unrecorded"
        linked = [base / "transcript.json"]
    elif stage == "analysis":
        require(path.name == "edit_plan.json", "Checkpoint a validated P2 edit plan.")
        plan_analysis = read_json(base / "analysis.json")
        require(data["recommended_clip_id"] == plan_analysis["recommended_clip_id"], "P2 recommended clip changed.")
        checked_files(primary(previous, "ingest").parent, plan_analysis["input_sha256"])
        linked = [base / "analysis.json", base / "translation.json"]
    elif stage == "storyboard":
        require(data["topic_id"] == identity["topic_id"] and data["mode"] == "release-candidate",
                "Use a release-candidate storyboard for the selected B-line topic.")
        linked = [base / data["evidence"]]
    elif stage == "render":
        require(path.name == "render_manifest.json", "Checkpoint the render manifest.")
        if identity["line"] == "A":
            source = data["source"]
            media = identity["media"]
            edit = primary(previous, "analysis")
            require(data["render_mode"] == "full" and not data.get("preview_only")
                    and data["original_commentary"]["status"] == "review_candidate"
                    and Path(source["path"]).resolve() == Path(media["path"]) and source["sha256"] == media["sha256"]
                    and Path(data["p2"]["plan_path"]).resolve() == edit
                    and data["p2"]["p1_input_sha256"] == read_json(edit.with_name("analysis.json"))["input_sha256"],
                    "A render differs from the approved media and plan, or is a draft.")
            checked_files(edit.parent, data["p2"]["sha256"])
            linked = [base / "timeline.json", base / "cover-3x4.png"]
            for entry in data["original_commentary"].get("inputs", {}).values():
                audio_input = Path(entry["path"])
                matching_ref(entry, audio_input)
                linked.append(audio_input)
        else:
            require(data["topic_id"] == identity["topic_id"] and data["mode"] == "release-candidate",
                    "B render differs from the selected topic or is a draft.")
            storyboard = primary(previous, "storyboard")
            matching_ref(data["storyboard"], storyboard)
            require(sha256(base / "facts-and-test.md") == sha256(storyboard.parent / read_json(storyboard)["evidence"]),
                    "Rendered evidence differs from the approved storyboard evidence.")
            for asset in data["source_assets"].values():
                source = Path(asset["path"])
                matching_ref(asset, source)
                linked.append(source)
            linked += checked_files(ROOT, data["voice"].get("recording_sha256", {}))
        linked += checked_files(base, data.get("artifacts_sha256", {}))
        linked += [base / name for name in ("video.mp4", "cover.png", "subtitles.ass")]
    elif stage == "package":
        matching_ref(data["source"]["render"], primary(previous, "render"))
        simulated = identity.get("media", {}).get("synthetic_test", False)
        mode = "simulated" if simulated else "real"
        require(data["status"] == "awaiting_human_review" and data.get("data_mode", "real") == mode
                and data["source"]["lane"] == identity["line"] and data["source"]["topic_id"] == identity.get("topic_id")
                and set(data["platforms"]) == {"douyin", "xiaohongshu"}, "Package is incomplete, blocked or misidentified.")
        for platform in data["platforms"].values():
            entries = [platform["files"][key] for key in ("video", "cover", "copy")]
            entries += platform.get("cards", {}).get("files", [])
            linked += checked_files(base, {row["path"]: row["sha256"] for row in entries})
    elif stage == "register":
        package = primary(previous, "package")
        simulated = identity.get("media", {}).get("synthetic_test", False)
        require(isinstance(data, list) and data, "Save the P8 register output as a JSON receipt.")
        operations = Operations(simulated)
        with operations.lock:
            _, _, ledger = operations.publications()
        digest = sha256(package)
        ids = {row["publication_id"] for row in data}
        require(ids == {key for key, (row, _) in ledger.items() if row["publish_manifest"]["sha256"] == digest}
                and {ledger[key][0]["platform"] for key in ids} == {"douyin", "xiaohongshu"},
                "Receipt does not cover the two platforms in the correct P8 ledger.")
        for row in data:
            saved = ledger[row["publication_id"]][0]
            matching_ref(row["publish_manifest"], package)
            require(row["publish_manifest"] == saved["publish_manifest"] and row["data_mode"] == operations.mode,
                    "Receipt differs from the recorded publication.")
        details["publication_ids"] = sorted(ids)
    elif stage == "review":
        require(path.name == "review.json" and data["data_mode"] == "real"
                and path.is_relative_to(WORKSPACE / "p8-operations/reports"), "Use a real P8 review report.")
        linked = [base / "review.md"]
    return [path, *linked], details


def init(args):
    identity = None
    require((args.mode == "produce") == bool(args.topic or args.media), "Produce requires a topic ID or local media.")
    require(args.media or not (args.rights or args.source or args.synthetic_test or args.ai_label != "unknown"),
            "Media options require --media.")
    if args.topic:
        identity = topic_identity(args.topic)
    if args.media:
        identity = dict(line="A", media=media_identity(args.media, args.rights, args.ai_label,
                                                      args.source, args.synthetic_test))
    task = dict(schema_version=1, task_id=args.task_id, mode=args.mode, input=identity, checkpoints=[],
                created_at=now().isoformat(), hold=None)
    task["next_stage"] = route(task, identity)[0]
    return task


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    begin = commands.add_parser("init", help="Create a task with an immutable mode and input")
    begin.add_argument("task_id")
    begin.add_argument("mode", choices=MODES)
    binding = begin.add_mutually_exclusive_group()
    binding.add_argument("--topic")
    binding.add_argument("--media", type=Path)
    begin.add_argument("--rights", choices=("owned", "authorized"))
    begin.add_argument("--ai-label", choices=("required", "not-required", "unknown"), default="unknown")
    begin.add_argument("--source")
    begin.add_argument("--synthetic-test", action="store_true")
    view = commands.add_parser("resume", help="Return the verified stages and next action without running production")
    view.add_argument("task_id")
    stop = commands.add_parser("hold", help="Record a pending action or failure at the current stage")
    stop.add_argument("task_id")
    stop.add_argument("reason")
    save = commands.add_parser("checkpoint", help="Verify and record a stage's completed artifact")
    save.add_argument("task_id")
    save.add_argument("stage")
    save.add_argument("artifact", type=Path)
    save.add_argument("--topic", help="Selected full topic ID for the daily select stage")
    save.add_argument("--file", type=Path, action="append", default=[], help="Additional project-local dependency or evidence")
    save.add_argument("--seconds", help="Measured stage wall time; defaults to elapsed time since the previous checkpoint")
    save.add_argument("--reused", action="store_true", help="Record adoption and verification of existing outputs")
    save.add_argument("--note")
    save.add_argument("--manual-minutes")
    save.add_argument("--cost-cny")
    args = parser.parse_args()

    try:
        directory = BASE / task_path(args.task_id).name
        path = directory / "task.json"
        with history_lock(path):
            if args.command == "init":
                require(not path.exists(), "Task exists. Use resume with the same task ID.")
                atomic_write(path, json_text(init(args)))
            task = read_json(path)
            previous, identity, stage, stale = resume(task)
            if args.command == "hold":
                require(stage is not None, "Completed tasks have no pending stage.")
                task["hold"] = dict(stage=stage, reason=args.reason, at=now().isoformat())
                atomic_write(path, json_text(task))
            if args.command == "checkpoint":
                require(stage == args.stage, f"Expected stage {stage}, received {args.stage}.")
                at = now()
                artifact = args.artifact.resolve(strict=True)
                if stage == "select":
                    require(bool(args.topic) and artifact == primary(previous, "research"), "Select from the verified topics.json.")
                    details = {"selection": topic_identity(args.topic, artifact)}
                    outputs = [artifact]
                else:
                    require(args.topic is None, "--topic is only applicable to the select stage.")
                    outputs, details = checkpoint_files(stage, artifact, identity, previous)
                last = task["checkpoints"][len(previous) - 1]["completed_at"] if previous else task["created_at"]
                seconds = number(args.seconds, "seconds")
                if seconds is None:
                    seconds = (at - datetime.fromisoformat(last)).total_seconds()
                item = dict(stage=stage, artifact=relative(artifact), files=files([*outputs, *args.file]),
                            completed_at=at.isoformat(), seconds=round(seconds, 3), reused=args.reused, note=args.note,
                            manual_minutes=number(args.manual_minutes, "manual_minutes"),
                            cost_cny=number(args.cost_cny, "cost_cny", money=True), **details)
                if task["hold"]:
                    item["resolved_hold"] = task["hold"]
                previous[stage] = item
                identity = details.get("selection", identity)
                stage = next((name for name in route(task, identity) if name not in previous), None)
                task.update(checkpoints=list(previous.values()), next_stage=stage, hold=None)
                atomic_write(path, json_text(task))
                stale = None
            print(json_text(dict(task_path=relative(path), mode=task["mode"], identity=identity,
                                 verified=list(previous), next_stage=stage, invalid_file=stale, hold=task["hold"])), end="")
    except (OSError, ValueError, KeyError, AttributeError, TypeError) as error:
        print(f"Kai task error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
