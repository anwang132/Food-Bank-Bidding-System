import { useInfiniteQuery } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "../../api/client";
import type { AuditEntry } from "../../api/types";
import {
  Empty,
  ErrorState,
  Field,
  Loading,
  TableRegion,
  formatDateTime,
  PageHeader,
} from "../../components/ui";

const PAGE = 50;

function Diff({ before, after }: { before: AuditEntry["before"]; after: AuditEntry["after"] }) {
  const keys = Array.from(new Set([...Object.keys(before ?? {}), ...Object.keys(after ?? {})]));
  if (keys.length === 0) return <span className="muted">—</span>;
  return (
    <ul className="diff">
      {keys.map((k) => {
        const b = before?.[k];
        const a = after?.[k];
        const fmt = (v: unknown) => (v === undefined ? "—" : JSON.stringify(v));
        return (
          <li key={k}>
            <code>{k}</code>: {before ? `${fmt(b)} → ` : ""}
            {fmt(a)}
          </li>
        );
      })}
    </ul>
  );
}

export function AuditPage() {
  const [action, setAction] = useState("");
  const [targetType, setTargetType] = useState("");
  const [roundId, setRoundId] = useState("");
  const q = useInfiniteQuery({
    queryKey: ["admin", "audit", action, targetType, roundId],
    initialPageParam: null as number | null,
    queryFn: ({ pageParam }) => {
      const p = new URLSearchParams({ limit: String(PAGE) });
      if (action) p.set("action", action);
      if (targetType) p.set("target_type", targetType);
      if (roundId) p.set("round_id", roundId);
      if (pageParam) p.set("before_id", String(pageParam));
      return api<AuditEntry[]>(`/api/admin/audit?${p}`);
    },
    getNextPageParam: (last) => (last.length === PAGE ? last[last.length - 1]!.id : null),
  });
  const rows = q.data?.pages.flat() ?? [];

  return (
    <section>
      <PageHeader
        title="Audit log"
        subtitle="Who changed consequential data, when, what changed, and why."
      />
      <form className="inline-form card" role="search" onSubmit={(e) => e.preventDefault()}>
        <Field label="Action">
          {(id) => (
            <select id={id} value={action} onChange={(e) => setAction(e.target.value)}>
              <option value="">Any</option>
              {[
                "round.create",
                "round.update",
                "round.open",
                "round.close",
                "round.cancel",
                "round.finalize",
                "round.lot_reserve",
                "round.lot_change",
                "round.lot_release",
                "round.participant_set",
                "request.create",
                "request.update",
                "request.withdraw",
                "lot.create",
                "lot.update",
                "organization.create",
                "organization.update",
                "user.create",
                "user.update",
                "planning.preferences_update",
                "planning.document_upload",
                "planning.note_create",
                "planning.document_delete",
                "chat.message_moderate",
              ].map((a) => (
                <option key={a}>{a}</option>
              ))}
            </select>
          )}
        </Field>
        <Field label="Target type">
          {(id) => (
            <select id={id} value={targetType} onChange={(e) => setTargetType(e.target.value)}>
              <option value="">Any</option>
              {[
                "round",
                "round_lot",
                "participant",
                "request",
                "lot",
                "organization",
                "user",
                "planning_document",
                "chat_message",
              ].map((t) => (
                <option key={t}>{t}</option>
              ))}
            </select>
          )}
        </Field>
        <Field label="Round id">
          {(id) => (
            <input
              id={id}
              className="narrow"
              inputMode="numeric"
              value={roundId}
              onChange={(e) => setRoundId(e.target.value.replace(/\D/g, ""))}
            />
          )}
        </Field>
      </form>
      {q.isPending ? (
        <Loading />
      ) : q.isError ? (
        <ErrorState error={q.error} onRetry={() => q.refetch()} />
      ) : rows.length === 0 ? (
        <Empty>No matching audit records.</Empty>
      ) : (
        <>
          <TableRegion label="Audit records">
            <table>
              <thead>
                <tr>
                  <th scope="col">When</th>
                  <th scope="col">Who</th>
                  <th scope="col">Action</th>
                  <th scope="col">Target</th>
                  <th scope="col">Change</th>
                  <th scope="col">Reason</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((a) => (
                  <tr key={a.id}>
                    <td>{formatDateTime(a.created_at)}</td>
                    <td>
                      {a.actor_name ?? "System"}
                      {a.actor_email && <small className="muted block">{a.actor_email}</small>}
                    </td>
                    <td>
                      <code>{a.action}</code>
                    </td>
                    <td>
                      {a.target_type} {a.target_id}
                    </td>
                    <td>
                      <Diff before={a.before} after={a.after} />
                    </td>
                    <td>{a.reason ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableRegion>
          {q.hasNextPage && (
            <button
              type="button"
              className="btn btn-secondary"
              disabled={q.isFetchingNextPage}
              onClick={() => q.fetchNextPage()}
            >
              {q.isFetchingNextPage ? "Loading…" : "Load older entries"}
            </button>
          )}
        </>
      )}
    </section>
  );
}
