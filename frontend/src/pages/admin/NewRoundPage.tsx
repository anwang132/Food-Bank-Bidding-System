import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { api, errorMessage } from "../../api/client";
import { STRATEGY_HELP, STRATEGY_LABEL } from "../../api/types";
import type { RoundDetail, Strategy } from "../../api/types";
import { Alert, Field, PageHeader } from "../../components/ui";

function defaultDeadline(): string {
  const d = new Date(Date.now() + 7 * 24 * 3600 * 1000);
  d.setMinutes(0, 0, 0);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:00`;
}

export function NewRoundPage() {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [strategy, setStrategy] = useState<Strategy>("fcfs");
  const [deadline, setDeadline] = useState(defaultDeadline);
  const create = useMutation({
    mutationFn: () =>
      api<RoundDetail>("/api/admin/rounds", {
        method: "POST",
        body: {
          name,
          description,
          strategy,
          request_deadline: new Date(deadline).toISOString(),
        },
      }),
    onSuccess: (r) => {
      void qc.invalidateQueries({ queryKey: ["rounds"] });
      navigate(`/rounds/${r.id}`);
    },
  });
  return (
    <section>
      <PageHeader
        title="New round"
        breadcrumb={<Link to="/rounds">Distribution rounds</Link>}
        subtitle="Rounds start as drafts. Add inventory and adjust eligibility before opening."
      />
      {create.isError && <Alert kind="error">{errorMessage(create.error)}</Alert>}
      <form
        className="card grid-form"
        onSubmit={(e) => {
          e.preventDefault();
          create.mutate();
        }}
      >
        <Field label="Name">
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
        <fieldset>
          <legend>Allocation strategy</legend>
          {(Object.keys(STRATEGY_LABEL) as Strategy[]).map((s) => (
            <label key={s} className="radio">
              <input
                type="radio"
                name="strategy"
                value={s}
                checked={strategy === s}
                onChange={() => setStrategy(s)}
              />
              <span>
                <strong>{STRATEGY_LABEL[s]}</strong>
                <small className="muted block">{STRATEGY_HELP[s]}</small>
              </span>
            </label>
          ))}
        </fieldset>
        <Field label="Description (optional)">
          {(id) => (
            <textarea
              id={id}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
          )}
        </Field>
        <div>
          <button type="submit" className="btn btn-primary" disabled={create.isPending}>
            {create.isPending ? "Creating…" : "Create draft round"}
          </button>
        </div>
      </form>
    </section>
  );
}
