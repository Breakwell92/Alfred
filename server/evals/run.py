"""Eval runner CLI. Run from `server/`:

    uv run python -m evals.run --model-alias local-main --tag smoke
    uv run python -m evals.run --model-alias local-main --model-alias eval-candidate

Needs the LiteLLM gateway (`ALFRED_LITELLM_BASE_URL`, `ALFRED_LITELLM_API_KEY`, as for the
server). Tools run in mock mode; nothing real is changed. Exits 1 if any case fails.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import cast

from agno.agent import Agent

from alfred_server.agents.registry import get_registry
from alfred_server.config import get_settings
from alfred_server.models.gateway import CallMetadata, ModelAlias
from evals.cases import load_cases, select
from evals.mocks import load_fixtures
from evals.report import (
    REPORTS_DIR,
    render_json,
    render_markdown,
    summarise,
    summary_table,
    write_reports,
)
from evals.runner import (
    CaseResult,
    CloudNotAllowedError,
    EvalRunner,
    gateway_factory,
    is_cloud_alias,
)

WARM_UP_TEXT = "Say OK."


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="python -m evals.run", description=__doc__.splitlines()[0])
    p.add_argument(
        "--model-alias",
        action="append",
        default=[],
        help="alias the specialist runs on; repeat to compare several (default: router's choice)",
    )
    p.add_argument("--tag", action="append", default=[], help="only cases with this tag")
    p.add_argument("--case", action="append", default=[], help="only this case id")
    p.add_argument(
        "--allow-cloud",
        action="store_true",
        help="let runs use cloud models when the budget service allows them (costs money)",
    )
    p.add_argument("--reports-dir", type=Path, default=REPORTS_DIR)
    p.add_argument("--no-warm-up", action="store_true", help="skip the untimed warm-up call")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args(argv)


async def _warm_up(alias: str | None) -> None:
    """One untimed call per local model so load time isn't counted against the first case.

    Cloud models need no warm-up, and a call would be spent without asking the budget.
    """
    for name in ("local-router", alias or "local-main"):
        if is_cloud_alias(name):
            continue
        model = gateway_factory(cast(ModelAlias, name), CallMetadata(agent="eval"))
        await Agent(model=model, telemetry=False).arun(WARM_UP_TEXT)


async def run(args: argparse.Namespace) -> int:
    all_cases = load_cases()
    cases, skipped = select(all_cases, args.tag, args.case)
    if not cases:
        print("No cases to run (all pending, or no match for --tag/--case).", file=sys.stderr)
        return 2
    get_settings()  # fail early on bad config
    fixtures = load_fixtures()
    registry = get_registry()
    aliases: list[str | None] = list(args.model_alias) or [None]
    # Checked before anything runs: a cloud alias without --allow-cloud is refused.
    try:
        runners = [
            EvalRunner(registry, fixtures, model_alias=alias, allow_cloud=args.allow_cloud)
            for alias in aliases
        ]
    except CloudNotAllowedError as e:
        print(e, file=sys.stderr)
        return 2

    started = datetime.now()
    results: dict[str, list[CaseResult]] = {}
    for alias, runner in zip(aliases, runners, strict=True):
        label = alias or "router"
        if not args.no_warm_up:
            await _warm_up(alias)
        results[label] = []
        for case in cases:
            result = await runner.run_case(case)
            results[label].append(result)
            status = "pass" if result.passed else "FAIL " + "; ".join(result.failures)
            print(f"[{label}] {case.id}: {status} ({result.total_ms:.0f} ms)", flush=True)

    meta = {
        "date": started.isoformat(timespec="seconds"),
        "aliases": ", ".join(results),
        "tags": ", ".join(args.tag) or "all",
        "cases": len(cases),
        "skipped (pending)": len(skipped),
        "cloud runs allowed": args.allow_cloud,
    }
    name = "-vs-".join(results) + (f"-{'-'.join(args.tag)}" if args.tag else "")
    md, js = write_reports(
        name,
        render_markdown("Alfred eval report", meta, results, cases, skipped),
        render_json(meta, results, skipped),
        directory=args.reports_dir,
        now=started,
    )
    print()
    print(summary_table([summarise(label, rs) for label, rs in results.items()]))
    print(f"\nReports: {md}\n         {js}")
    return 0 if all(r.passed for rs in results.values() for r in rs) else 1


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING)
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
