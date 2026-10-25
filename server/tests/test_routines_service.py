"""Saving, validating and running routines (no model anywhere)."""

import asyncio
import logging

import pytest

from alfred_server.confirmations.service import ConfirmationsUnavailableError
from alfred_server.connectors.builtin.home_assistant import HomeAssistantError
from alfred_server.connectors.builtin.test_messages import SENT_TEST_MESSAGES
from alfred_server.routines.models import Routine, RoutineStep
from alfred_server.routines.service import RoutineNotFoundError
from alfred_server.routines.validation import RoutineInvalidError
from tests.ha_fakes import result
from tests.routine_helpers import Policies, make_service, movie_time

SEND = RoutineStep(
    connector="test_messages", tool="send_test_message", args={"recipient": "Sam", "text": "Hi"}
)


# --- Saving -----------------------------------------------------------------------------------


def test_create_get_update_delete() -> None:
    service, *_ = make_service()
    saved = service.create(movie_time())
    assert service.get(saved.id) == saved
    assert service.find("Movie Time") == saved
    assert [r.name for r in service.all()] == ["movie time"]

    updated = service.update(saved.id, movie_time(phrases=["movie time", "cinema"]))
    assert updated.phrases == ["movie time", "cinema"]
    assert updated.created_at == saved.created_at

    service.delete(saved.id)
    assert service.all() == []
    with pytest.raises(RoutineNotFoundError):
        service.get(saved.id)
    with pytest.raises(RoutineNotFoundError):
        service.delete(saved.id)


def test_save_replaces_a_routine_of_the_same_name() -> None:
    service, *_ = make_service()
    first = service.save(movie_time())
    second = service.save(movie_time(reply="Enjoy the film."))
    assert second.id == first.id
    assert service.get(first.id).reply == "Enjoy the film."


def test_a_phrase_can_only_run_one_routine() -> None:
    service, *_ = make_service()
    service.create(movie_time())
    with pytest.raises(RoutineInvalidError, match="already runs the movie time routine"):
        service.create(movie_time(name="film", phrases=["Movie time!"]))
    with pytest.raises(RoutineInvalidError, match="already a routine called"):
        service.create(movie_time(phrases=["cinema"]))


@pytest.mark.parametrize("phrase", ["yes", "No thanks", "!!!"])
def test_phrases_that_answer_questions_or_say_nothing_are_refused(phrase: str) -> None:
    service, *_ = make_service()
    with pytest.raises(RoutineInvalidError):
        service.create(movie_time(phrases=[phrase]))


@pytest.mark.parametrize("tool", ["ha_unlock", "ha_open_door", "ha_lock"])
def test_irreversible_tools_are_never_in_a_routine(tool: str) -> None:
    service, *_ = make_service()
    step = RoutineStep(connector="home_assistant", tool=tool, args={"name": "back door"})
    with pytest.raises(RoutineInvalidError, match="approval in the Alfred app"):
        service.create(movie_time(steps=[step], approved_tools=[tool]))


def test_a_tool_raised_to_irreversible_is_refused_too() -> None:
    raised = Policies({("home_assistant", "ha_turn_on"): "irreversible"})
    service, *_ = make_service(policies=raised)
    with pytest.raises(RoutineInvalidError, match="Step 2"):
        service.create(movie_time())


def test_a_gated_tool_needs_approving_for_the_routine() -> None:
    service, *_ = make_service()
    with pytest.raises(RoutineInvalidError, match="normally asks first"):
        service.create(movie_time(steps=[SEND]))
    saved = service.create(movie_time(steps=[SEND], approved_tools=["send_test_message"]))
    assert saved.approved_tools == ["send_test_message"]


def test_approvals_are_trimmed_to_the_routines_own_gated_steps() -> None:
    service, *_ = make_service()
    saved = service.create(movie_time(approved_tools=["ha_turn_on", "send_test_message"]))
    # ha_turn_on runs without asking anyway; send_test_message isn't a step.
    assert saved.approved_tools == []


