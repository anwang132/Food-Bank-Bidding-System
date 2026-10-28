import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, errorMessage } from "../../api/client";
import type { Organization } from "../../api/types";
import {
  Alert,
  Empty,
  ErrorState,
  Field,
  Loading,
  TableRegion,
  PageHeader,
} from "../../components/ui";

type Notice = { kind: "success" | "error"; text: string } | null;

function OrgRow({ org, onDone }: { org: Organization; onDone: (n: Notice) => void }) {
  const qc = useQueryClient();
  const [weight, setWeight] = useState(String(org.default_weight));
  const [priority, setPriority] = useState(String(org.default_priority));
  const [region, setRegion] = useState(org.region ?? "");
  const [active, setActive] = useState(Boolean(org.is_active));
  const save = useMutation({
    mutationFn: () =>
      api<Organization>(`/api/admin/organizations/${org.id}`, {
        method: "PATCH",
        body: {
          version: org.version,
          default_weight: Number(weight),
          default_priority: Number(priority),
          region,
          is_active: active,
        },
      }),
    onSuccess: () => onDone({ kind: "success", text: `Saved ${org.name}.` }),
    onError: (err) => onDone({ kind: "error", text: errorMessage(err) }),
    onSettled: () => qc.invalidateQueries({ queryKey: ["admin", "orgs"] }),
  });
  return (
    <tr>
      <th scope="row">{org.name}</th>
      <td>
        <input
          type="number"
          className="narrow"
          min={1}
          max={1000}
          aria-label={`${org.name} default need weight`}
          value={weight}
          onChange={(e) => setWeight(e.target.value)}
        />
      </td>
      <td>
        <input
          type="number"
          className="narrow"
          min={1}
          max={10}
          aria-label={`${org.name} default priority tier`}
          value={priority}
          onChange={(e) => setPriority(e.target.value)}
        />
      </td>
      <td>
        <input
          className="region-input"
          maxLength={60}
          placeholder="No region"
          aria-label={`${org.name} region`}
          value={region}
          onChange={(e) => setRegion(e.target.value)}
        />
      </td>
      <td>
        <input
          type="checkbox"
          aria-label={`${org.name} active`}
          checked={active}
          onChange={(e) => setActive(e.target.checked)}
        />
      </td>
      <td>
        <button
          type="button"
          className="btn btn-secondary"
          disabled={save.isPending}
          onClick={() => save.mutate()}
        >
          Save
        </button>
      </td>
    </tr>
  );
}

export function OrganizationsPage() {
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["admin", "orgs"],
    queryFn: () => api<Organization[]>("/api/admin/organizations"),
  });
  const [notice, setNotice] = useState<Notice>(null);
  const [name, setName] = useState("");
  const create = useMutation({
    mutationFn: () =>
      api<Organization>("/api/admin/organizations", {
        method: "POST",
        body: { name, default_weight: 1, default_priority: 5 },
      }),
    onSuccess: (o) => {
      setNotice({ kind: "success", text: `Added ${o.name}.` });
      setName("");
      void qc.invalidateQueries({ queryKey: ["admin", "orgs"] });
    },
    onError: (err) => setNotice({ kind: "error", text: errorMessage(err) }),
  });
  return (
    <section>
      <PageHeader
        title="Organizations"
        subtitle="Partner food banks. Default weights and tiers are copied into each new round, where they can be adjusted; changes here never alter existing rounds. Food banks in the same region share a local chat channel."
      />
      {notice && <Alert kind={notice.kind}>{notice.text}</Alert>}
      <form
        className="inline-form card"
        onSubmit={(e) => {
          e.preventDefault();
          create.mutate();
        }}
      >
        <Field label="New organization name">
          {(id) => (
            <input
              id={id}
              required
              minLength={2}
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          )}
        </Field>
        <button type="submit" className="btn btn-primary" disabled={create.isPending}>
          Add
        </button>
      </form>
      {q.isPending ? (
        <Loading />
      ) : q.isError ? (
        <ErrorState error={q.error} onRetry={() => q.refetch()} />
      ) : q.data.length === 0 ? (
        <Empty>No organizations yet.</Empty>
      ) : (
        <TableRegion label="Organizations">
          <table>
            <thead>
              <tr>
                <th scope="col">Name</th>
                <th scope="col">Default weight</th>
                <th scope="col">Default tier</th>
                <th scope="col">Region</th>
                <th scope="col">Active</th>
                <th scope="col">Save</th>
              </tr>
            </thead>
            <tbody>
              {q.data.map((o) => (
                <OrgRow key={`${o.id}-${o.version}`} org={o} onDone={setNotice} />
              ))}
            </tbody>
          </table>
        </TableRegion>
      )}
    </section>
  );
}
