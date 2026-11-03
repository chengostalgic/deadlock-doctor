"""Exclusive non-reentrant mutex snapshots and minimum-cost hypothetical recovery."""

from dataclasses import dataclass
from enum import Enum
from typing import Annotated

from slimconfig import Output


class Tool(Enum):
    RESOLVE_DEADLOCK = "resolve_deadlock"


class Task(Enum):
    FIX_DEADLOCK = "fix-deadlock"


class DeadlockStatus(Enum):
    NO_DEADLOCK = "no_deadlock"
    RECOVERY_PLAN = "recovery_plan"
    UNRECOVERABLE = "unrecoverable_under_model"


@dataclass
class ThreadState(Output):
    id: Annotated[str, "Unique nonblank, case-sensitive thread ID; preserve the user's identifier exactly"]
    held_locks: Annotated[list[str], "Every lock currently held; no duplicates; each lock has at most one owner"]
    waiting_for: Annotated[
        str | None,
        "At most one requested lock, or null if none. A free requested lock creates no wait edge. "
        "A request for an already-held lock is a self-deadlock: mutexes are non-reentrant.",
    ]
    can_abort: Annotated[
        bool,
        "Explicit scenario permission for safe rollback and release of ALL this thread's locks. Never infer it.",
    ]
    rollback_cost: Annotated[int, "Explicit positive integer rollback cost, required even for protected threads"]


@dataclass
class DeadlockInput(Output):
    locks: Annotated[list[str], "Complete list of 0 to 24 unique nonblank lock IDs, including unowned locks"]
    threads: Annotated[
        list[ThreadState],
        "Complete snapshot of 1 to 12 distinct threads. Held locks persist while blocked. "
        "Every thread's held locks, request/null, rollback permission, and positive cost must be supplied.",
    ]


@dataclass
class WaitForEdge(Output):
    waiter: str
    holder: str
    lock: Annotated[str, "The requested mutex whose owner causes this wait-for edge"]


@dataclass
class DeadlockCycle(Output):
    members: Annotated[
        list[str],
        "Cycle in edge direction, rotated to its lexicographically smallest member; no repeated closing member. "
        "A one-element list denotes a self-cycle. Cycle lists are sorted lexicographically.",
    ]


@dataclass
class RecoveryPlan(Output):
    victims: Annotated[
        list[str], "Sorted abortable thread IDs; minimum total cost, then lexicographically smallest tuple"
    ]
    released_locks: Annotated[list[str], "Sorted list of ALL locks held by the victims, not just their requested locks"]
    remaining_edges: Annotated[
        list[WaitForEdge],
        "Recomputed after removing victims and releasing their locks. No subsequent lock acquisitions are invented.",
    ]


@dataclass
class DeadlockOutput(Output):
    snapshot: Annotated[DeadlockInput, "Exact original task input, with original list order preserved"]
    status: DeadlockStatus
    wait_for_edges: Annotated[list[WaitForEdge], "Original edges sorted by (waiter, holder, lock)"]
    cycles: list[DeadlockCycle]
    cycle_members: Annotated[list[str], "Sorted union of all actual cycle members"]
    blocked_behind_cycles: Annotated[list[str], "Sorted non-cycle threads whose wait chain reaches a cycle"]
    plan: Annotated[
        RecoveryPlan | None,
        "No deadlock: empty victims/releases and unchanged remaining graph. Recovery: complete optimal plan. "
        "Unrecoverable: null, never a partial fix presented as a complete plan.",
    ]
    total_rollback_cost: Annotated[
        int | None, "0 for no deadlock; positive optimal cost for recovery; null if unrecoverable"
    ]
    explanation: Annotated[str, "Short deterministic verified summary, including the exact decimal cost when available"]


TOOLS = {Tool.RESOLVE_DEADLOCK: (DeadlockInput, DeadlockOutput)}
TASKS = {Task.FIX_DEADLOCK: (DeadlockInput, DeadlockOutput)}
