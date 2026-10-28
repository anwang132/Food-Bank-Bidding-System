import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";

import { api } from "../api/client";
import type { RoundStatus, RoundSummary } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import {
  Empty,
  ErrorState,
  Loading,
  PageHeader,
  StatusBadge,
  TableRegion,
  formatDateTime,
  relativeDeadline,
} from "../components/ui";

type Filter = "active" | RoundStatus | "all";

const FILTERS: { key: Filter; label: string; match: (r: RoundSummary) => boolean }[] = [
  { key: "active", label: "Active", match: (r) => ["draft", "open", "closed"].includes(r.status) },
  { key: "allocated", label: "Allocated", match: (r) => r.status === "allocated" },
  { key: "cancelled", label: "Cancelled", match: (r) => r.status === "cancelled" },
  { key: "all", label: "All", match: () => true },
];

export function RoundsPage() {
  const { me } = useAuth();
  const isAdmin = me?.user.role === "admin";
  const [filter, setFilter] = useState<Filter>("active");
  const q = useQuery({ queryKey: ["rounds"], queryFn: () => api<RoundSummary[]>("/api/rounds") });
  const current = FILTERS.find((f) => f.key === filter)!;
  const rows = (q.data ?? []).filter(current.match);

  return (
    <section>
      <PageHeader
        title="Distribution rounds"
        subtitle={
          isAdmin
            ? "Each round offers reserved inventory for a limited time, then allocates it with one strategy."
            : "Request inventory from open rounds and see how finished rounds were allocated."
        }
        actions={
          isAdmin && (
            <Link className="btn btn-primary" to="/admin/rounds/new">
              New round
            </Link>
          )
        }
      />
      <div className="segmented" role="group" aria-label="Filter rounds">
        {FILTERS.map((f) => (
          <button
            key={f.key}
            type="button"
            aria-pressed={filter === f.key}
            onClick={() => setFilter(f.key)}
          >
            {f.label}
            <span className="count">{(q.data ?? []).filter(f.match).length}</span>
          </button>
        ))}
      </div>
      {q.isPending ? (
        <Loading />
      ) : q.isError ? (
        <ErrorState error={q.error} onRetry={() => q.refetch()} />
      ) : rows.length === 0 ? (
        <Empty>
          {filter === "active" && isAdmin
            ? "No active rounds. Create one to start distributing inventory."
            : filter === "active"
              ? "No rounds are open yet. New rounds will appear here automatically."
              : "Nothing here yet."}
        </Empty>
      ) : (
        <div className="card card-flush">
          <TableRegion label="Rounds">
            <table>
              <thead>
                <tr>
                  <th scope="col">Round</th>
                  <th scope="col">Status</th>
                  <th scope="col">Strategy</th>
                  <th scope="col" className="num">
                    Items
                  </th>
                  <th scope="col">Deadline</th>
                  {!isAdmin && (
                    <th scope="col" className="num">
                      Your requests
                    </th>
                  )}
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.id}>
                    <td>
                      <Link to={`/rounds/${r.id}`} className="row-title">
                        {r.name}
                      </Link>
                      {r.description && (
                        <small className="muted block clamp">{r.description}</small>
                      )}
                    </td>
                    <td>
                      <StatusBadge status={r.status} />
                    </td>
                    <td>{r.strategy_label}</td>
                    <td className="num">
                      {r.lot_count}
                      <small className="muted block">
                        {r.total_quantity.toLocaleString()} units
                      </small>
                    </td>
                    <td>
                      {formatDateTime(r.request_deadline)}
                      {r.status === "open" && (
                        <small className="muted block">
                          {relativeDeadline(r.request_deadline)}
                        </small>
                      )}
                    </td>
                    {!isAdmin && <td className="num">{r.my_request_count ?? 0}</td>}
                  </tr>
                ))}
              </tbody>
            </table>
          </TableRegion>
        </div>
      )}
    </section>
  );
}
