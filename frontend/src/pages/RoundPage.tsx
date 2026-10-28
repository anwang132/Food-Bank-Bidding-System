import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import type { FormEvent } from "react";
import { Link, useParams } from "react-router-dom";

import { ApiError, api, errorMessage, newIdempotencyKey } from "../api/client";
import { STRATEGY_HELP } from "../api/types";
import type { RequestItem, RoundDetail, RoundLot } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import {
  Alert,
  Card,
  ConfirmButton,
  ErrorState,
  Loading,
  PageHeader,
  StatusBadge,
  TableRegion,
  formatDate,
  formatDateTime,
  relativeDeadline,
} from "../components/ui";
import { AdminPanels } from "./round/AdminPanels";

type Notice = { kind: "success" | "error" | "warning"; text: string } | null;

export function RoundPage() {
  const id = Number(useParams().id);
  const { me } = useAuth();
  const q = useQuery({
    queryKey: ["round", id],
    queryFn: () => api<RoundDetail>(`/api/rounds/${id}`),
    enabled: Number.isFinite(id),
  });
  if (q.isPending) return <Loading />;
  if (q.isError) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  const round = q.data;
  const isAdmin = me?.user.role === "admin";

  return (
    <section>
      <PageHeader
        title={round.name}
        badge={<StatusBadge status={round.status} />}
        subtitle={round.description || undefined}
        breadcrumb={<Link to="/rounds">Distribution rounds</Link>}
      />
      <div className="card meta-card">
        <dl className="meta">
          <div>
            <dt>Allocation strategy</dt>
            <dd>{round.strategy_label}</dd>
          </div>
          <div>
            <dt>Request deadline</dt>
            <dd>
              {formatDateTime(round.request_deadline)}
              {round.status === "open" && (
                <small
                  className={`block deadline ${round.deadline_passed ? "deadline-passed" : ""}`}
                >
                  {relativeDeadline(round.request_deadline)}
                </small>
              )}
            </dd>
          </div>
          <div>
            <dt>Inventory offered</dt>
            <dd>
              {round.lot_count} items · {round.total_quantity.toLocaleString()} units
            </dd>
          </div>
          {round.participation && (
            <div>
              <dt>Your organization</dt>
              <dd>
                {round.participation.eligible ? "Eligible" : "Not eligible"}
                {round.strategy === "priority" &&
                  ` · priority tier ${round.participation.priority}`}
                {round.strategy === "need_weighted" &&
                  ` · need weight ${round.participation.weight}`}
              </dd>
            </div>
          )}
        </dl>
        <details className="help">
          <summary>How {round.strategy_label.toLowerCase()} works</summary>
          <p>{STRATEGY_HELP[round.strategy]}</p>
        </details>
      </div>

      {round.status === "allocated" && !isAdmin && (
        <Alert kind="success">
          Allocation is final. <Link to={`/rounds/${round.id}/results`}>View results</Link>
        </Alert>
      )}
      {round.status === "allocated" && isAdmin && (
        <Alert kind="success">Allocation is final. Results are visible to members.</Alert>
      )}
      {round.status === "closed" && !isAdmin && (
        <Alert kind="info">
          Requests are closed and frozen. Results will appear here once the allocation is final.
        </Alert>
      )}
      {round.status === "cancelled" && (
        <Alert kind="warning">This round was cancelled and its inventory returned to stock.</Alert>
      )}

      {isAdmin ? <AdminPanels round={round} /> : <MemberLots round={round} />}
    </section>
  );
}

