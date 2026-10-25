"""Eval reports: a summary per model alias, compared side by side, as markdown and JSON."""

from __future__ import annotations

import json
import statistics
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from evals.cases import EvalCase
from evals.runner import CaseResult

REPORTS_DIR = Path(__file__).parent / "reports"
CHECKS = ("specialist", "tools", "forbidden", "tier", "reply", "confirm")


@dataclass(frozen=True)
class Summary:
    alias: str
    cases: int
    passed: int
    errors: int
    # check name → (passed, cases that check it)
    checks: dict[str, tuple[int, int]]
    median_ms: float
    p90_ms: float
    median_route_ms: float

    @property
    def pass_rate(self) -> float:
        return self.passed / self.cases if self.cases else 0.0


def _percentile(values: Sequence[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(q * (len(ordered) - 1)))]


def summarise(alias: str, results: Sequence[CaseResult]) -> Summary:
    checks = {
        name: (
            sum(1 for r in results if r.checks.get(name) is True),
            sum(1 for r in results if r.checks.get(name) is not None),
        )
        for name in CHECKS
    }
    totals = [r.total_ms for r in results]
    return Summary(
        alias=alias,
        cases=len(results),
        passed=sum(1 for r in results if r.passed),
        errors=sum(1 for r in results if r.error),
        checks=checks,
        median_ms=statistics.median(totals) if totals else 0.0,
        p90_ms=_percentile(totals, 0.9),
        median_route_ms=statistics.median(r.route_ms for r in results) if results else 0.0,
    )


def _ratio(passed: int, total: int) -> str:
    return f"{passed}/{total}" if total else "–"


def summary_table(summaries: Sequence[Summary]) -> str:
    """Markdown table, one row per alias. Also what the CLI prints."""
    head = ["alias", "passed", "rate", *CHECKS, "errors", "median ms", "p90 ms", "route ms"]
    rows = [
        [
            s.alias,
            _ratio(s.passed, s.cases),
            f"{s.pass_rate:.0%}",
            *(_ratio(*s.checks[name]) for name in CHECKS),
            str(s.errors),
            f"{s.median_ms:.0f}",
            f"{s.p90_ms:.0f}",
            f"{s.median_route_ms:.0f}",
        ]
        for s in summaries
    ]
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    lines += ["| " + " | ".join(row) + " |" for row in rows]
    return "\n".join(lines)


def _cell(result: CaseResult | None) -> str:
    if result is None:
        return "–"
    mark = "pass" if result.passed else "FAIL: " + "; ".join(result.failures)
    return f"{mark} ({result.total_ms:.0f} ms)".replace("|", "\\|")


def render_markdown(
    title: str,
    meta: dict[str, Any],
    results: dict[str, list[CaseResult]],
    cases: Sequence[EvalCase],
    skipped: Sequence[EvalCase],
) -> str:
    summaries = [summarise(alias, rs) for alias, rs in results.items()]
    by_alias = {alias: {r.case_id: r for r in rs} for alias, rs in results.items()}
    lines = [f"# {title}", ""]
    lines += [f"- **{key}:** {value}" for key, value in meta.items()]
    lines += ["", "## Summary", "", summary_table(summaries), "", "## Cases", ""]
    lines.append("| case | input | " + " | ".join(results) + " |")
    lines.append("|---|---|" + "---|" * len(results))
    for case in cases:
        text = case.input.replace("|", "\\|")
        cells = [_cell(by_alias[alias].get(case.id)) for alias in results]
        lines.append(f"| {case.id} | {text} | " + " | ".join(cells) + " |")
    unmocked = sorted(
        {c.name for rs in results.values() for r in rs for c in r.tool_calls if not c.mocked}
    )
    if unmocked:
        lines += ["", f"Tools called without a fixture: {', '.join(unmocked)}."]
    if skipped:
        lines += ["", f"Skipped as pending ({len(skipped)}): {', '.join(c.id for c in skipped)}."]
    return "\n".join(lines) + "\n"


def render_json(
    meta: dict[str, Any],
    results: dict[str, list[CaseResult]],
    skipped: Sequence[EvalCase],
) -> str:
    data = {
        "meta": meta,
        "summary": [
            {**asdict(summarise(alias, rs)), "pass_rate": summarise(alias, rs).pass_rate}
            for alias, rs in results.items()
        ],
        "results": {
            alias: [{**asdict(r), "passed": r.passed} for r in rs] for alias, rs in results.items()
        },
        "skipped": [c.id for c in skipped],
    }
    return json.dumps(data, indent=2, default=str)


def write_reports(
    name: str,
    markdown: str,
    data: str,
    directory: Path = REPORTS_DIR,
    now: datetime | None = None,
) -> tuple[Path, Path]:
    """Write `<timestamp>-<name>.md` and `.json` to `directory`; returns their paths."""
    directory.mkdir(parents=True, exist_ok=True)
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    md, js = directory / f"{stamp}-{name}.md", directory / f"{stamp}-{name}.json"
    md.write_text(markdown, encoding="utf-8")
    js.write_text(data, encoding="utf-8")
    return md, js