@pytest.mark.parametrize(
    ("connector", "tool", "problem"),
    [
        ("nope", "ha_turn_on", "no nope connector"),
        ("spotify", "ha_turn_on", "Spotify has no tool called ha_turn_on"),
        ("routines", "save_routine", "can't change routines"),
    ],
)
def test_steps_must_be_declared_tools(connector: str, tool: str, problem: str) -> None:
    service, *_ = make_service()
    step = RoutineStep(connector=connector, tool=tool)
    with pytest.raises(RoutineInvalidError, match=problem):
        service.create(movie_time(steps=[step], approved_tools=[tool]))


def test_arguments_are_checked_against_the_tools_schema() -> None:
    service, *_ = make_service()
    bad = RoutineStep(connector="home_assistant", tool="ha_turn_on", args={"room": "lounge"})
    with pytest.raises(RoutineInvalidError, match="no setting called room"):
        asyncio.run(service.check_arguments(movie_time(steps=[bad])))
    missing = RoutineStep(connector="home_assistant", tool="ha_turn_on", args={"area": "lounge"})
    with pytest.raises(RoutineInvalidError, match="needs domain"):
        asyncio.run(service.check_arguments(movie_time(steps=[missing])))
    asyncio.run(service.check_arguments(movie_time()))


def test_step_catalog_lists_connected_tools_without_irreversible_ones() -> None:
    service, loader, *_ = make_service()
    catalog = {t["tool"]: t for t in asyncio.run(service.step_catalog())}
    assert "ha_turn_on" in catalog and "spotify_play" in catalog
    assert not {"ha_lock", "ha_unlock", "ha_open_door", "ha_close_door"} & set(catalog)
    assert "save_routine" not in catalog
    assert catalog["ha_turn_on"]["asks_first"] is False
    assert catalog["send_test_message"]["asks_first"] is True
    assert catalog["ha_turn_on"]["parameters"]["required"] == ["domain"]
    assert "routines" not in loader.loaded


# --- Running ----------------------------------------------------------------------------------


def test_running_calls_every_step_in_order_without_a_model(
    caplog: pytest.LogCaptureFixture,
) -> None:
    service, loader, _, _ = make_service()
    routine = service.create(movie_time())
    with caplog.at_level(logging.INFO, logger="alfred_server.routines"):
        ran = asyncio.run(service.run(routine, "voice"))
    assert loader.ha.calls == [
        ("HassLightSet", {"area": "lounge", "brightness": 30}),
        ("HassTurnOn", {"name": "TV", "domain": ["media_player"]}),
    ]
    assert ran.ok
    assert ran.reply == "Movie time."
    assert [s.index for s in ran.steps] == [1, 2]
    assert ran.ms < 1000  # server time, with fakes
    assert loader.loaded == ["home"]  # only the connectors the steps use
    assert "routine 'movie time' ran 2 of 2 steps" in caplog.text
    assert "(no LLM, trigger voice)" in caplog.text


def test_a_failed_step_is_reported_and_the_rest_still_run() -> None:
    service, loader, _, _ = make_service()
    loader.ha.answers["HassLightSet"] = result("Lounge lights unavailable", error=True)
    routine = service.create(movie_time(reply="Enjoy the film."))
    ran = asyncio.run(service.run(routine, "text"))
    assert [s.ok for s in ran.steps] == [False, True]
    assert len(loader.ha.calls) == 2
    assert ran.reply == (
        "Enjoy the film. Step 1 (set the lounge lights) failed: Home Assistant couldn't do "
        "that: Lounge lights unavailable."
    )


def test_stop_on_error_skips_the_rest() -> None:
    service, loader, _, _ = make_service()
    loader.ha.fail = HomeAssistantError("Home Assistant isn't reachable right now.")
    spec = movie_time()
    spec.steps[0].stop_on_error = True
    routine = service.create(spec)
    ran = asyncio.run(service.run(routine, "text"))
    assert len(loader.ha.calls) == 1
    assert [(s.ok, s.skipped) for s in ran.steps] == [(False, False), (False, True)]
    assert ran.reply.endswith("isn't reachable right now. I stopped there.")


