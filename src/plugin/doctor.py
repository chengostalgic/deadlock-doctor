"""Functional wait-for graph diagnosis and cheapest-per-disjoint-cycle recovery."""

from pathlib import Path

from contract import DeadlockCycle, DeadlockInput, DeadlockOutput, DeadlockStatus, RecoveryPlan, WaitForEdge


def identifier(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(label + " must be a nonblank string")


def validate(snapshot):
    if not 1 <= len(snapshot.threads) <= 12:
        raise ValueError("snapshot must contain 1 to 12 threads")
    if len(snapshot.locks) > 24:
        raise ValueError("snapshot supports at most 24 locks")
    for lock in snapshot.locks:
        identifier(lock, "lock ID")
    if len(set(snapshot.locks)) != len(snapshot.locks):
        raise ValueError("duplicate lock ID in complete lock list")
    known, threads, owners = set(snapshot.locks), {}, {}
    for thread in snapshot.threads:
        identifier(thread.id, "thread ID")
        if thread.id in threads:
            raise ValueError("duplicate thread ID: " + thread.id)
        threads[thread.id] = thread
        if type(thread.can_abort) is not bool:
            raise ValueError("can_abort must be an explicit boolean for thread " + thread.id)
        if type(thread.rollback_cost) is not int or thread.rollback_cost <= 0:
            raise ValueError("rollback_cost must be a positive integer for thread " + thread.id)
        held = set()
        for lock in thread.held_locks:
            identifier(lock, "held lock ID")
            if lock not in known:
                raise ValueError("unknown held lock " + lock + " for thread " + thread.id)
            if lock in held:
                raise ValueError("duplicate held-lock entry " + lock + " for thread " + thread.id)
            held.add(lock)
            if lock in owners:
                raise ValueError(
                    "exclusive ownership violated: " + lock + " is held by " + owners[lock] + " and " + thread.id
                )
            owners[lock] = thread.id
        if thread.waiting_for is not None:
            identifier(thread.waiting_for, "requested lock ID")
            if thread.waiting_for not in known:
                raise ValueError("unknown requested lock " + thread.waiting_for + " for thread " + thread.id)
    return threads, owners


def edges(threads, owners, removed):
    result = []
    for name in sorted(threads):
        thread = threads[name]
        holder = owners.get(thread.waiting_for)
        if name not in removed and holder is not None and holder not in removed:
            result.append(WaitForEdge(name, holder, thread.waiting_for))
    return result


def cycles_of(successor):
    completed, cycles = set(), []
    for start in sorted(successor):
        path, offsets, current = [], {}, start
        while current in successor and current not in completed and current not in offsets:
            offsets[current] = len(path)
            path.append(current)
            current = successor[current]
        if current in offsets:
            cycle = path[offsets[current] :]
            pivot = cycle.index(min(cycle))
            cycles.append(cycle[pivot:] + cycle[:pivot])
        completed.update(path)
    return sorted(cycles)


def resolve_deadlock(value: DeadlockInput, workspace: Path) -> DeadlockOutput:
    threads, owners = validate(value)
    original = edges(threads, owners, set())
    successor = {edge.waiter: edge.holder for edge in original}
    cycles = cycles_of(successor)
    members = {name for cycle in cycles for name in cycle}
    blocked = []
    for name in sorted(set(threads) - members):
        current, seen = name, set()
        while current in successor and current not in members and current not in seen:
            seen.add(current)
            current = successor[current]
        if current in members:
            blocked.append(name)
    cycle_rows = [DeadlockCycle(cycle) for cycle in cycles]
    if not cycles:
        return DeadlockOutput(
            value,
            DeadlockStatus.NO_DEADLOCK,
            original,
            [],
            [],
            [],
            RecoveryPlan([], [], list(original)),
            0,
            "No deadlock: the snapshot has no directed cycle; no rollback is needed (total cost 0).",
        )
    if any(not any(threads[name].can_abort for name in cycle) for cycle in cycles):
        return DeadlockOutput(
            value,
            DeadlockStatus.UNRECOVERABLE,
            original,
            cycle_rows,
            sorted(members),
            blocked,
            None,
            None,
            "Unrecoverable under this model: at least one cycle has no abortable member; no complete plan or cost is available.",
        )
    victims = sorted(
        min((name for name in cycle if threads[name].can_abort), key=lambda name: (threads[name].rollback_cost, name))
        for cycle in cycles
    )
    removed = set(victims)
    released = sorted(lock for lock, owner in owners.items() if owner in removed)
    remaining = edges(threads, owners, removed)
    total = sum(threads[name].rollback_cost for name in victims)
    explanation = (
        "Rollback "
        + ", ".join(victims)
        + " for total cost "
        + str(total)
        + "; release "
        + ", ".join(released)
        + " to break every current cycle."
    )
    return DeadlockOutput(
        value,
        DeadlockStatus.RECOVERY_PLAN,
        original,
        cycle_rows,
        sorted(members),
        blocked,
        RecoveryPlan(victims, released, remaining),
        total,
        explanation,
    )
