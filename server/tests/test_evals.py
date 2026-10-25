import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from agno.models.base import Model

from alfred_server.agents.registry import get_registry
from alfred_server.config import Settings
from alfred_server.models.gateway import CallMetadata, ModelAlias
from evals import run as cli
from evals.cases import ANY_TOOL, EvalCase, load_cases, select
from evals.mocks import UNMOCKED_RESULT, MockTools, ToolCall, load_fixtures
from evals.report import render_json, render_markdown, summarise, summary_table, write_reports
from evals.runner import CaseResult, CloudNotAllowedError, EvalRunner, gateway_factory, score
from tests.llm_fakes import FakeProxy, reply, route_completion, tool_call_completion
from tests.router_fakes import FakeBudget, deny


def case(**fields: Any) -> EvalCase:
    return EvalCase.model_validate(
        {"id": "c1", "input": "What time is it?", "expected_specialist": "general", **fields}
    )


class Harness:
    """An eval runner over a fake proxy."""

    def __init__(self, *responses: Any, **runner: Any) -> None:
        self.proxy = FakeProxy(*responses)
        self.runner = EvalRunner(
            get_registry(), load_fixtures(), model_factory=self.model, **runner
        )

    def model(self, alias: ModelAlias, metadata: CallMetadata, **kwargs: Any) -> Model:
        return gateway_factory(
            alias,
            metadata,
            settings=Settings(_env_file=None, litellm_api_key="sk-test"),
            http_client=self.proxy.async_client(),
            **kwargs,
        )

    def models_called(self) -> list[str]:
        return [self.proxy.body(i)["model"] for i in range(len(self.proxy.requests))]


# --- Cases -----------------------------------------------------------------------------------


def test_starter_set_loads() -> None:
    cases = load_cases()

    assert len(cases) >= 50
    assert len({c.id for c in cases}) == len(cases)
    smoke, _ = select(cases, ["smoke"])
    assert len(smoke) >= 10


def test_runnable_cases_use_registered_specialists_and_mocked_tools() -> None:
    fixtures = load_fixtures()
    runnable, pending = select(load_cases())

    assert pending, "connectors that aren't built yet are tagged pending"
    for c in runnable:
        assert c.expected_specialist in get_registry().names(), c.id
    for c in [*runnable, *pending]:
        for t in c.expected_tools:
            assert t.name in fixtures, f"{c.id}: no fixture for {t.name}"


def test_select_skips_pending_and_filters() -> None:
    cases = [case(id="a", tags=["smoke"]), case(id="b", tags=["pending", "smoke"]), case(id="c")]

    assert [[c.id for c in group] for group in select(cases, ["smoke"])] == [["a"], ["b"]]
    assert [c.id for c in select(cases, ids=["c"])[0]] == ["c"]


def test_case_rejects_tools_both_expected_and_forbidden() -> None:
    with pytest.raises(ValueError, match="both expected and forbidden"):
        case(expected_tools=[{"name": "get_time"}], must_not_call=["get_time"])
    with pytest.raises(ValueError, match="both expected and forbidden"):
        case(expected_tools=[{"name": "get_time"}], must_not_call=[ANY_TOOL])


def test_case_validates_context() -> None:
    with pytest.raises(ValueError, match="unknown timezone"):
        case(context={"timezone": "Mars/Olympus"})
    with pytest.raises(ValueError, match="session_id"):
        case(context={"session_id": "x"})
    with pytest.raises(ValueError):
        case(unknown_field=1)

    ctx = case(context={"active_room": "kitchen"}).request_context("s1")
    assert (ctx.active_room, ctx.timezone, ctx.now.hour) == ("kitchen", "Europe/London", 14)


def test_load_cases_reports_file_and_duplicates(tmp_path: Path) -> None:
    path = tmp_path / "cases.yaml"
    entry = "- {id: dup, input: hi, expected_specialist: general}\n"
    path.write_text(entry * 2, encoding="utf-8")

    with pytest.raises(ValueError, match="duplicate case id 'dup'"):
        load_cases([path])


# --- Mock tools --------------------------------------------------------------------------------


