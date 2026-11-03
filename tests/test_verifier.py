"""Required scenarios, full result auditing, authoritative inputs, and invalid snapshots."""

import copy
import importlib
import json
from dataclasses import replace
from pathlib import Path

import pytest
from slimconfig import build_output, plain_output

import verifier
from contract import (
    DeadlockCycle,
    DeadlockInput,
    DeadlockOutput,
    DeadlockStatus,
    RecoveryPlan,
    ThreadState,
    WaitForEdge,
)
from plugin import resolve_deadlock

CASES = Path(__file__).parent / "cases"


def sample(name="required-three-cycle"):
    return build_output(json.loads((CASES / (name + ".input.json")).read_text()), DeadlockInput)


@pytest.mark.parametrize("path", sorted(CASES.glob("*.input.json")), ids=lambda p: p.stem)
def test_exact_declared_scenarios(path, tmp_path):
    given = build_output(json.loads(path.read_text()), DeadlockInput)
    before = copy.deepcopy(given)
    made = resolve_deadlock(given, tmp_path)
    expected = json.loads(path.with_name(path.name.replace(".input.", ".expected.")).read_text())
    assert plain_output(made) == expected
    assert verifier.verify_input("resolve_deadlock", given)["valid"]
    assert verifier.verify_output("resolve_deadlock", given, made, "")["valid"]
    assert verifier.verify_task("fix-deadlock", given, made, None, "")["valid"]
    assert given == before and not list(tmp_path.iterdir())


@pytest.mark.parametrize(
    "field",
    [
        "snapshot",
        "status",
        "wait_for_edges",
        "cycles",
        "cycle_members",
        "blocked_behind_cycles",
        "plan",
        "total_rollback_cost",
        "explanation",
    ],
)
def test_every_output_field_is_authoritatively_checked(field, tmp_path):
    given = sample()
    made = resolve_deadlock(given, tmp_path)
    wrong = {
        "snapshot": replace(given, locks=list(reversed(given.locks))),
        "status": DeadlockStatus.NO_DEADLOCK,
        "wait_for_edges": [],
        "cycles": [],
        "cycle_members": ["A"],
        "blocked_behind_cycles": ["A"],
        "plan": None,
        "total_rollback_cost": 3,
        "explanation": "Just abort someone.",
    }
    result = verifier.verify_task("fix-deadlock", given, replace(made, **{field: wrong[field]}), None, "")
    assert not result["valid"] and result["score"] == 0


@pytest.mark.parametrize("field", ["victims", "released_locks", "remaining_edges"])
def test_every_plan_field_is_checked(field, tmp_path):
    given = sample()
    made = resolve_deadlock(given, tmp_path)
    wrong = {"victims": ["A"], "released_locks": ["L3"], "remaining_edges": []}
    plan = replace(made.plan, **{field: wrong[field]})
    assert not verifier.verify_task("fix-deadlock", given, replace(made, plan=plan), None, "")["valid"]


@pytest.mark.parametrize("which", ["original", "remaining"])
@pytest.mark.parametrize("field", ["waiter", "holder", "lock"])
def test_every_edge_field_is_checked(which, field, tmp_path):
    given = sample()
    made = resolve_deadlock(given, tmp_path)
    rows = list(made.wait_for_edges if which == "original" else made.plan.remaining_edges)
    rows[0] = replace(rows[0], **{field: "unknown"})
    bad = (
        replace(made, wait_for_edges=rows)
        if which == "original"
        else replace(made, plan=replace(made.plan, remaining_edges=rows))
    )
    assert not verifier.verify_task("fix-deadlock", given, bad, None, "")["valid"]


@pytest.mark.parametrize("mutation", ["rotate", "reverse", "duplicate", "close_again", "sorted_members"])
def test_cycle_canonicalization_and_direction(mutation, tmp_path):
    given = sample("directed-cycle-order")
    made = resolve_deadlock(given, tmp_path)
    route = made.cycles[0].members
    wrong = {
        "rotate": [DeadlockCycle(route[1:] + route[:1])],
        "reverse": [DeadlockCycle([route[0], *reversed(route[1:])])],
        "duplicate": [made.cycles[0], made.cycles[0]],
        "close_again": [DeadlockCycle([*route, route[0]])],
        "sorted_members": [DeadlockCycle(sorted(route))],
    }
    assert not verifier.verify_task("fix-deadlock", given, replace(made, cycles=wrong[mutation]), None, "")["valid"]


def test_more_expensive_acyclic_recovery_is_rejected(tmp_path):
    given = sample()
    made = resolve_deadlock(given, tmp_path)
    plan = RecoveryPlan(["A"], ["L1"], [WaitForEdge("B", "C", "L3")])
    wrong = replace(
        made,
        plan=plan,
        total_rollback_cost=5,
        explanation="Rollback A for total cost 5; release L1 to break every current cycle.",
    )
    assert not verifier.verify_task("fix-deadlock", given, wrong, None, "")["valid"]


