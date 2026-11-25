"""Command-line workflow for local P2 content analysis."""

import argparse
import sys
import tempfile
from pathlib import Path

from analysis_plan import (
    PROJECT, build_outputs, json_text, load_source, make_translation, prepare,
    rank_candidates, read_json, require, validate,
)
from analysis_review import make_review_request, make_review_response, validate_review


def write_new(path: Path, text: str) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as file:
        file.write(text)


def write_json(path: Path, value: dict) -> None:
    write_new(path, json_text(value, indent=2) + "\n")


def publish(output: Path, artifacts: dict[str, dict]) -> None:
    require(not output.exists(), f"{output} already exists. Choose a new --version.")
    with tempfile.TemporaryDirectory(prefix=".p2-", dir=output.parent) as temp:
        staged = Path(temp) / "analysis"
        staged.mkdir()
        for name, value in artifacts.items():
            write_json(staged / name, value)
        staged.rename(output)


def run(args: argparse.Namespace) -> None:
    task = args.task.resolve()
    workspace = PROJECT / "workspace"
    require(task != workspace and task.is_relative_to(workspace), f"Choose a task under {workspace}.")
    source = load_source(task)
    if args.command == "prepare":
        print(json_text(prepare(source, args.chunk_chars, args.chunk)))
        return
    strategy = read_json(PROJECT / "config" / "kai-content.json")
    if args.command == "rank":
        ranked = rank_candidates(source, read_json(args.draft), strategy)
        print(json_text([
            {key: clip[key] for key in ("clip_id", "weighted_score")} for clip in ranked
        ]))
        return

    name = "analysis" if args.version == 1 else f"analysis-v{args.version}"
    output = task / name
    if args.command == "build":
        publish(output, build_outputs(source, read_json(args.draft), strategy))
        print(output)
        return

    artifacts = validate(source, output, strategy)
    validate_review(output, "edit_plan.json" in artifacts)
    if args.command == "validate":
        print("P2 structural validation passed.")
    elif args.command == "translate":
        analysis = artifacts["analysis.json"]
        clip = next((clip for clip in analysis["candidates"] if clip["clip_id"] == args.clip), None)
        require(clip is not None and args.clip != analysis["recommended_clip_id"],
                "Choose an existing non-recommended candidate.")
        target = output / f"translation-{args.clip}.json"
        write_json(target, make_translation(source, clip, read_json(args.draft)["translation_segments"]))
        print(target)
    elif args.command == "review-pack":
        require(args.allow_third_party_sharing, "Owner authorization for third-party sharing is required.")
        require("edit_plan.json" in artifacts, "Review requires a recommended clip.")
        target = output / "review_request.md"
        write_new(target, make_review_request(source, artifacts))
        print(target)
    elif args.command == "import-review":
        target = output / "review_response.json"
        response = make_review_response(
            output, read_json(args.reply), read_json(args.decisions), args.origin,
        )
        write_json(target, response)
        print(target)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("prepare", "rank", "build", "validate", "translate", "review-pack", "import-review"):
        command = commands.add_parser(name)
        command.add_argument("task", type=Path)
        if name not in ("prepare", "rank"):
            command.add_argument("--version", type=int, default=1, metavar="N")
        if name in ("rank", "build", "translate"):
            command.add_argument("draft", type=Path)
        if name == "prepare":
            command.add_argument("--chunk-chars", type=int, default=18_000)
            command.add_argument("--chunk", type=int)
        if name == "translate":
            command.add_argument("--clip", required=True)
        if name == "review-pack":
            command.add_argument("--allow-third-party-sharing", action="store_true")
        if name == "import-review":
            command.add_argument("reply", type=Path)
            command.add_argument("decisions", type=Path)
            command.add_argument("--origin", choices=("gemini", "simulated"), required=True)
    args = parser.parse_args()
    if getattr(args, "version", 1) < 1:
        parser.error("--version must be positive")
    return args


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    try:
        run(parse_args())
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"P2 error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
