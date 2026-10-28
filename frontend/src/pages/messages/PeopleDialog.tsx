import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { useNavigate } from "react-router-dom";

import { api, errorMessage } from "../../api/client";
import type { Person } from "../../api/types";
import { CHANNELS_KEY, usePeople } from "../../chat/queries";
import { Icon } from "../../components/Icon";
import { Alert, Avatar, Field, Loading } from "../../components/ui";

export type PeopleMode =
  { kind: "direct" } | { kind: "group" } | { kind: "add"; channelId: number; existing: number[] };

const TITLES = {
  direct: "New message",
  group: "New group",
  add: "Add people",
} as const;

/** Modal person picker for starting a direct message, creating a group, or adding
 *  people to a group. Uses the native <dialog> for focus trapping and Escape. */
export function PeopleDialog({ mode, onClose }: { mode: PeopleMode; onClose: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  const qc = useQueryClient();
  const navigate = useNavigate();
  const people = usePeople();
  const [query, setQuery] = useState("");
  const [picked, setPicked] = useState<number[]>([]);
  const [name, setName] = useState("");

  useEffect(() => {
    const dialog = ref.current;
    dialog?.showModal();
    return () => dialog?.close();
  }, []);

  const submit = useMutation({
    mutationFn: async (ids: number[]) => {
      if (mode.kind === "direct") {
        return api<{ id: number }>("/api/chat/direct", {
          method: "POST",
          body: { user_id: ids[0] },
        });
      }
      if (mode.kind === "group") {
        return api<{ id: number }>("/api/chat/groups", {
          method: "POST",
          body: { name, member_ids: ids },
        });
      }
      return api<{ id: number }>(`/api/chat/channels/${mode.channelId}/members`, {
        method: "POST",
        body: { user_ids: ids },
      });
    },
    onSuccess: ({ id }) => {
      void qc.invalidateQueries({ queryKey: CHANNELS_KEY });
      void qc.invalidateQueries({ queryKey: ["chat", "messages", id] });
      onClose();
      navigate(`/messages/${id}`);
    },
  });

  const excluded = new Set(mode.kind === "add" ? mode.existing : []);
  const q = query.trim().toLowerCase();
  const options = (people.data ?? []).filter(
    (p) =>
      !excluded.has(p.id) &&
      (!q || `${p.display_name} ${p.organization_name ?? ""}`.toLowerCase().includes(q)),
  );
  const multi = mode.kind !== "direct";

  const choose = (p: Person) => {
    if (!multi) {
      submit.mutate([p.id]);
      return;
    }
    setPicked((cur) => (cur.includes(p.id) ? cur.filter((id) => id !== p.id) : [...cur, p.id]));
  };

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (picked.length > 0) submit.mutate(picked);
  };

  return (
    <dialog
      ref={ref}
      className="dialog"
      aria-labelledby="people-dialog-title"
      onClose={onClose}
      onCancel={onClose}
    >
      <form method="dialog" onSubmit={onSubmit} className="dialog-form">
        <div className="dialog-head">
          <h2 id="people-dialog-title">{TITLES[mode.kind]}</h2>
          <button type="button" className="icon-btn" aria-label="Close" onClick={onClose}>
            <Icon name="close" />
          </button>
        </div>
        <div className="dialog-body">
          {submit.isError && <Alert kind="error">{errorMessage(submit.error)}</Alert>}
          {mode.kind === "group" && (
            <Field label="Group name">
              {(id) => (
                <input
                  id={id}
                  required
                  minLength={2}
                  maxLength={80}
                  placeholder="e.g. Produce run — North Valley"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                />
              )}
            </Field>
          )}
          <div className="search">
            <Icon name="search" size={16} />
            <input
              type="search"
              aria-label="Search people"
              placeholder="Search by name or food bank"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              autoFocus={mode.kind !== "group"}
            />
          </div>
          {people.isPending ? (
            <Loading label="Loading people…" />
          ) : options.length === 0 ? (
            <p className="muted small">No one matches.</p>
          ) : (
            <ul className="people-list" aria-label="People">
              {options.map((p) => {
                const selected = picked.includes(p.id);
                return (
                  <li key={p.id}>
                    <button
                      type="button"
                      className="person-option"
                      aria-pressed={multi ? selected : undefined}
                      disabled={submit.isPending}
                      onClick={() => choose(p)}
                    >
                      <Avatar name={p.display_name} size="sm" />
                      <span className="person-text">
                        <span>{p.display_name}</span>
                        <small>{p.organization_name ?? "Network administrator"}</small>
                      </span>
                      {selected && (
                        <span className="check">
                          <Icon name="check" />
                        </span>
                      )}
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
        {multi && (
          <div className="dialog-foot">
            <span className="muted small spacer">
              {picked.length === 0 ? "Choose at least one person" : `${picked.length} selected`}
            </span>
            <button type="button" className="btn btn-secondary" onClick={onClose}>
              Cancel
            </button>
            <button
              type="submit"
              className="btn btn-primary"
              disabled={picked.length === 0 || submit.isPending}
            >
              {mode.kind === "group" ? "Create group" : "Add to group"}
            </button>
          </div>
        )}
      </form>
    </dialog>
  );
}