def test_mock_tools_format_fixtures_and_record_calls() -> None:
    ctx = case().request_context("s1")
    tools = MockTools(
        {"get_time": "{now:%H:%M} ({timezone})", "play": "{query}", "x": {"a": 1}}, ctx
    )

    assert tools("get_time", {}) == "14:05 (Europe/London)"
    assert tools("play", {"query": "Abba"}) == "Abba"
    assert tools("play", {}) == "{query}"  # argument missing: left unformatted
    assert json.loads(tools("x", {})) == {"a": 1}
    assert tools("nope", {"b": 2}) == UNMOCKED_RESULT
    assert tools.calls[-1] == ToolCall("nope", {"b": 2}, mocked=False)
    assert len(tools.calls) == 5


# --- Scoring -----------------------------------------------------------------------------------


def result(**fields: Any) -> CaseResult:
    return CaseResult(
        **{
            "case_id": "c1",
            "alias": "local-main",
            "specialist": "general",
            "routed_alias": "local-main",
            "routed_tier": "local",
            **fields,
        }
    )


def test_score_tool_args_subset() -> None:
    c = case(expected_tools=[{"name": "light_on", "args_subset": {"area": "Kitchen"}}])
    good = result(tool_calls=[ToolCall("light_on", {"area": "kitchen ", "level": 5}, True)])
    wrong = result(tool_calls=[ToolCall("light_on", {"area": "hall"}, True)])

    score(c, good)
    score(c, wrong)

    assert good.passed and good.checks["tools"] is True
    assert not wrong.passed
    assert wrong.failures == ["missing call light_on({'area': 'Kitchen'})"]


def test_score_forbidden_tier_reply_and_specialist() -> None:
    c = case(
        must_not_call=[ANY_TOOL],
        max_tier="local",
        reply_contains=["14:05"],
        expected_specialist="home",
    )
    r = result(
        specialist="general",
        tool_calls=[ToolCall("get_time", {}, True)],
        routed_alias="cloud-claude",
        routed_tier="cloud",
        reply="It's two o'clock.",
    )

    score(c, r)

    assert r.checks == {
        "specialist": False,
        "tools": None,
        "forbidden": False,
        "tier": False,
        "reply": False,
    }
    assert len(r.failures) == 4


def test_score_error_fails_case() -> None:
    r = result(error="AgentRunError: boom")
    score(case(), r)

    assert not r.passed
    assert r.failures[0] == "AgentRunError: boom"


# --- Runner ------------------------------------------------------------------------------------


def test_runner_answers_tools_from_fixtures() -> None:
    h = Harness(
        route_completion(level="simple"),
        tool_call_completion("get_time", {}),
        reply("It's 14:05."),
        model_alias="eval-candidate",
    )
    c = case(expected_tools=[{"name": "get_time"}], max_tier="local", reply_contains=["14:05"])

    r = asyncio.run(h.runner.run_case(c))

    assert r.passed, r.failures
    assert r.tool_calls == [ToolCall("get_time", {}, True)]
    # The classifier stays on local-router; the specialist runs on the alias asked for.
    assert h.models_called() == ["local-router", "eval-candidate", "eval-candidate"]
    tool_message = h.proxy.body(2)["messages"][-1]
    assert tool_message["role"] == "tool"
    assert tool_message["content"] == "14:05 on Saturday 24 October 2026 (Europe/London)"
    assert (r.alias, r.routed_alias, r.input_tokens) == ("eval-candidate", "local-router", 20)
    assert r.total_ms >= r.route_ms > 0


def test_runner_scores_where_router_wants_but_runs_locally() -> None:
    h = Harness(route_completion(level="complex"), reply("A poem."))

    r = asyncio.run(h.runner.run_case(case(max_tier="local")))

    assert (r.routed_alias, r.routed_tier) == ("cloud-claude", "cloud")
    assert h.models_called() == ["local-router", "local-main"]
    assert r.alias == "local-main"
    assert r.checks["tier"] is False


def test_runner_never_falls_back_to_cloud() -> None:
    h = Harness(route_completion(), reply("<<UNSURE>>"))

    r = asyncio.run(h.runner.run_case(case()))

    assert h.models_called() == ["local-router", "local-main"]
    assert r.reply == "Sorry, I'm not sure about that."


CLOUD: list[ModelAlias] = ["cloud-claude", "cloud-gpt", "cloud-gemini"]


def test_runner_allow_cloud_asks_the_real_budget() -> None:
    budget = FakeBudget(CLOUD)
    h = Harness(
        route_completion(cloud=True, provider="gpt"), reply("Hi."), allow_cloud=True, budget=budget
    )

    r = asyncio.run(h.runner.run_case(case()))

    assert h.models_called() == ["local-router", "cloud-gpt"]
    assert budget.calls == [("cloud-gpt", "user_request")]
    assert r.passed