def test_delays_are_waited_before_their_step() -> None:
    service, _, _, sleep = make_service()
    spec = movie_time()
    spec.steps[1].delay_s = 5
    asyncio.run(service.run(service.create(spec), "text"))
    assert sleep.slept == [5]


def test_a_disconnected_connectors_steps_fail_cleanly() -> None:
    service, loader, _, _ = make_service()
    loader.disconnected.add("home")
    ran = asyncio.run(service.run(service.create(movie_time()), "text"))
    assert [s.message for s in ran.steps] == ["Home Assistant isn't connected."] * 2


def test_tools_that_fail_to_load_fail_their_steps() -> None:
    service, loader, _, _ = make_service()
    loader.broken.add("home")
    ran = asyncio.run(service.run(service.create(movie_time()), "text"))
    assert not any(s.ok for s in ran.steps)


def test_bad_saved_arguments_fail_the_step() -> None:
    service, loader, _, _ = make_service()
    bad = RoutineStep(connector="home_assistant", tool="ha_turn_on", args={"colour": "red"})
    ran = asyncio.run(service.run(service.create(movie_time(steps=[bad])), "text"))
    assert ran.steps[0].message == "It couldn't run with the saved settings."
    assert loader.ha.calls == []


def test_approved_gated_steps_run_without_asking() -> None:
    SENT_TEST_MESSAGES.clear()
    service, *_ = make_service()
    routine = service.create(movie_time(steps=[SEND], approved_tools=["send_test_message"]))
    ran = asyncio.run(service.run(routine, "voice"))
    assert ran.ok
    assert SENT_TEST_MESSAGES == [("Sam", "Hi")]


def test_a_step_raised_to_irreversible_since_saving_doesnt_run() -> None:
    service, loader, policies, _ = make_service()
    routine = service.create(movie_time())
    policies.overrides[("home_assistant", "ha_turn_on")] = "irreversible"
    ran = asyncio.run(service.run(routine, "voice"))
    assert [s.ok for s in ran.steps] == [True, False]
    assert "needs approval in the Alfred app" in ran.steps[1].message
    assert [c[0] for c in loader.ha.calls] == ["HassLightSet"]


def test_a_step_raised_to_write_since_saving_doesnt_run_unapproved() -> None:
    service, loader, policies, _ = make_service()
    routine = service.create(movie_time())
    policies.overrides[("home_assistant", "ha_turn_on")] = "write"
    ran = asyncio.run(service.run(routine, "voice"))
    assert "normally asks first" in ran.steps[1].message


def test_running_fails_closed_without_the_policy() -> None:
    service, loader, policies, _ = make_service()
    routine = service.create(movie_time())
    policies.down = True
    with pytest.raises(ConfirmationsUnavailableError):
        asyncio.run(service.run(routine, "voice"))
    assert loader.ha.calls == []


def test_match_and_run() -> None:
    service, loader, _, _ = make_service()
    service.create(movie_time())
    assert asyncio.run(service.match_and_run("what time is it", "text")) is None
    assert loader.ha.calls == []
    ran = asyncio.run(service.match_and_run("Alfred, movie time", "text"))
    assert ran is not None and ran.ok


def test_match_and_run_falls_through_when_routines_cant_be_read() -> None:
    service, *_ = make_service()

    def broken() -> list[Routine]:
        raise RuntimeError("database down")

    service.store.all = broken  # type: ignore[method-assign]
    assert asyncio.run(service.match_and_run("movie time", "text")) is None


def test_turn_info_looks_like_a_routed_turn() -> None:
    service, *_ = make_service()
    ran = asyncio.run(service.run(service.create(movie_time()), "text"))
    info = ran.turn_info()
    assert info["specialist"] == "routine"
    assert info["tools"] == ["ha_set_light", "ha_turn_on"]
    assert info["cost_gbp"] is None and info["input_tokens"] == 0
    assert info["routine"]["name"] == "movie time"
    assert len(info["routine"]["steps"]) == 2
