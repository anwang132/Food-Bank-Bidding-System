import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import type { FormEvent } from "react";

import { ApiError, api, errorMessage } from "../../api/client";
import type { Category, PlanningProfile, Preferences, SuggestedProfile } from "../../api/types";
import { Icon } from "../../components/Icon";
import { Alert, Card, ErrorState, Field, Loading, timeAgo } from "../../components/ui";
import { CATEGORIES, CATEGORY_LABEL, orgParam, planningKey } from "./shared";

type Form = {
  households_served: string;
  priority_categories: Category[];
  avoid_categories: Category[];
  dry_storage_capacity: string;
  cold_storage_capacity: string;
  frozen_storage_capacity: string;
  max_share_percent: string;
  dietary_notes: string;
};

const RATIONALE_LABEL: Record<string, string> = {
  overall: "Overall",
  priority_categories: "Priorities",
  avoid_categories: "Avoid",
  households_served: "Households served",
  storage: "Storage",
  max_share_percent: "Largest share of a lot",
  dietary_notes: "Dietary notes",
};

function toForm(p: Preferences): Form {
  const text = (n: number | null) => (n === null ? "" : String(n));
  return {
    households_served: p.households_served ? String(p.households_served) : "",
    priority_categories: p.priority_categories,
    avoid_categories: p.avoid_categories,
    dry_storage_capacity: text(p.dry_storage_capacity),
    cold_storage_capacity: text(p.cold_storage_capacity),
    frozen_storage_capacity: text(p.frozen_storage_capacity),
    max_share_percent: String(p.max_share_percent),
    dietary_notes: p.dietary_notes,
  };
}

function fromForm(f: Form): Preferences {
  const optional = (s: string) => (s.trim() === "" ? null : Number(s));
  return {
    households_served: Number(f.households_served || 0),
    priority_categories: f.priority_categories,
    avoid_categories: f.avoid_categories,
    dry_storage_capacity: optional(f.dry_storage_capacity),
    cold_storage_capacity: optional(f.cold_storage_capacity),
    frozen_storage_capacity: optional(f.frozen_storage_capacity),
    max_share_percent: Number(f.max_share_percent || 40),
    dietary_notes: f.dietary_notes,
  };
}

type Notice = { kind: "success" | "error"; text: string } | null;

