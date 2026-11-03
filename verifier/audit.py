"""Independent input audit, adjacency-matrix closure, and exhaustive subset optimality.

This module never imports the solver. Cycle detection uses transitive closure
and mutual reachability; recovery checks enumerate all abortable subsets and
use Kahn elimination, rather than choosing a cheapest member of each cycle.
"""

from contract import DeadlockCycle, DeadlockStatus, RecoveryPlan, WaitForEdge


def require(condition, message):
    if not condition:
        raise ValueError(message)


def nonblank(value):
    return isinstance(value, str) and bool(value.strip())


def inspect(snapshot):
    require(1 <= len(snapshot.threads) <= 12, "expected 1 to 12 threads")
    require(len(snapshot.locks) <= 24, "expected at most 24 locks")
    require(all(nonblank(lock) for lock in snapshot.locks), "lock IDs must be nonblank strings")
    require(len(set(snapshot.locks)) == len(snapshot.locks), "duplicate lock ID")
    identifiers = [thread.id for thread in snapshot.threads]
    require(all(nonblank(name) for name in identifiers), "thread IDs must be nonblank strings")
    require(len(set(identifiers)) == len(identifiers), "duplicate thread ID")
    threads = sorted(snapshot.threads, key=lambda thread: thread.id)
    known, owners = set(snapshot.locks), {}
    for index, thread in enumerate(threads):
        require(type(thread.can_abort) is bool, "can_abort must be an explicit boolean")
        require(
            type(thread.rollback_cost) is int and thread.rollback_cost > 0, "rollback cost must be a positive integer"
        )
        require(all(nonblank(lock) for lock in thread.held_locks), "held lock IDs must be nonblank strings")
        require(
            len(set(thread.held_locks)) == len(thread.held_locks), "duplicate held-lock entry in thread " + thread.id
        )
        for lock in thread.held_locks:
            require(lock in known, "unknown held lock: " + lock)
            require(lock not in owners, "exclusive ownership violated for lock " + lock)
            owners[lock] = index
        require(
            thread.waiting_for is None or (nonblank(thread.waiting_for) and thread.waiting_for in known),
            "unknown or invalid requested lock",
        )
    size = len(threads)
    matrix = [[False] * size for _ in threads]
    edges = []
    for index, thread in enumerate(threads):
        if thread.waiting_for in owners:
            holder = owners[thread.waiting_for]
            matrix[index][holder] = True
            edges.append(WaitForEdge(thread.id, threads[holder].id, thread.waiting_for))
    return threads, matrix, edges


def components(threads, matrix):
    size = len(threads)
    reachable = [list(row) for row in matrix]
    for intermediate in range(size):
        for source in range(size):
            for destination in range(size):
                reachable[source][destination] = reachable[source][destination] or (
                    reachable[source][intermediate] and reachable[intermediate][destination]
                )
    cyclic = {index for index in range(size) if reachable[index][index]}
    remaining, cycles = set(cyclic), []
    while remaining:
        first = min(remaining)
        component = {index for index in remaining if reachable[first][index] and reachable[index][first]}
        route, cursor = [], first
        while cursor not in route:
            require(cursor in component, "cyclic component is not a functional cycle")
            route.append(cursor)
            cursor = matrix[cursor].index(True)
        require(cursor == first and set(route) == component, "cycle direction/component mismatch")
        cycles.append(DeadlockCycle([threads[index].id for index in route]))
        remaining.difference_update(component)
    cycles.sort(key=lambda cycle: cycle.members)
    members = sorted(threads[index].id for index in cyclic)
    blocked = [
        threads[index].id
        for index in range(size)
        if index not in cyclic and any(reachable[index][member] for member in cyclic)
    ]
    return cycles, members, blocked


def acyclic(matrix, removed):
    active = [index for index in range(len(matrix)) if index not in removed]
    incoming = {target: sum(matrix[source][target] for source in active) for target in active}
    ready = [index for index in active if incoming[index] == 0]
    processed = 0
    while ready:
        source = ready.pop()
        processed += 1
        for target in active:
            if matrix[source][target]:
                incoming[target] -= 1
                if incoming[target] == 0:
                    ready.append(target)
    return processed == len(active)


def optimal_subset(threads, matrix):
    eligible = [index for index, thread in enumerate(threads) if thread.can_abort]
    best = None
    # At most 2**12 = 4096 candidates, including the empty set. No cycle shortcut.
    for mask in range(1 << len(eligible)):
        chosen = [index for bit, index in enumerate(eligible) if mask & (1 << bit)]
        if acyclic(matrix, set(chosen)):
            cost = sum(threads[index].rollback_cost for index in chosen)
            score = (cost, tuple(threads[index].id for index in chosen))
            if best is None or score < best:
                best = score
    return best


def audit(given, made):
    threads, matrix, original = inspect(given)
    cycles, members, blocked = components(threads, matrix)
    require(made.snapshot == given, "returned snapshot differs from the authoritative task input")
    require(made.wait_for_edges == original, "original wait-for edges or responsible locks are incorrect or unordered")
    require(made.cycles == cycles, "cycles are missing, duplicated, incorrectly directed, or noncanonical")
    require(made.cycle_members == members, "cycle membership is incorrect")
    require(made.blocked_behind_cycles == blocked, "threads merely blocked behind cycles are misclassified")
    best = optimal_subset(threads, matrix)
    if best is None:
        require(made.status == DeadlockStatus.UNRECOVERABLE, "snapshot has no complete abortable recovery")
        require(made.plan is None and made.total_rollback_cost is None, "unrecoverable requires null plan and cost")
        explanation = "Unrecoverable under this model: at least one cycle has no abortable member; no complete plan or cost is available."
    else:
        cost, chosen = best
        expected_status = DeadlockStatus.RECOVERY_PLAN if cycles else DeadlockStatus.NO_DEADLOCK
        require(made.status == expected_status, "reported deadlock status is incorrect")
        require(made.plan is not None, "a recoverable or acyclic snapshot must have a plan object")
        require(
            made.plan.victims == list(chosen),
            "victims violate minimum cost, rollback permission, ordering, or tie-break",
        )
        require(
            type(made.total_rollback_cost) is int and made.total_rollback_cost == cost,
            "total rollback cost is incorrect",
        )
        removed = set(chosen)
        released = sorted(lock for thread in threads if thread.id in removed for lock in thread.held_locks)
        # Rebuild ownership from surviving threads, rather than editing the reported graph.
        owners = {lock: thread.id for thread in threads if thread.id not in removed for lock in thread.held_locks}
        surviving = [thread for thread in threads if thread.id not in removed]
        remaining = [
            WaitForEdge(thread.id, owners[thread.waiting_for], thread.waiting_for)
            for thread in surviving
            if thread.waiting_for in owners
        ]
        require(
            made.plan == RecoveryPlan(list(chosen), released, remaining),
            "released locks or recomputed remaining edges are incorrect",
        )
        indices = {index for index, thread in enumerate(threads) if thread.id in removed}
        require(all(thread.can_abort for thread in threads if thread.id in removed), "a victim is protected")
        require(acyclic(matrix, indices), "recovery still contains a directed cycle")
        if not cycles:
            explanation = "No deadlock: the snapshot has no directed cycle; no rollback is needed (total cost 0)."
        else:
            explanation = (
                "Rollback "
                + ", ".join(chosen)
                + " for total cost "
                + str(cost)
                + "; release "
                + ", ".join(released)
                + " to break every current cycle."
            )
    require(made.explanation == explanation, "explanation does not match the verified result")
    return best
