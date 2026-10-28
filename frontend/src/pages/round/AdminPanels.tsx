import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import type { FormEvent } from "react";
import { Link } from "react-router-dom";

import { api, errorMessage, newIdempotencyKey } from "../../api/client";
import { STRATEGY_HELP, STRATEGY_LABEL } from "../../api/types";
import type { Allocation, Lot, Participant, RoundDetail, Strategy } from "../../api/types";
import { AllocationView } from "../../components/AllocationView";
import {
  Alert,
  ConfirmButton,
  Empty,
  ErrorState,
  Field,
  Loading,
  TableRegion,
  formatDateTime,
} from "../../components/ui";

type Notice = { kind: "success" | "error"; text: string } | null;

function toLocalInput(iso: string): string {
  const d = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/** Mutation helper: invalidate the round on success; surface errors in a notice. */
function useRoundAction<TVars>(
  round: RoundDetail,
  fn: (vars: TVars) => Promise<unknown>,
  setNotice: (n: Notice) => void,
  success: string,
) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: () => {
      setNotice({ kind: "success", text: success });
      void qc.invalidateQueries({ queryKey: ["round", round.id] });
      void qc.invalidateQueries({ queryKey: ["rounds"] });
      void qc.invalidateQueries({ queryKey: ["admin", "lots"] });
    },
    onError: (err) => {
      setNotice({ kind: "error", text: errorMessage(err) });
      void qc.invalidateQueries({ queryKey: ["round", round.id] });
    },
  });
}

export function AdminPanels({ round }: { round: RoundDetail }) {
  const [notice, setNotice] = useState<Notice>(null);
  const editable = round.status === "draft" || round.status === "open";
  return (
    <div className="stack">
      {notice && <Alert kind={notice.kind}>{notice.text}</Alert>}
      <Lifecycle round={round} setNotice={setNotice} />
      {round.status === "closed" && <PreviewPanel round={round} setNotice={setNotice} />}
      {editable && <Settings round={round} setNotice={setNotice} />}
      <Lots round={round} setNotice={setNotice} editable={editable} />
      <Participants round={round} setNotice={setNotice} editable={editable} />
      <Requests round={round} />
      {round.snapshot && (
        <section className="card">
          <h2>Frozen input</h2>
          <p className="muted">
            Captured at {formatDateTime(round.snapshot.created_at)} ·{" "}
            {STRATEGY_LABEL[round.snapshot.strategy]} v{round.snapshot.strategy_version}
          </p>
          <p className="mono">input {round.snapshot.input_hash.slice(0, 16)}…</p>
          {round.snapshot.result_hash && (
            <p className="mono">result {round.snapshot.result_hash.slice(0, 16)}…</p>
          )}
        </section>
      )}
    </div>
  );
}

function Lifecycle({ round, setNotice }: { round: RoundDetail; setNotice: (n: Notice) => void }) {
  const [reason, setReason] = useState("");
  const transition = (path: string, body: unknown = {}) =>
    api(`/api/admin/rounds/${round.id}/${path}`, { method: "POST", body });
  const open = useRoundAction(
    round,
    () => transition("open"),
    setNotice,
    "Round opened for requests.",
  );
  const close = useRoundAction(
    round,
    () => transition("close"),
    setNotice,
    "Round closed. Requests, eligibility, weights and strategy are now frozen.",
  );
  const cancel = useRoundAction(
    round,
    () => transition("cancel", { reason }),
    setNotice,
    "Round cancelled and reserved inventory released.",
  );
  const cancellable = ["draft", "open", "closed"].includes(round.status);

  return (
    <section className="card" aria-labelledby="lifecycle">
      <h2 id="lifecycle">Round status</h2>
      <ol className="steps" aria-label="Round lifecycle">
        {(["draft", "open", "closed", "allocated"] as const).map((s) => (
          <li key={s} aria-current={round.status === s ? "step" : undefined}>
            {s}
          </li>
        ))}
      </ol>
      <div className="actions">
        {round.status === "draft" && (
          <ConfirmButton
            label="Open for requests"
            confirmLabel="Open round"
            prompt="Members will be able to submit requests."
            busy={open.isPending}
            disabled={round.lots.length === 0}
            onConfirm={() => open.mutate(undefined)}
          />
        )}
        {round.status === "open" && (
          <ConfirmButton
            label="Close requests"
            confirmLabel="Close round"
            prompt="This freezes all requests and settings."
            busy={close.isPending}
            onConfirm={() => close.mutate(undefined)}
          />
        )}
        {round.status === "allocated" && (
          <Link className="btn btn-primary" to={`/rounds/${round.id}/results`}>
            View results
          </Link>
        )}
      </div>
      {round.status === "draft" && round.lots.length === 0 && (
        <p className="muted">Add at least one lot before opening.</p>
      )}
      {cancellable && (
        <form
          className="inline-form"
          onSubmit={(e) => {
            e.preventDefault();
            cancel.mutate(undefined);
          }}
        >
          <label htmlFor="cancel-reason">Cancel reason</label>
          <input
            id="cancel-reason"
            value={reason}
            minLength={3}
            required
            onChange={(e) => setReason(e.target.value)}
          />
          <button type="submit" className="btn btn-danger" disabled={cancel.isPending}>
            Cancel round
          </button>
        </form>
      )}
    </section>
  );
}

