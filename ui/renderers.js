import { dataTable, disclosure, h, statusPill } from "./kit/components.js";

const labels = { no_deadlock: "No deadlock", recovery_plan: "Minimum-cost recovery proposed", unrecoverable_under_model: "Unrecoverable under this model" };
const list = (values) => values.length ? values.join(", ") : "None";
const graph = (edges, caption) => edges.length ? dataTable({ columns: [
  { key: "waiter", label: "Waiting thread" }, { key: "lock", label: "Requested lock" }, { key: "holder", label: "Held by" },
], rows: edges, caption }).element : h("p", { text: "No wait-for edges." });

export default {
  DeadlockOutput: (value, { verdict }) => {
    if (verdict?.valid === false) return h("section", { class: "doctor" }, statusPill("Verification failed", "bad"), h("p", { text: verdict.message }));
    return h("section", { class: "doctor" },
      statusPill(verdict?.valid ? "Verified snapshot analysis" : "Snapshot analysis", "ok"),
      h("h3", { text: labels[value.status] }),
      // This verified text preserves exact integer costs even beyond JS's safe numeric range.
      h("p", { class: "summary", text: value.explanation }),
      h("h4", { text: "Deadlock cycles" }),
      value.cycles.length ? h("ol", {}, value.cycles.map((cycle) => h("li", { text: [...cycle.members, cycle.members[0]].join(" → ") + (cycle.members.length === 1 ? " (self-deadlock)" : "") }))) : h("p", { text: "No directed cycle." }),
      h("p", { text: "Cycle members: " + list(value.cycle_members) }),
      h("p", { text: "Blocked behind cycles, outside the cycles: " + list(value.blocked_behind_cycles) }),
      disclosure({ summary: "Original wait-for graph" }, graph(value.wait_for_edges, "Original snapshot edges")),
      value.plan === null ? h("p", { class: "unrecoverable", text: "No complete recovery plan or total cost is available. No partial fix is being recommended." }) : h("div", { class: "plan" },
        h("p", { text: "Recommended rollbacks: " + list(value.plan.victims) }),
        h("p", { text: "Released locks: " + list(value.plan.released_locks) }),
        disclosure({ summary: "Remaining wait-for graph" }, graph(value.plan.remaining_edges, "After removing victims and releasing their locks")),
      ),
      disclosure({ summary: "Input ownership and rollback permissions" },
        h("p", { text: "Complete lock list: " + list(value.snapshot.locks) }),
        dataTable({ columns: [
          { key: "id", label: "Thread" }, { key: "held", label: "Holds" }, { key: "request", label: "Requests" }, { key: "permission", label: "Safe rollback permitted" },
        ], rows: value.snapshot.threads.map((thread) => ({ id: thread.id, held: list(thread.held_locks), request: thread.waiting_for ?? "None", permission: thread.can_abort ? "Yes" : "No" })), caption: "Stated hypothetical facts" }).element,
      ),
      h("p", { class: "muted", text: "Hypothetical exclusive, non-reentrant mutexes. The proposal breaks current snapshot cycles only. Released locks remain free; no subsequent acquisitions are simulated. This does not establish race freedom, future deadlock prevention, or scheduling fairness." }),
    );
  },
};