def test_equal_cost_wrong_global_tie_break_is_rejected(tmp_path):
    given = sample("global-equal-cost")
    made = resolve_deadlock(given, tmp_path)
    wrong = replace(
        made,
        plan=RecoveryPlan(["C", "Z"], ["L2", "L4"], []),
        explanation="Rollback C, Z for total cost 4; release L2, L4 to break every current cycle.",
    )
    assert not verifier.verify_task("fix-deadlock", given, wrong, None, "")["valid"]


def test_protected_cheapest_and_partial_unrecoverable_plans_are_rejected(tmp_path):
    given = sample("protected-cheapest")
    made = resolve_deadlock(given, tmp_path)
    wrong = replace(made, plan=RecoveryPlan(["A"], ["L1"], []), total_rollback_cost=1)
    assert not verifier.verify_task("fix-deadlock", given, wrong, None, "")["valid"]
    given = sample("mixed-unrecoverable")
    made = resolve_deadlock(given, tmp_path)
    assert made.status == DeadlockStatus.UNRECOVERABLE and made.plan is made.total_rollback_cost is None
    wrong = replace(
        made,
        status=DeadlockStatus.RECOVERY_PLAN,
        plan=RecoveryPlan(["C"], ["L3"], made.wait_for_edges[:2]),
        total_rollback_cost=1,
    )
    assert not verifier.verify_task("fix-deadlock", given, wrong, None, "")["valid"]
    assert verifier.verify_task("fix-deadlock", given, made, None, "")["valid"]


def test_dependency_is_not_cycle_membership_and_release_does_not_acquire(tmp_path):
    given = sample("blocked-behind")
    made = resolve_deadlock(given, tmp_path)
    assert made.cycle_members == ["A", "B"] and made.blocked_behind_cycles == ["C", "D", "E"]
    assert made.plan.victims == ["B"] and made.plan.released_locks == ["L2"]
    assert made.plan.remaining_edges == [
        WaitForEdge("C", "A", "L1"),
        WaitForEdge("D", "C", "L3"),
        WaitForEdge("E", "C", "L3"),
    ]
    bad = replace(made, cycle_members=["A", "B", "C", "D", "E"], blocked_behind_cycles=[])
    assert not verifier.verify_task("fix-deadlock", given, bad, None, "")["valid"]
    # A still holds L1 and merely requests now-free L2; C is not given L1 by fiat.
    assert given.threads[0].held_locks == ["L1"]


def test_original_input_cannot_be_replaced_with_a_self_consistent_forgery(tmp_path):
    given = sample()
    changed = replace(given, threads=[replace(t, waiting_for=None) for t in given.threads])
    forged = resolve_deadlock(changed, tmp_path)
    assert not verifier.verify_task("fix-deadlock", given, forged, None, "")["valid"]


def test_permuted_input_changes_only_the_echo(tmp_path):
    given = sample("release-all-held")
    made = resolve_deadlock(given, tmp_path)
    alternate = replace(
        given,
        locks=list(reversed(given.locks)),
        threads=[replace(t, held_locks=list(reversed(t.held_locks))) for t in reversed(given.threads)],
    )
    reordered = resolve_deadlock(alternate, tmp_path)
    assert reordered.snapshot == alternate and replace(reordered, snapshot=given) == made
    assert verifier.verify_task("fix-deadlock", alternate, reordered, None, "")["valid"]


