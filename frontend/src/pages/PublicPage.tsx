import { useQuery } from "@tanstack/react-query";

import { api } from "../api/client";
import type { PublicRound } from "../api/types";
import {
  Empty,
  ErrorState,
  Loading,
  PageHeader,
  Progress,
  StatCard,
  StatusBadge,
  TableRegion,
  formatDateTime,
} from "../components/ui";

export function PublicPage() {
  const q = useQuery({
    queryKey: ["public"],
    queryFn: () => api<PublicRound[]>("/api/public/rounds"),
  });
  const rounds = q.data ?? [];
  const distributed = rounds
    .flatMap((r) => r.lots)
    .reduce((n, l) => n + (l.allocated_total ?? 0), 0);

  return (
    <section>
      <PageHeader
        title="Public summary"
        subtitle="Inventory offered in each round and aggregate outcomes. Individual requests and organization details are never shown here."
      />
      {q.isPending ? (
        <Loading />
      ) : q.isError ? (
        <ErrorState error={q.error} onRetry={() => q.refetch()} />
      ) : rounds.length === 0 ? (
        <Empty>No rounds have been published yet.</Empty>
      ) : (
        <>
          <div className="stats-grid">
            <StatCard label="Rounds published" value={rounds.length} />
            <StatCard
              label="Open now"
              value={rounds.filter((r) => r.status === "open").length}
              hint="accepting requests"
            />
            <StatCard
              label="Units distributed"
              value={distributed.toLocaleString()}
              tone="good"
              hint="in allocated rounds"
            />
          </div>
          <div className="stack">
            {rounds.map((r) => (
              <article key={r.id} className="card">
                <div className="card-header">
                  <div>
                    <h2>{r.name}</h2>
                    <p className="muted card-description">
                      {r.strategy_label} · request deadline {formatDateTime(r.request_deadline)}
                    </p>
                  </div>
                  <StatusBadge status={r.status} />
                </div>
                <TableRegion label={`Lots in ${r.name}`}>
                  <table>
                    <thead>
                      <tr>
                        <th scope="col">Item</th>
                        <th scope="col" className="num">
                          Offered
                        </th>
                        <th scope="col">Allocated</th>
                        <th scope="col" className="num">
                          Organizations served
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {r.lots.map((l) => (
                        <tr key={l.name}>
                          <td className="row-title">{l.name}</td>
                          <td className="num">
                            {l.quantity} {l.unit}
                          </td>
                          <td className="alloc-cell">
                            {l.allocated_total === null ? (
                              <span className="muted">Not yet allocated</span>
                            ) : (
                              <>
                                <span>
                                  {l.allocated_total} {l.unit}
                                </span>
                                <Progress
                                  value={l.allocated_total}
                                  max={l.quantity}
                                  label={`${l.name}: ${l.allocated_total} of ${l.quantity} allocated`}
                                />
                              </>
                            )}
                          </td>
                          <td className="num">{l.organizations_served ?? "—"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </TableRegion>
              </article>
            ))}
          </div>
        </>
      )}
    </section>
  );
}
