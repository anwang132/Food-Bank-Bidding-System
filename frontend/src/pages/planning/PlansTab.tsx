import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";

import { api, errorMessage, newIdempotencyKey } from "../../api/client";
import type { Plan, PlanRound, Plans } from "../../api/types";
import { Icon } from "../../components/Icon";
import {
  Alert,
  Card,
  EmptyState,
  ErrorState,
  Loading,
  TableRegion,
  formatDateTime,
  relativeDeadline,
} from "../../components/ui";
import { CATEGORY_LABEL, STORAGE_LABEL, orgParam, planningKey } from "./shared";

type Outcome = { kind: "success" | "error" | "info"; text: string };

function Delta({ suggested, current }: { suggested: number; current: number | null }) {
  if (current === null) return <span className="delta">New request</span>;
  if (current === suggested) return <span className="delta">Matches your request</span>;
  const diff = suggested - current;
  return (
    <span className="delta">
      {diff > 0 ? "+" : "−"}
      {Math.abs(diff)} vs. your {current}
    </span>
  );
}

function RoundPlan({
  round,
  canApply,
  orgId,
}: {
  round: PlanRound;
  canApply: boolean;
  orgId: number | null;
}) {
  const qc = useQueryClient();
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const changes = round.lines.filter((l) => l.current_quantity !== l.suggested);

  // Creates use idempotency keys and edits carry versions, so a retry or a
  // concurrent edit can never double-submit or silently overwrite.
  const apply = useMutation({
    mutationFn: async () => {
      const failed: string[] = [];
      for (const line of changes) {
        try {
          if (line.current_request_id !== null) {
            await api(`/api/requests/${line.current_request_id}`, {
              method: "PATCH",
              body: { quantity: line.suggested, version: line.current_request_version },
            });
          } else {
            await api(`/api/rounds/${round.round_id}/requests`, {
              method: "POST",
              body: { lot_id: line.lot_id, quantity: line.suggested },
              idempotencyKey: newIdempotencyKey(),
            });
          }
        } catch (err) {
          failed.push(`${line.lot_name}: ${errorMessage(err)}`);
        }
      }
      return failed;
    },
    onSuccess: (failed) => {
      const done = changes.length - failed.length;
      setOutcome(
        failed.length === 0
          ? { kind: "success", text: `Submitted ${done} request${done === 1 ? "" : "s"}.` }
          : {
              kind: "error",
              text: `${done} submitted; ${failed.length} could not be: ${failed.join("; ")}`,
            },
      );
      void qc.invalidateQueries({ queryKey: planningKey(orgId) });
      void qc.invalidateQueries({ queryKey: ["round", round.round_id] });
      void qc.invalidateQueries({ queryKey: ["rounds"] });
    },
  });

  return (
    <Card
      title={round.round_name}
      description={
        <>
          {round.strategy_label} · {relativeDeadline(round.request_deadline)} (
          {formatDateTime(round.request_deadline)})
        </>
      }
      actions={
        <>
          <Link to={`/rounds/${round.round_id}`} className="btn btn-ghost btn-sm">
            Open round
          </Link>
          {canApply && (
            <button
              type="button"
              className="btn btn-primary btn-sm"
              disabled={changes.length === 0 || apply.isPending}
              onClick={() => apply.mutate()}
            >
              {apply.isPending
                ? "Submitting…"
                : changes.length === 0
                  ? "Already applied"
                  : `Apply to ${round.round_name}`}
            </button>
          )}
        </>
      }
    >
      <p className="tip">
        <Icon name="info" size={15} />
        <span>{round.tip}</span>
      </p>
      {outcome && <Alert kind={outcome.kind}>{outcome.text}</Alert>}
      <TableRegion label={`Suggested requests for ${round.round_name}`}>
        <table>
          <colgroup>
            <col className="col-item" />
            <col className="col-num" />
            <col className="col-suggested" />
            <col />
          </colgroup>
          <thead>
            <tr>
              <th scope="col">Item</th>
              <th scope="col" className="num">
                Offered
              </th>
              <th scope="col" className="num">
                Suggested
              </th>
              <th scope="col">Why</th>
            </tr>
          </thead>
          <tbody>
            {round.lines.map((line) => (
              <tr key={line.lot_id}>
                <th scope="row">
                  {line.lot_name}
                  <span className="category-tag">
                    {CATEGORY_LABEL[line.category]} · {STORAGE_LABEL[line.storage]}
                  </span>
                </th>
                <td className="num">
                  {line.offered} <span className="muted small">{line.unit}</span>
                </td>
                <td className="num">
                  <span className="suggested">{line.suggested}</span>
                  <Delta suggested={line.suggested} current={line.current_quantity} />
                </td>
                <td>
                  <ul className="reasons">
                    {line.reasons.map((r) => (
                      <li key={r}>{r}</li>
                    ))}
                  </ul>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </TableRegion>
    </Card>
  );
}

function PlanPicker({
  plans,
  selected,
  onSelect,
}: {
  plans: Plan[];
  selected: Plan["key"];
  onSelect: (key: Plan["key"]) => void;
}) {
  return (
    <div className="plan-options" role="group" aria-label="Choose a plan">
      {plans.map((p) => (
        <button
          key={p.key}
          type="button"
          className="plan-option"
          aria-pressed={p.key === selected}
          onClick={() => onSelect(p.key)}
        >
          <span className="plan-option-head">
            <h3>{p.title}</h3>
            <span className="radio-dot" aria-hidden="true" />
          </span>
          <span className="plan-total">
            {p.total_units.toLocaleString()}
            <small>units · {p.line_count} requests</small>
          </span>
          <p>{p.tagline}</p>
        </button>
      ))}
    </div>
  );
}

export function PlansTab({
  orgId,
  canApply,
  onGoTo,
}: {
  orgId: number | null;
  canApply: boolean;
  onGoTo: (tab: "preferences" | "documents") => void;
}) {
  const [selected, setSelected] = useState<Plan["key"]>("balanced");
  const q = useQuery({
    queryKey: planningKey(orgId, "plans"),
    queryFn: () => api<Plans>(`/api/planning/plans${orgParam(orgId)}`),
  });
  if (q.isPending) return <Loading label="Drafting plans…" />;
  if (q.isError) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;

  const data = q.data;
  const plan = data.plans.find((p) => p.key === selected) ?? data.plans[0]!;
  const { inputs, insights } = data;

  return (
    <>
      <Card className="ai-hero">
        <div className="ai-hero-row">
          <div>
            <h2>Three ways to request from the open rounds</h2>
            <p>
              Each plan follows the same rules with a different emphasis. Every quantity lists the
              reasons behind it, so you can see exactly why it was suggested.
            </p>
            <div className="inputs-row" aria-label="What these plans are based on">
              <span className="chip">
                <Icon name="sliders" size={14} />
                {inputs.preferences_version ? "Your preferences" : "Default preferences"}
              </span>
              <span className="chip">
                <Icon name="file" size={14} />
                <strong>{inputs.documents}</strong> documents and notes
              </span>
              <span className="chip">
                <strong>{inputs.needs}</strong> stated needs
              </span>
              <span className="chip">
                <strong>{inputs.open_rounds}</strong> open rounds
              </span>
              {insights.fill_rate_percent !== null && (
                <span className="chip">
                  <strong>{insights.fill_rate_percent}%</strong> historical fill rate
                </span>
              )}
            </div>
          </div>
          <div className="actions">
            <button
              type="button"
              className="btn btn-secondary"
              onClick={() => onGoTo("preferences")}
            >
              <Icon name="sliders" size={16} /> Tune preferences
            </button>
            <button
              type="button"
              className="btn btn-ai"
              disabled={q.isFetching}
              onClick={() => q.refetch()}
            >
              <Icon name="refresh" size={16} />
              {q.isFetching ? "Regenerating…" : "Regenerate"}
            </button>
          </div>
        </div>
      </Card>

      {inputs.preferences_version === 0 && inputs.needs === 0 && (
        <Alert kind="info">
          These plans use neutral defaults and current requests only. For sharper plans,{" "}
          <button type="button" className="link-btn" onClick={() => onGoTo("preferences")}>
            set preferences
          </button>{" "}
          or{" "}
          <button type="button" className="link-btn" onClick={() => onGoTo("documents")}>
            upload a needs list
          </button>
          .
        </Alert>
      )}
      {inputs.open_rounds === 0 ? (
        <Card>
          <EmptyState icon={<Icon name="rounds" size={24} />} title="No open rounds right now">
            Plans appear here as soon as a round opens. Meanwhile, keep your preferences and needs
            up to date so the first draft is a good one.
          </EmptyState>
        </Card>
      ) : (
        <>
          <PlanPicker plans={data.plans} selected={plan.key} onSelect={setSelected} />
          <Card>
            <p className="plan-summary">{plan.summary}</p>
          </Card>
          {plan.warnings.map((w) => (
            <Alert key={w} kind="warning">
              {w}
            </Alert>
          ))}
          {!canApply && (
            <Alert kind="info">
              You are viewing {data.organization_name}&rsquo;s plans. Only its staff can submit them
              as requests.
            </Alert>
          )}
          {plan.rounds.length === 0 ? (
            <Card>
              <EmptyState icon={<Icon name="sparkles" size={24} />} title="Nothing to request">
                This plan found nothing in the open rounds that matches your priorities and needs.
                Try the Balanced plan, or add needs under{" "}
                <button type="button" className="link-btn" onClick={() => onGoTo("documents")}>
                  Documents &amp; notes
                </button>
                .
              </EmptyState>
            </Card>
          ) : (
            plan.rounds.map((r) => (
              <RoundPlan
                key={`${plan.key}-${r.round_id}`}
                round={r}
                canApply={canApply}
                orgId={orgId}
              />
            ))
          )}
          <p className="muted small">
            Generated {formatDateTime(data.generated_at)}. Plans are drafts computed from your data
            each time you open this page; they are never submitted automatically.
          </p>
        </>
      )}
    </>
  );
}