@pytest.mark.parametrize(
    "mutation,words",
    [
        ("empty_threads", "1 to 12"),
        ("too_many_threads", "1 to 12"),
        ("too_many_locks", "24"),
        ("duplicate_thread", "duplicate thread"),
        ("duplicate_lock", "duplicate lock"),
        ("duplicate_held", "duplicate held-lock"),
        ("two_owners", "ownership"),
        ("unknown_held", "unknown held lock"),
        ("unknown_request", "requested lock"),
        ("blank_thread", "thread ID"),
        ("blank_lock", "lock ID"),
        ("blank_held", "lock ID"),
        ("blank_request", "lock ID"),
        ("zero_cost", "positive integer"),
        ("negative_cost", "positive integer"),
        ("bool_cost", "positive integer"),
        ("float_cost", "positive integer"),
        ("string_flag", "boolean"),
    ],
)
def test_invalid_inputs_are_rejected_with_clear_errors(mutation, words, tmp_path):
    given = copy.deepcopy(sample())
    if mutation == "empty_threads":
        given.threads = []
    elif mutation == "too_many_threads":
        given.threads = [ThreadState(str(i), [], None, True, 1) for i in range(13)]
    elif mutation == "too_many_locks":
        given.locks = [str(i) for i in range(25)]
    elif mutation == "duplicate_thread":
        given.threads[1].id = "A"
    elif mutation == "duplicate_lock":
        given.locks.append("L1")
    elif mutation == "duplicate_held":
        given.threads[0].held_locks.append("L1")
    elif mutation == "two_owners":
        given.threads[1].held_locks.append("L1")
    elif mutation == "unknown_held":
        given.threads[0].held_locks = ["Missing"]
    elif mutation == "unknown_request":
        given.threads[0].waiting_for = "Missing"
    elif mutation == "blank_thread":
        given.threads[0].id = " "
    elif mutation == "blank_lock":
        given.locks[0] = " "
    elif mutation == "blank_held":
        given.threads[0].held_locks = [" "]
    elif mutation == "blank_request":
        given.threads[0].waiting_for = " "
    elif mutation == "zero_cost":
        given.threads[0].rollback_cost = 0
    elif mutation == "negative_cost":
        given.threads[0].rollback_cost = -1
    elif mutation == "bool_cost":
        given.threads[0].rollback_cost = True
    elif mutation == "float_cost":
        given.threads[0].rollback_cost = 1.5
    else:
        given.threads[0].can_abort = "yes"
    result = verifier.verify_input("resolve_deadlock", given)
    assert not result["valid"] and result["score"] == 0
    with pytest.raises(ValueError, match=words):
        resolve_deadlock(given, tmp_path)


def test_missing_required_fields_and_bad_schema(tmp_path):
    raw = plain_output(sample())
    for field in ["id", "held_locks", "waiting_for", "can_abort", "rollback_cost"]:
        bad = copy.deepcopy(raw)
        bad["threads"][0].pop(field)
        with pytest.raises((ValueError, TypeError)):
            build_output(bad, DeadlockInput)
    for field in ["locks", "threads"]:
        bad = copy.deepcopy(raw)
        bad.pop(field)
        with pytest.raises((ValueError, TypeError)):
            build_output(bad, DeadlockInput)
    made = plain_output(resolve_deadlock(sample(), tmp_path))
    made["total_rollback_cost"] = "two"
    with pytest.raises((ValueError, TypeError)):
        build_output(made, DeadlockOutput)
    assert not verifier.verify_task("fix-deadlock", sample(), None, None, "")["valid"]
    assert not verifier.verify_input("unknown", sample())["valid"]
    assert not verifier.verify_task("unknown", sample(), None, None, "")["valid"]
    assert not verifier.verify_output("unknown", sample(), None, "")["valid"]


def test_more_than_one_request_is_rejected_by_the_typed_contract():
    raw = plain_output(sample())
    raw["threads"][0]["waiting_for"] = ["L1", "L2"]
    with pytest.raises((ValueError, TypeError)):
        build_output(raw, DeadlockInput)


@pytest.mark.parametrize("field", ["plan", "total_rollback_cost"])
def test_unrecoverable_requires_null_not_empty_or_zero(field, tmp_path):
    given = sample("unrecoverable")
    made = resolve_deadlock(given, tmp_path)
    wrong = RecoveryPlan([], [], made.wait_for_edges) if field == "plan" else 0
    assert not verifier.verify_task("fix-deadlock", given, replace(made, **{field: wrong}), None, "")["valid"]


def test_no_deadlock_does_not_allow_an_unnecessary_rollback(tmp_path):
    given = sample("free-request")
    made = resolve_deadlock(given, tmp_path)
    wrong = replace(made, plan=RecoveryPlan(["A"], [], []), total_rollback_cost=1)
    assert not verifier.verify_task("fix-deadlock", given, wrong, None, "")["valid"]


def test_all_4096_abortable_subsets_are_actually_enumerated(monkeypatch):
    checker = importlib.import_module("verifier.audit")
    threads, matrix, _ = checker.inspect(sample("maximum-snapshot"))
    original = checker.acyclic
    seen = []

    def count(matrix, removed):
        seen.append(tuple(sorted(removed)))
        return original(matrix, removed)

    monkeypatch.setattr(checker, "acyclic", count)
    best = checker.optimal_subset(threads, matrix)
    assert best == (78, tuple(t.id for t in threads))
    assert len(seen) == len(set(seen)) == 4096


def test_verifier_does_not_trust_solver_cycle_detection(monkeypatch, tmp_path):
    engine = importlib.import_module("plugin.doctor")
    monkeypatch.setattr(engine, "cycles_of", lambda successor: [])
    given = sample()
    forged = engine.resolve_deadlock(given, tmp_path)
    assert forged.status == DeadlockStatus.NO_DEADLOCK
    assert not verifier.verify_task("fix-deadlock", given, forged, None, "")["valid"]