function Settings({ round, setNotice }: { round: RoundDetail; setNotice: (n: Notice) => void }) {
  const [name, setName] = useState(round.name);
  const [description, setDescription] = useState(round.description);
  const [strategy, setStrategy] = useState<Strategy>(round.strategy);
  const [deadline, setDeadline] = useState(toLocalInput(round.request_deadline));
  const [reason, setReason] = useState("");
  const save = useRoundAction(
    round,
    () =>
      api(`/api/admin/rounds/${round.id}`, {
        method: "PATCH",
        body: {
          version: round.public_version,
          name,
          description,
          strategy,
          request_deadline: new Date(deadline).toISOString(),
          ...(reason ? { reason } : {}),
        },
      }),
    setNotice,
    "Round settings saved.",
  );
  return (
    <section className="card" aria-labelledby="settings">
      <h2 id="settings">Settings</h2>
      <form
        className="grid-form"
        onSubmit={(e: FormEvent) => {
          e.preventDefault();
          save.mutate(undefined);
        }}
      >
        <Field label="Name">
          {(id) => (
            <input id={id} value={name} required onChange={(e) => setName(e.target.value)} />
          )}
        </Field>
        <Field label="Request deadline">
          {(id) => (
            <input
              id={id}
              type="datetime-local"
              required
              value={deadline}
              onChange={(e) => setDeadline(e.target.value)}
            />
          )}
        </Field>
        <Field label="Strategy" hint={STRATEGY_HELP[strategy]}>
          {(id, hint) => (
            <select
              id={id}
              aria-describedby={hint}
              value={strategy}
              onChange={(e) => setStrategy(e.target.value as Strategy)}
            >
              {(Object.keys(STRATEGY_LABEL) as Strategy[]).map((s) => (
                <option key={s} value={s}>
                  {STRATEGY_LABEL[s]}
                </option>
              ))}
            </select>
          )}
        </Field>
        <Field label="Description">
          {(id) => (
            <textarea
              id={id}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
          )}
        </Field>
        <Field label="Reason for change (recorded in audit log)">
          {(id) => <input id={id} value={reason} onChange={(e) => setReason(e.target.value)} />}
        </Field>
        <div>
          <button type="submit" className="btn btn-primary" disabled={save.isPending}>
            Save settings
          </button>
        </div>
      </form>
    </section>
  );
}