function MemberLots({ round }: { round: RoundDetail }) {
  const [notice, setNotice] = useState<Notice>(null);
  const eligible = round.participation?.eligible ?? false;
  const canChange = round.status === "open" && !round.deadline_passed && eligible;
  const active = new Map(
    round.requests.filter((r) => r.status === "active").map((r) => [r.lot_id, r]),
  );

  return (
    <Card
      title="Available inventory"
      id="lots"
      description={canChange ? "Enter a whole quantity for any item you need." : undefined}
    >
      {round.status === "open" && !eligible && (
        <Alert kind="warning">
          Your organization is not eligible to request from this round. Contact the administrator if
          this seems wrong.
        </Alert>
      )}
      {round.status === "open" && round.deadline_passed && (
        <Alert kind="warning">
          The request deadline has passed; requests can no longer change.
        </Alert>
      )}
      {notice && <Alert kind={notice.kind}>{notice.text}</Alert>}
      {round.lots.length === 0 ? (
        <p className="empty">No inventory has been added to this round.</p>
      ) : (
        <TableRegion label="Inventory in this round">
          <table>
            <thead>
              <tr>
                <th scope="col">Item</th>
                <th scope="col" className="num">
                  Offered
                </th>
                <th scope="col">Best before</th>
                <th scope="col">Your request</th>
              </tr>
            </thead>
            <tbody>
              {round.lots.map((lot) => (
                <tr key={lot.lot_id}>
                  <td>
                    <strong>{lot.name}</strong>
                    {lot.description && <small className="muted block">{lot.description}</small>}
                  </td>
                  <td className="num">
                    {lot.quantity} {lot.unit}
                  </td>
                  <td>{formatDate(lot.best_before)}</td>
                  <td>
                    <RequestCell
                      round={round}
                      lot={lot}
                      request={active.get(lot.lot_id)}
                      canChange={canChange}
                      onNotice={setNotice}
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableRegion>
      )}
    </Card>
  );
}

function RequestCell({
  round,
  lot,
  request,
  canChange,
  onNotice,
}: {
  round: RoundDetail;
  lot: RoundLot;
  request: RequestItem | undefined;
  canChange: boolean;
  onNotice: (n: Notice) => void;
}) {
  const qc = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [qty, setQty] = useState<string>(request ? String(request.quantity) : "");
  // One key per create intent: retries after a lost response replay, not duplicate.
  const createKey = useRef<string>(newIdempotencyKey());
  const refresh = () => qc.invalidateQueries({ queryKey: ["round", round.id] });

  const onError = (err: unknown) => {
    if (err instanceof ApiError && err.code === "VERSION_CONFLICT") {
      const d = err.details as { current_quantity?: number; status?: string } | undefined;
      onNotice({
        kind: "warning",
        text:
          d?.status === "withdrawn"
            ? `Your ${lot.name} request was withdrawn elsewhere. Nothing was changed.`
            : `Your ${lot.name} request was changed elsewhere (now ${d?.current_quantity} ${lot.unit}). Review it and try again.`,
      });
      setEditing(false);
      void refresh();
      return;
    }
    if (err instanceof ApiError && err.code === "REQUEST_EXISTS") {
      onNotice({
        kind: "warning",
        text: `Your organization already has a ${lot.name} request (perhaps from a colleague). It is shown below.`,
      });
      createKey.current = newIdempotencyKey();
      void refresh();
      return;
    }
    onNotice({ kind: "error", text: errorMessage(err) });
    if (!(err instanceof ApiError && err.isNetwork)) void refresh();
  };

  const create = useMutation({
    mutationFn: (quantity: number) =>
      api<RequestItem>(`/api/rounds/${round.id}/requests`, {
        method: "POST",
        body: { lot_id: lot.lot_id, quantity },
        idempotencyKey: createKey.current,
      }),
    onSuccess: (r) => {
      createKey.current = newIdempotencyKey();
      onNotice({ kind: "success", text: `Requested ${r.quantity} ${lot.unit} of ${lot.name}.` });
      setEditing(false);
      void refresh();
    },
    onError,
  });
  const update = useMutation({
    mutationFn: (quantity: number) =>
      api<RequestItem>(`/api/requests/${request!.id}`, {
        method: "PATCH",
        body: { quantity, version: request!.version },
      }),
    onSuccess: (r) => {
      onNotice({ kind: "success", text: `Updated ${lot.name} to ${r.quantity} ${lot.unit}.` });
      setEditing(false);
      void refresh();
    },
    onError,
  });
  const withdraw = useMutation({
    mutationFn: () =>
      api<RequestItem>(`/api/requests/${request!.id}/withdraw`, {
        method: "POST",
        body: { version: request!.version },
      }),
    onSuccess: () => {
      onNotice({ kind: "success", text: `Withdrew your ${lot.name} request.` });
      void refresh();
    },
    onError,
  });
  const busy = create.isPending || update.isPending || withdraw.isPending;

  const submit = (e: FormEvent) => {
    e.preventDefault();
    const n = Number(qty);
    if (!Number.isInteger(n) || n < 1) {
      onNotice({ kind: "error", text: "Enter a whole number of at least 1." });
      return;
    }
    if (request) update.mutate(n);
    else create.mutate(n);
  };

  if (editing || (!request && canChange)) {
    return (
      <form className="inline-form" onSubmit={submit} aria-label={`Request ${lot.name}`}>
        <label className="sr-only" htmlFor={`qty-${lot.lot_id}`}>
          Quantity of {lot.name} in {lot.unit}
        </label>
        <input
          id={`qty-${lot.lot_id}`}
          type="number"
          inputMode="numeric"
          min={1}
          step={1}
          required
          value={qty}
          onChange={(e) => setQty(e.target.value)}
          placeholder={lot.unit}
        />
        <button type="submit" className="btn btn-primary" disabled={busy}>
          {busy ? "Saving…" : request ? "Save" : "Request"}
        </button>
        {editing && (
          <button type="button" className="btn btn-secondary" onClick={() => setEditing(false)}>
            Cancel
          </button>
        )}
      </form>
    );
  }
  if (!request) return <span className="muted">No request</span>;
  return (
    <div className="request-cell">
      <span data-testid={`my-request-${lot.lot_id}`}>
        {request.quantity} {lot.unit}
        <small className="muted block">updated {formatDateTime(request.updated_at)}</small>
      </span>
      {canChange && (
        <span className="actions">
          <button
            type="button"
            className="btn btn-secondary"
            disabled={busy}
            onClick={() => {
              setQty(String(request.quantity));
              setEditing(true);
            }}
          >
            Edit
          </button>
          <ConfirmButton
            label="Withdraw"
            confirmLabel="Withdraw request"
            prompt="Withdraw? A later request counts as new and loses this one's place in line."
            className="btn btn-danger"
            busy={withdraw.isPending}
            onConfirm={() => withdraw.mutate()}
          />
        </span>
      )}
    </div>
  );
}