def test_runner_allow_cloud_stays_local_when_budget_refuses() -> None:
    budget = FakeBudget(CLOUD, {a: deny("exhausted") for a in CLOUD})
    h = Harness(route_completion(level="complex"), reply("Hi."), allow_cloud=True, budget=budget)

    r = asyncio.run(h.runner.run_case(case()))

    assert h.models_called() == ["local-router", "local-main"]
    assert (r.alias, r.routed_alias) == ("local-main", "cloud-claude")


def test_forced_cloud_alias_needs_allow_cloud() -> None:
    with pytest.raises(CloudNotAllowedError, match="--allow-cloud"):
        EvalRunner(get_registry(), {}, model_alias="cloud-claude")
    # Eval-only proxy names are local candidates.
    EvalRunner(get_registry(), {}, model_alias="eval-candidate")


def test_forced_cloud_alias_asks_the_budget_each_case() -> None:
    budget = FakeBudget(decisions={"cloud-claude": deny("exhausted", "Over budget.")})
    h = Harness(route_completion(), model_alias="cloud-claude", allow_cloud=True, budget=budget)

    r = asyncio.run(h.runner.run_case(case()))

    assert h.models_called() == ["local-router"]  # nothing ran on cloud
    assert budget.calls == [("cloud-claude", "user_request")]
    assert r.error == "BudgetRefusedError: budget refused cloud-claude: Over budget."

    h = Harness(
        route_completion(),
        reply("Hi."),
        model_alias="cloud-claude",
        allow_cloud=True,
        budget=FakeBudget(),
    )
    assert asyncio.run(h.runner.run_case(case())).passed
    assert h.models_called() == ["local-router", "cloud-claude"]


@pytest.mark.parametrize("alias", ["local-main", "eval-candidate"])
def test_forced_alias_is_never_retried_elsewhere(alias: str) -> None:
    h = Harness(
        route_completion(),
        reply("<<UNSURE>>"),
        model_alias=alias,
        allow_cloud=True,
        budget=FakeBudget(CLOUD),
    )

    r = asyncio.run(h.runner.run_case(case()))

    assert h.models_called() == ["local-router", alias]
    assert r.reply == "Sorry, I'm not sure about that."


def test_runner_records_errors() -> None:
    h = Harness(route_completion(), httpx.Response(500, json={"error": "down"}))

    r = asyncio.run(h.runner.run_case(case()))

    assert r.error is not None
    assert not r.passed


# --- Reports and CLI -------------------------------------------------------------------------


def test_reports(tmp_path: Path) -> None:
    c1, c2 = case(id="a"), case(id="b", input="x | y")
    ok = result(case_id="a", total_ms=100)
    bad = result(case_id="b", total_ms=300, error="boom")
    score(c1, ok)
    score(c2, bad)
    results = {"local-main": [ok, bad], "eval-candidate": [ok]}
    pending = [case(id="p", tags=["pending"])]

    s = summarise("local-main", [ok, bad])
    assert (s.passed, s.cases, s.errors, s.median_ms, s.p90_ms) == (1, 2, 1, 200, 300)
    assert "| local-main | 1/2 | 50% |" in summary_table([s])

    md = render_markdown("Report", {"tags": "smoke"}, results, [c1, c2], pending)
    assert "x \\| y" in md and "FAIL: boom" in md and "Skipped as pending (1): p." in md
    data = json.loads(render_json({}, results, pending))
    assert data["skipped"] == ["p"] and data["results"]["local-main"][1]["passed"] is False

    md_path, json_path = write_reports("smoke", md, "{}", directory=tmp_path)
    assert md_path.read_text(encoding="utf-8") == md and json_path.exists()


def test_cli_without_matching_cases() -> None:
    assert cli.main(["--case", "no-such-case"]) == 2


def test_cli_refuses_cloud_alias_without_allow_cloud(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["--model-alias", "cloud-claude", "--tag", "smoke"]) == 2
    assert "--allow-cloud" in capsys.readouterr().err


def test_cli_parses_several_aliases() -> None:
    args = cli.parse_args(["--model-alias", "local-main", "--model-alias", "eval-candidate"])

    assert args.model_alias == ["local-main", "eval-candidate"]
    assert args.tag == [] and not args.allow_cloud