function Lots({
  round,
  setNotice,
  editable,
}: {
  round: RoundDetail;
  setNotice: (n: Notice) => void;
  editable: boolean;
}) {
  const lotsQ = useQuery({
    queryKey: ["admin", "lots"],
    queryFn: () => api<Lot[]>("/api/admin/lots"),
    enabled: editable,
  });
  const [lotId, setLotId] = useState("");
  const [qty, setQty] = useState("");
  const add = useRoundAction(
    round,
    () =>
      api(`/api/admin/rounds/${round.id}/lots`, {
        method: "POST",
        body: { lot_id: Number(lotId), quantity: Number(qty) },
      }),
    setNotice,
    "Inventory reserved for this round.",
  );
  const remove = useRoundAction(
    round,
    (id: number) => api(`/api/admin/rounds/${round.id}/lots/${id}`, { method: "DELETE" }),
    setNotice,
    "Lot removed and its reservation released.",
  );
  const candidates = (lotsQ.data ?? []).filter(
    (l) => l.reserved_by_round_id === null && l.quantity_available > 0,
  );
  const selected = candidates.find((l) => String(l.id) === lotId);

  return (
    <section className="card" aria-labelledby="lots">
      <h2 id="lots">Inventory in this round</h2>
      {round.lots.length === 0 ? (
        <Empty>No lots reserved yet.</Empty>
      ) : (
        <TableRegion label="Reserved lots">
          <table>
            <thead>
              <tr>
                <th scope="col">Item</th>
                <th scope="col">Reserved</th>
                <th scope="col">Status</th>
                {editable && <th scope="col">Actions</th>}
              </tr>
            </thead>
            <tbody>
              {round.lots.map((l) => (
                <tr key={l.lot_id}>
                  <td>{l.name}</td>
                  <td>
                    {l.quantity} {l.unit}
                  </td>
                  <td>
                    {l.status === "consumed"
                      ? `${l.allocated_quantity} allocated, ${l.quantity - (l.allocated_quantity ?? 0)} returned`
                      : l.status}
                  </td>
                  {editable && (
                    <td>
                      <ConfirmButton
                        label="Remove"
                        confirmLabel="Remove lot"
                        prompt={`Release ${l.quantity} ${l.unit}?`}
                        className="btn btn-secondary"
                        busy={remove.isPending}
                        onConfirm={() => remove.mutate(l.lot_id)}
                      />
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </TableRegion>
      )}
      {editable && (
        <form
          className="inline-form"
          aria-label="Reserve a lot"
          onSubmit={(e) => {
            e.preventDefault();
            add.mutate(undefined);
          }}
        >
          <label htmlFor="add-lot">Lot</label>
          <select id="add-lot" required value={lotId} onChange={(e) => setLotId(e.target.value)}>
            <option value="">
              {lotsQ.isPending ? "Loading…" : candidates.length ? "Choose a lot" : "No free stock"}
            </option>
            {candidates.map((l) => (
              <option key={l.id} value={l.id}>
                {l.name} ({l.quantity_available} {l.unit} available)
              </option>
            ))}
          </select>
          <label htmlFor="add-qty">Quantity</label>
          <input
            id="add-qty"
            type="number"
            min={1}
            max={selected?.quantity_available}
            required
            value={qty}
            onChange={(e) => setQty(e.target.value)}
          />
          <button type="submit" className="btn btn-primary" disabled={add.isPending}>
            Reserve
          </button>
        </form>
      )}
    </section>
  );
}

function ParticipantRow({
  round,
  p,
  editable,
  setNotice,
}: {
  round: RoundDetail;
  p: Participant;
  editable: boolean;
  setNotice: (n: Notice) => void;
}) {
  const [eligible, setEligible] = useState(p.eligible);
  const [weight, setWeight] = useState(String(p.weight));
  const [priority, setPriority] = useState(String(p.priority));
  const [reason, setReason] = useState("");
  const save = useRoundAction(
    round,
    () =>
      api(`/api/admin/rounds/${round.id}/participants/${p.organization_id}`, {
        method: "PUT",
        body: { eligible, weight: Number(weight), priority: Number(priority), reason },
      }),
    setNotice,
    `Saved settings for ${p.organization_name}.`,
  );
  const dirty =
    eligible !== p.eligible || weight !== String(p.weight) || priority !== String(p.priority);
  const label = p.organization_name;
  return (
    <tr>
      <th scope="row">
        {label}
        {!p.organization_active && <small className="muted block">organization inactive</small>}
      </th>
      <td>
        <input
          type="checkbox"
          aria-label={`${label} eligible`}
          checked={eligible}
          disabled={!editable}
          onChange={(e) => setEligible(e.target.checked)}
        />
      </td>
      <td>
        <input
          type="number"
          min={1}
          max={1000}
          aria-label={`${label} need weight`}
          className="narrow"
          value={weight}
          disabled={!editable}
          onChange={(e) => setWeight(e.target.value)}
        />
      </td>
      <td>
        <input
          type="number"
          min={1}
          max={10}
          aria-label={`${label} priority tier`}
          className="narrow"
          value={priority}
          disabled={!editable}
          onChange={(e) => setPriority(e.target.value)}
        />
      </td>
      {editable && (
        <td>
          <form
            className="inline-form"
            onSubmit={(e) => {
              e.preventDefault();
              save.mutate(undefined);
            }}
          >
            <input
              aria-label={`Reason for changing ${label}`}
              placeholder="Reason (required)"
              required
              minLength={3}
              value={reason}
              onChange={(e) => setReason(e.target.value)}
            />
            <button type="submit" className="btn btn-secondary" disabled={!dirty || save.isPending}>
              Save
            </button>
          </form>
        </td>
      )}
    </tr>
  );
}

function Participants({
  round,
  setNotice,
  editable,
}: {
  round: RoundDetail;
  setNotice: (n: Notice) => void;
  editable: boolean;
}) {
  const ps = round.participants ?? [];
  return (
    <section className="card" aria-labelledby="participants">
      <h2 id="participants">Eligibility, need weights and priority tiers</h2>
      <p className="muted">
        Weights (1–1000) are used by need-weighted rounds; tiers (1 = served first, up to 10) by
        priority rounds. Changes are audited and frozen when the round closes.
      </p>
      {ps.length === 0 ? (
        <Empty>No organizations participate.</Empty>
      ) : (
        <TableRegion label="Participants">
          <table>
            <thead>
              <tr>
                <th scope="col">Organization</th>
                <th scope="col">Eligible</th>
                <th scope="col">Need weight</th>
                <th scope="col">Priority tier</th>
                {editable && <th scope="col">Save</th>}
              </tr>
            </thead>
            <tbody>
              {ps.map((p) => (
                <ParticipantRow
                  key={`${p.organization_id}-${p.version}`}
                  round={round}
                  p={p}
                  editable={editable}
                  setNotice={setNotice}
                />
              ))}
            </tbody>
          </table>
        </TableRegion>
      )}
    </section>
  );
}

function Requests({ round }: { round: RoundDetail }) {
  const lotName = new Map(round.lots.map((l) => [l.lot_id, l]));
  return (
    <section className="card" aria-labelledby="requests">
      <h2 id="requests">Requests</h2>
      {round.requests.length === 0 ? (
        <Empty>No requests yet.</Empty>
      ) : (
        <TableRegion label="All requests">
          <table>
            <thead>
              <tr>
                <th scope="col">Order</th>
                <th scope="col">Organization</th>
                <th scope="col">Item</th>
                <th scope="col">Quantity</th>
                <th scope="col">Status</th>
                <th scope="col">Last change</th>
              </tr>
            </thead>
            <tbody>
              {round.requests.map((r) => (
                <tr key={r.id} className={r.status === "withdrawn" ? "withdrawn" : undefined}>
                  <td>#{r.accepted_seq}</td>
                  <td>{r.organization_name}</td>
                  <td>{lotName.get(r.lot_id)?.name ?? r.lot_id}</td>
                  <td>
                    {r.quantity} {lotName.get(r.lot_id)?.unit}
                  </td>
                  <td>{r.status}</td>
                  <td>{formatDateTime(r.updated_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableRegion>
      )}
    </section>
  );
}

function PreviewPanel({
  round,
  setNotice,
}: {
  round: RoundDetail;
  setNotice: (n: Notice) => void;
}) {
  const frozen = round.snapshot?.strategy ?? round.strategy;
  const [strategy, setStrategy] = useState<Strategy>(frozen);
  const finalizeKey = useRef(newIdempotencyKey());
  const q = useQuery({
    queryKey: ["preview", round.id, strategy],
    queryFn: () =>
      api<Allocation>(
        `/api/admin/rounds/${round.id}/preview${strategy === frozen ? "" : `?strategy=${strategy}`}`,
      ),
  });
  const qc = useQueryClient();
  const finalize = useMutation({
    mutationFn: () =>
      api<Allocation>(`/api/admin/rounds/${round.id}/finalize`, {
        method: "POST",
        idempotencyKey: finalizeKey.current,
      }),
    onSuccess: () => {
      setNotice({ kind: "success", text: "Allocation finalized. Members can now see results." });
      void qc.invalidateQueries({ queryKey: ["round", round.id] });
      void qc.invalidateQueries({ queryKey: ["rounds"] });
    },
    // The key is kept: retrying after a lost response replays the same result.
    onError: (err) => setNotice({ kind: "error", text: errorMessage(err) }),
  });

  return (
    <section className="card" aria-labelledby="preview">
      <h2 id="preview">Preview allocation</h2>
      <Field
        label="Strategy to preview"
        hint="Finalizing always uses the frozen strategy. Other strategies are shown for comparison only."
      >
        {(id, hint) => (
          <select
            id={id}
            aria-describedby={hint}
            value={strategy}
            onChange={(e) => setStrategy(e.target.value as Strategy)}
          >
            {(Object.keys(STRATEGY_LABEL) as Strategy[]).map((s) => (
              <option key={s} value={s}>
                {STRATEGY_LABEL[s]}
                {s === frozen ? " (frozen — will be finalized)" : " (what-if)"}
              </option>
            ))}
          </select>
        )}
      </Field>
      {q.isPending ? (
        <Loading label="Computing preview…" />
      ) : q.isError ? (
        <ErrorState error={q.error} onRetry={() => q.refetch()} />
      ) : (
        <>
          {strategy !== frozen && (
            <Alert kind="info">
              What-if comparison using {STRATEGY_LABEL[strategy]}. This is not what will be
              finalized.
            </Alert>
          )}
          <AllocationView allocation={q.data} showOrganizations />
        </>
      )}
      <div className="actions">
        <ConfirmButton
          label="Finalize allocation"
          confirmLabel="Finalize — this cannot be undone"
          prompt={`Finalize using ${STRATEGY_LABEL[frozen]}?`}
          busy={finalize.isPending}
          onConfirm={() => finalize.mutate()}
        />
      </div>
    </section>
  );
}