function Editor({
  profile,
  orgId,
  notice,
  setNotice,
}: {
  profile: PlanningProfile;
  orgId: number | null;
  notice: Notice;
  setNotice: (n: Notice) => void;
}) {
  const qc = useQueryClient();
  const [form, setForm] = useState<Form>(() => toForm(profile.preferences));
  const [suggestion, setSuggestion] = useState<SuggestedProfile | null>(null);
  const set = <K extends keyof Form>(key: K, value: Form[K]) =>
    setForm((f) => ({ ...f, [key]: value }));

  const togglePriority = (c: Category) =>
    setForm((f) => ({
      ...f,
      priority_categories: f.priority_categories.includes(c)
        ? f.priority_categories.filter((x) => x !== c)
        : [...f.priority_categories, c],
      avoid_categories: f.avoid_categories.filter((x) => x !== c),
    }));
  const toggleAvoid = (c: Category) =>
    setForm((f) => ({
      ...f,
      avoid_categories: f.avoid_categories.includes(c)
        ? f.avoid_categories.filter((x) => x !== c)
        : [...f.avoid_categories, c],
      priority_categories: f.priority_categories.filter((x) => x !== c),
    }));

  const save = useMutation({
    mutationFn: () =>
      api<PlanningProfile>(`/api/planning/profile${orgParam(orgId)}`, {
        method: "PUT",
        body: { version: profile.version, preferences: fromForm(form) },
      }),
    onSuccess: () => {
      setNotice({ kind: "success", text: "Preferences saved. Plans now use them." });
      setSuggestion(null);
      void qc.invalidateQueries({ queryKey: planningKey(orgId) });
    },
    onError: (err) =>
      setNotice({
        kind: "error",
        text:
          err instanceof ApiError && err.code === "VERSION_CONFLICT"
            ? "Someone else changed these preferences. Reload the page to see their version."
            : errorMessage(err),
      }),
  });

  const suggest = useMutation({
    mutationFn: () => api<SuggestedProfile>(`/api/planning/profile/suggestion${orgParam(orgId)}`),
    onSuccess: setSuggestion,
    onError: (err) => setNotice({ kind: "error", text: errorMessage(err) }),
  });

  const submit = (e: FormEvent) => {
    e.preventDefault();
    setNotice(null);
    save.mutate();
  };

  return (
    <>
      {suggestion && (
        <Card
          className="suggestion"
          title="Suggested from your history"
          description={`Simulated from ${suggestion.based_on_rounds} round${
            suggestion.based_on_rounds === 1 ? "" : "s"
          } of requests and your documents. Review, then save to use them.`}
          actions={
            <>
              <button
                type="button"
                className="btn btn-ghost btn-sm"
                onClick={() => setSuggestion(null)}
              >
                Dismiss
              </button>
              <button
                type="button"
                className="btn btn-ai btn-sm"
                onClick={() => {
                  setForm(toForm(suggestion.preferences));
                  setNotice({
                    kind: "success",
                    text: "Suggestions filled in below. Save to keep them.",
                  });
                  setSuggestion(null);
                }}
              >
                <Icon name="check" size={15} /> Use these
              </button>
            </>
          }
        >
          <dl>
            {Object.entries(suggestion.rationale).map(([key, why]) => (
              <div key={key}>
                <dt>{RATIONALE_LABEL[key] ?? key}</dt>
                <dd>{why}</dd>
              </div>
            ))}
          </dl>
        </Card>
      )}
      {notice && <Alert kind={notice.kind}>{notice.text}</Alert>}
      <form className="card" onSubmit={submit} aria-label="Planning preferences">
        <div className="card-header">
          <div>
            <h2>Preferences</h2>
            <p className="muted card-description">
              {profile.updated_at
                ? `Last updated ${timeAgo(profile.updated_at)}${
                    profile.updated_by_name ? ` by ${profile.updated_by_name}` : ""
                  }.`
                : "Not set yet: plans use neutral defaults until you save."}
            </p>
          </div>
          <button
            type="button"
            className="btn btn-ai btn-sm"
            disabled={suggest.isPending}
            onClick={() => suggest.mutate()}
          >
            <Icon name="sparkles" size={15} />
            {suggest.isPending ? "Thinking…" : "Suggest from history"}
          </button>
        </div>

        <div className="pref-section">
          <h3>Who you serve</h3>
          <p>Used to size requests when your documents don&rsquo;t list a quantity.</p>
          <div className="pref-grid">
            <Field label="Households served per month" hint="Roughly one unit per 25 households.">
              {(id, hint) => (
                <input
                  id={id}
                  aria-describedby={hint}
                  type="number"
                  min={0}
                  max={1000000}
                  inputMode="numeric"
                  placeholder="e.g. 1200"
                  value={form.households_served}
                  onChange={(e) => set("households_served", e.target.value)}
                />
              )}
            </Field>
            <Field label="Dietary notes" hint="Shown with your plans; not used for sizing.">
              {(id, hint) => (
                <input
                  id={id}
                  aria-describedby={hint}
                  maxLength={1000}
                  placeholder="e.g. Many halal households"
                  value={form.dietary_notes}
                  onChange={(e) => set("dietary_notes", e.target.value)}
                />
              )}
            </Field>
          </div>
        </div>

        <div className="pref-section">
          <h3>Priorities</h3>
          <p>Select categories in order of importance. The first one counts the most.</p>
          <div className="chips" role="group" aria-label="Priority categories">
            {CATEGORIES.map((c) => {
              const rank = form.priority_categories.indexOf(c);
              return (
                <button
                  key={c}
                  type="button"
                  className="chip"
                  aria-pressed={rank >= 0}
                  onClick={() => togglePriority(c)}
                >
                  {rank >= 0 && <span className="chip-rank">{rank + 1}</span>}
                  {CATEGORY_LABEL[c]}
                </button>
              );
            })}
          </div>
          <p className="chips-label">Leave out entirely</p>
          <div className="chips" role="group" aria-label="Categories to avoid">
            {CATEGORIES.map((c) => (
              <button
                key={c}
                type="button"
                className="chip chip-avoid"
                aria-pressed={form.avoid_categories.includes(c)}
                onClick={() => toggleAvoid(c)}
              >
                {CATEGORY_LABEL[c]}
              </button>
            ))}
          </div>
        </div>

        <div className="pref-section">
          <h3>Capacity</h3>
          <p>
            Plans never suggest more than you can store. Capacity is counted in units (cases, boxes,
            bags). Leave blank if there is no limit.
          </p>
          <div className="pref-grid">
            {(
              [
                ["dry_storage_capacity", "Dry storage"],
                ["cold_storage_capacity", "Refrigerated"],
                ["frozen_storage_capacity", "Frozen"],
              ] as const
            ).map(([key, label]) => (
              <Field key={key} label={label}>
                {(id) => (
                  <input
                    id={id}
                    type="number"
                    min={0}
                    max={1000000}
                    inputMode="numeric"
                    placeholder="No limit"
                    value={form[key]}
                    onChange={(e) => set(key, e.target.value)}
                  />
                )}
              </Field>
            ))}
            <Field label="Largest share of one lot (%)" hint="Leaves room for other food banks.">
              {(id, hint) => (
                <input
                  id={id}
                  aria-describedby={hint}
                  type="number"
                  min={5}
                  max={100}
                  required
                  value={form.max_share_percent}
                  onChange={(e) => set("max_share_percent", e.target.value)}
                />
              )}
            </Field>
          </div>
        </div>

        <div className="form-foot">
          <span className="muted small">Saving updates every sample plan immediately.</span>
          <div className="actions">
            <button
              type="button"
              className="btn btn-ghost"
              onClick={() => {
                setForm(toForm(profile.preferences));
                setNotice(null);
              }}
            >
              Reset
            </button>
            <button type="submit" className="btn btn-primary" disabled={save.isPending}>
              {save.isPending ? "Saving…" : "Save preferences"}
            </button>
          </div>
        </div>
      </form>
    </>
  );
}

export function PreferencesTab({ orgId }: { orgId: number | null }) {
  // Lives here so it survives the editor remounting on a new version.
  const [notice, setNotice] = useState<Notice>(null);
  const q = useQuery({
    queryKey: planningKey(orgId, "profile"),
    queryFn: () => api<PlanningProfile>(`/api/planning/profile${orgParam(orgId)}`),
  });
  if (q.isPending) return <Loading />;
  if (q.isError) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  return (
    <Editor
      key={q.data.version}
      profile={q.data}
      orgId={orgId}
      notice={notice}
      setNotice={setNotice}
    />
  );
}
