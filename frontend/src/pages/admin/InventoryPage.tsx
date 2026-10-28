import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";

import { api, errorMessage } from "../../api/client";
import type { Lot } from "../../api/types";
import {
  Alert,
  Empty,
  ErrorState,
  Field,
  Loading,
  TableRegion,
  formatDate,
  PageHeader,
} from "../../components/ui";

type Notice = { kind: "success" | "error"; text: string } | null;

function AdjustForm({ lot, onDone }: { lot: Lot; onDone: (n: Notice) => void }) {
  const qc = useQueryClient();
  const [qty, setQty] = useState(String(lot.quantity_on_hand));
  const [reason, setReason] = useState("");
  const save = useMutation({
    mutationFn: () =>
      api<Lot>(`/api/admin/lots/${lot.id}`, {
        method: "PATCH",
        body: { version: lot.version, quantity_on_hand: Number(qty), reason },
      }),
    onSuccess: (l) =>
      onDone({ kind: "success", text: `${l.name}: ${l.quantity_on_hand} ${l.unit} on hand.` }),
    onError: (err) => onDone({ kind: "error", text: errorMessage(err) }),
    onSettled: () => qc.invalidateQueries({ queryKey: ["admin", "lots"] }),
  });
  return (
    <form
      className="inline-form"
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate();
      }}
    >
      <input
        type="number"
        min={lot.quantity_reserved}
        className="narrow"
        aria-label={`New on-hand quantity for ${lot.name}`}
        value={qty}
        onChange={(e) => setQty(e.target.value)}
      />
      <input
        aria-label={`Reason for adjusting ${lot.name}`}
        placeholder="Reason"
        required
        minLength={3}
        value={reason}
        onChange={(e) => setReason(e.target.value)}
      />
      <button className="btn btn-secondary" type="submit" disabled={save.isPending}>
        Adjust
      </button>
    </form>
  );
}

export function InventoryPage() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["admin", "lots"], queryFn: () => api<Lot[]>("/api/admin/lots") });
  const [notice, setNotice] = useState<Notice>(null);
  const [form, setForm] = useState({
    name: "",
    unit: "cases",
    quantity: "",
    best_before: "",
    description: "",
  });
  const create = useMutation({
    mutationFn: () =>
      api<Lot>("/api/admin/lots", {
        method: "POST",
        body: {
          name: form.name,
          unit: form.unit,
          description: form.description,
          quantity_on_hand: Number(form.quantity),
          best_before: form.best_before || null,
        },
      }),
    onSuccess: (l) => {
      setNotice({ kind: "success", text: `Added ${l.quantity_on_hand} ${l.unit} of ${l.name}.` });
      setForm({ name: "", unit: "cases", quantity: "", best_before: "", description: "" });
      void qc.invalidateQueries({ queryKey: ["admin", "lots"] });
    },
    onError: (err) => setNotice({ kind: "error", text: errorMessage(err) }),
  });
  const set = (k: keyof typeof form) => (e: { target: { value: string } }) =>
    setForm((f) => ({ ...f, [k]: e.target.value }));

  return (
    <section>
      <PageHeader
        title="Inventory"
        subtitle="Donated stock on hand. Stock reserved by a round cannot be reduced below the reserved amount."
      />
      {notice && <Alert kind={notice.kind}>{notice.text}</Alert>}
      <form
        className="card grid-form"
        aria-labelledby="add-lot-heading"
        onSubmit={(e) => {
          e.preventDefault();
          create.mutate();
        }}
      >
        <h2 id="add-lot-heading">Add donated lot</h2>
        <Field label="Item name">
          {(id) => <input id={id} required value={form.name} onChange={set("name")} />}
        </Field>
        <Field label="Unit" hint="e.g. cases, boxes, bags">
          {(id, hint) => (
            <input
              id={id}
              required
              aria-describedby={hint}
              value={form.unit}
              onChange={set("unit")}
            />
          )}
        </Field>
        <Field label="Quantity on hand">
          {(id) => (
            <input
              id={id}
              type="number"
              min={0}
              required
              value={form.quantity}
              onChange={set("quantity")}
            />
          )}
        </Field>
        <Field label="Best before (optional)">
          {(id) => (
            <input id={id} type="date" value={form.best_before} onChange={set("best_before")} />
          )}
        </Field>
        <Field label="Description (optional)">
          {(id) => <input id={id} value={form.description} onChange={set("description")} />}
        </Field>
        <div>
          <button className="btn btn-primary" type="submit" disabled={create.isPending}>
            Add lot
          </button>
        </div>
      </form>

      <h2>Stock</h2>
      {q.isPending ? (
        <Loading />
      ) : q.isError ? (
        <ErrorState error={q.error} onRetry={() => q.refetch()} />
      ) : q.data.length === 0 ? (
        <Empty>No inventory yet. Add a donated lot above.</Empty>
      ) : (
        <TableRegion label="Inventory lots">
          <table>
            <thead>
              <tr>
                <th scope="col">Item</th>
                <th scope="col">On hand</th>
                <th scope="col">Reserved</th>
                <th scope="col">Available</th>
                <th scope="col">Best before</th>
                <th scope="col">Adjust on-hand</th>
              </tr>
            </thead>
            <tbody>
              {q.data.map((l) => (
                <tr key={l.id}>
                  <td>
                    {l.name}
                    {l.description && <small className="muted block">{l.description}</small>}
                  </td>
                  <td>
                    {l.quantity_on_hand} {l.unit}
                  </td>
                  <td>
                    {l.quantity_reserved}
                    {l.reserved_by_round_id && (
                      <small className="block">
                        <Link to={`/rounds/${l.reserved_by_round_id}`}>
                          round {l.reserved_by_round_id}
                        </Link>
                      </small>
                    )}
                  </td>
                  <td>{l.quantity_available}</td>
                  <td>{formatDate(l.best_before)}</td>
                  <td>
                    <AdjustForm key={l.version} lot={l} onDone={setNotice} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableRegion>
      )}
    </section>
  );
}
