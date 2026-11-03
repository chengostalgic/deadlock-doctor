"""Exhaust every four-thread functional graph and rollback permission combination."""

import itertools

import verifier
from contract import DeadlockInput, DeadlockStatus, ThreadState
from plugin import resolve_deadlock


def cycle_oracle(ids, successors):
    cycles = []
    for length in range(1, len(ids) + 1):
        for route in itertools.permutations(range(len(ids)), length):
            names = [ids[i] for i in route]
            if names[0] != min(names):
                continue
            if all(successors[route[i]] == route[(i + 1) % length] for i in range(length)):
                cycles.append(names)
    return sorted(cycles)


def test_10000_graph_and_permission_snapshots(tmp_path):
    ids = ["z", "A", "T2", "T10"]
    locks = ["L0", "L1", "L2", "L3"]
    for targets in itertools.product([None, 0, 1, 2, 3], repeat=4):
        cycles = cycle_oracle(ids, targets)
        for permission in range(16):
            threads = [
                ThreadState(
                    ids[i],
                    [locks[i]],
                    None if targets[i] is None else locks[targets[i]],
                    bool(permission & (1 << i)),
                    1 + i % 2,
                )
                for i in range(4)
            ]
            given = DeadlockInput(locks, threads)
            made = resolve_deadlock(given, tmp_path)
            assert [c.members for c in made.cycles] == cycles
            candidates = []
            for bits in range(16):
                chosen = [i for i in range(4) if bits & (1 << i)]
                if any(not threads[i].can_abort for i in chosen):
                    continue
                if all(set(cycle) & {ids[i] for i in chosen} for cycle in cycles):
                    candidates.append(
                        (sum(threads[i].rollback_cost for i in chosen), tuple(sorted(ids[i] for i in chosen)))
                    )
            best = min(candidates) if candidates else None
            if best is None:
                assert made.status == DeadlockStatus.UNRECOVERABLE and made.plan is made.total_rollback_cost is None
            else:
                assert (made.total_rollback_cost, tuple(made.plan.victims)) == best
            assert verifier.verify_task("fix-deadlock", given, made, None, "")["valid"]
