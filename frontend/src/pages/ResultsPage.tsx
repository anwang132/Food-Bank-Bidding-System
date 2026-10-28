import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";

import { api } from "../api/client";
import { STRATEGY_HELP } from "../api/types";
import type { Allocation, RoundDetail } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { AllocationView } from "../components/AllocationView";
import { ErrorState, Loading, PageHeader, StatCard } from "../components/ui";

export function ResultsPage() {
  const id = Number(useParams().id);
  const { me } = useAuth();
  const q = useQuery({
    queryKey: ["results", id],
    queryFn: () => api<Allocation>(`/api/rounds/${id}/results`),
  });
  const round = useQuery({
    queryKey: ["round", id],
    queryFn: () => api<RoundDetail>(`/api/rounds/${id}`),
  });
  const isAdmin = me?.user.role === "admin";
  const lines = q.data?.lots.flatMap((l) => l.lines) ?? [];
  const requested = lines.reduce((n, l) => n + l.requested, 0);
  const received = lines.reduce((n, l) => n + l.allocated, 0);

  return (
    <section>
      <PageHeader
        title={round.data ? `Results: ${round.data.name}` : "Allocation results"}
        breadcrumb={
          <>
            <Link to="/rounds">Distribution rounds</Link>
            <span aria-hidden="true"> / </span>
            <Link to={`/rounds/${id}`}>{round.data?.name ?? "Round"}</Link>
          </>
        }
        subtitle={
          q.data &&
          `Decided by ${q.data.strategy_label} (version ${q.data.strategy_version}). ${STRATEGY_HELP[q.data.strategy]}`
        }
      />
      {q.isPending ? (
        <Loading />
      ) : q.isError ? (
        <ErrorState error={q.error} onRetry={() => q.refetch()} />
      ) : (
        <>
          <div className="stats-grid">
            <StatCard
              label={isAdmin ? "Total requested" : "Your organization requested"}
              value={requested.toLocaleString()}
              hint="units across all items"
            />
            <StatCard
              label={isAdmin ? "Total allocated" : "Your organization received"}
              value={received.toLocaleString()}
              tone="good"
              hint={
                requested ? `${Math.round((received / requested) * 100)}% of requested` : undefined
              }
            />
            <StatCard
              label="Returned to stock"
              value={q.data.lots.reduce((n, l) => n + l.unallocated, 0).toLocaleString()}
              hint="unallocated supply"
            />
          </div>
          {!isAdmin && (
            <p className="muted">
              You see your organization&apos;s requests only. Lot totals include all organizations.
            </p>
          )}
          <AllocationView allocation={q.data} showOrganizations={isAdmin} />
          <p className="muted mono small">
            Input {q.data.input_hash.slice(0, 12)}… · result {q.data.result_hash.slice(0, 12)}…
          </p>
        </>
      )}
    </section>
  );
}
