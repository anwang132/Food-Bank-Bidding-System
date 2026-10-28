import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, errorMessage } from "../../api/client";
import type { Organization, Role, User } from "../../api/types";
import { useAuth } from "../../auth/AuthContext";
import { Alert, ErrorState, Field, Loading, TableRegion, PageHeader } from "../../components/ui";

type Notice = { kind: "success" | "error"; text: string } | null;

function UserRow({
  user,
  orgs,
  isSelf,
  onDone,
}: {
  user: User;
  orgs: Organization[];
  isSelf: boolean;
  onDone: (n: Notice) => void;
}) {
  const qc = useQueryClient();
  const [role, setRole] = useState<Role>(user.role);
  const [orgId, setOrgId] = useState(user.organization_id ? String(user.organization_id) : "");
  const [active, setActive] = useState(user.is_active);
  const save = useMutation({
    mutationFn: () =>
      api<User>(`/api/admin/users/${user.id}`, {
        method: "PATCH",
        body: {
          version: user.version,
          role,
          organization_id: role === "member" ? Number(orgId) : null,
          is_active: active,
        },
      }),
    onSuccess: () => onDone({ kind: "success", text: `Saved ${user.email}.` }),
    onError: (err) => onDone({ kind: "error", text: errorMessage(err) }),
    onSettled: () => qc.invalidateQueries({ queryKey: ["admin", "users"] }),
  });
  return (
    <tr>
      <th scope="row">
        {user.display_name}
        <small className="muted block">{user.email}</small>
      </th>
      <td>
        <select
          aria-label={`${user.email} role`}
          value={role}
          disabled={isSelf}
          onChange={(e) => setRole(e.target.value as Role)}
        >
          <option value="admin">admin</option>
          <option value="member">member</option>
          <option value="viewer">viewer</option>
        </select>
      </td>
      <td>
        <select
          aria-label={`${user.email} organization`}
          value={orgId}
          disabled={role !== "member"}
          onChange={(e) => setOrgId(e.target.value)}
        >
          <option value="">—</option>
          {orgs.map((o) => (
            <option key={o.id} value={o.id}>
              {o.name}
            </option>
          ))}
        </select>
      </td>
      <td>
        <input
          type="checkbox"
          aria-label={`${user.email} active`}
          checked={active}
          disabled={isSelf}
          onChange={(e) => setActive(e.target.checked)}
        />
      </td>
      <td>
        <button
          type="button"
          className="btn btn-secondary"
          disabled={save.isPending || (role === "member" && !orgId)}
          onClick={() => save.mutate()}
        >
          Save
        </button>
      </td>
    </tr>
  );
}

export function UsersPage() {
  const { me } = useAuth();
  const qc = useQueryClient();
  const users = useQuery({
    queryKey: ["admin", "users"],
    queryFn: () => api<User[]>("/api/admin/users"),
  });
  const orgs = useQuery({
    queryKey: ["admin", "orgs"],
    queryFn: () => api<Organization[]>("/api/admin/organizations"),
  });
  const [notice, setNotice] = useState<Notice>(null);
  const empty = {
    email: "",
    display_name: "",
    password: "",
    role: "member" as Role,
    organization_id: "",
  };
  const [form, setForm] = useState(empty);
  const create = useMutation({
    mutationFn: () =>
      api<User>("/api/admin/users", {
        method: "POST",
        body: {
          ...form,
          organization_id: form.role === "member" ? Number(form.organization_id) : null,
        },
      }),
    onSuccess: (u) => {
      setNotice({ kind: "success", text: `Created ${u.email}.` });
      setForm(empty);
      void qc.invalidateQueries({ queryKey: ["admin", "users"] });
    },
    onError: (err) => setNotice({ kind: "error", text: errorMessage(err) }),
  });
  const set = (k: keyof typeof form) => (e: { target: { value: string } }) =>
    setForm((f) => ({ ...f, [k]: e.target.value }));

  return (
    <section>
      <PageHeader
        title="Users"
        subtitle="Accounts for administrators, member organizations and viewers. Roles are assigned here only."
      />
      {notice && <Alert kind={notice.kind}>{notice.text}</Alert>}
      <form
        className="card grid-form"
        aria-labelledby="new-user"
        onSubmit={(e) => {
          e.preventDefault();
          create.mutate();
        }}
      >
        <h2 id="new-user">Add user</h2>
        <Field label="Email">
          {(id) => (
            <input id={id} type="email" required value={form.email} onChange={set("email")} />
          )}
        </Field>
        <Field label="Display name">
          {(id) => (
            <input id={id} required value={form.display_name} onChange={set("display_name")} />
          )}
        </Field>
        <Field label="Initial password" hint="At least 10 characters. Share it securely.">
          {(id, hint) => (
            <input
              id={id}
              type="password"
              autoComplete="new-password"
              minLength={10}
              required
              aria-describedby={hint}
              value={form.password}
              onChange={set("password")}
            />
          )}
        </Field>
        <Field label="Role">
          {(id) => (
            <select id={id} value={form.role} onChange={set("role")}>
              <option value="member">Food bank member</option>
              <option value="viewer">Viewer</option>
              <option value="admin">Administrator</option>
            </select>
          )}
        </Field>
        {form.role === "member" && (
          <Field label="Organization">
            {(id) => (
              <select
                id={id}
                required
                value={form.organization_id}
                onChange={set("organization_id")}
              >
                <option value="">Choose…</option>
                {(orgs.data ?? []).map((o) => (
                  <option key={o.id} value={o.id}>
                    {o.name}
                  </option>
                ))}
              </select>
            )}
          </Field>
        )}
        <div>
          <button type="submit" className="btn btn-primary" disabled={create.isPending}>
            Create user
          </button>
        </div>
      </form>
      {users.isPending || orgs.isPending ? (
        <Loading />
      ) : users.isError ? (
        <ErrorState error={users.error} onRetry={() => users.refetch()} />
      ) : (
        <TableRegion label="Users">
          <table>
            <thead>
              <tr>
                <th scope="col">User</th>
                <th scope="col">Role</th>
                <th scope="col">Organization</th>
                <th scope="col">Active</th>
                <th scope="col">Save</th>
              </tr>
            </thead>
            <tbody>
              {users.data.map((u) => (
                <UserRow
                  key={`${u.id}-${u.version}`}
                  user={u}
                  orgs={orgs.data ?? []}
                  isSelf={u.id === me?.user.id}
                  onDone={setNotice}
                />
              ))}
            </tbody>
          </table>
        </TableRegion>
      )}
    </section>
  );
}
