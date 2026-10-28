import { useQueries, useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { api } from "../api/client";
import type { Allocation, AuditEntry, Lot, Organization, Plans, RoundSummary } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { useChannels } from "../chat/queries";
import { Icon } from "../components/Icon";
import {
  Card,
  Empty,
  ErrorState,
  Loading,
  PageHeader,
  formatDate,
  Progress,
  StatCard,
  StatusBadge,
  relativeDeadline,
  timeAgo,
} from "../components/ui";

export function OverviewPage() {
  const { me } = useAuth();
  return me?.user.role === "admin" ? <AdminOverview /> : <MemberOverview />;
}

function firstName(name: string): string {
  return name.split(/\s+/)[0] ?? name;
}

function useRounds() {
  return useQuery({ queryKey: ["rounds"], queryFn: () => api<RoundSummary[]>("/api/rounds") });
}

function RoundLine({ r, cta }: { r: RoundSummary; cta?: string }) {
  return (
    <li className="list-row">
      <div className="list-main">
        <Link to={`/rounds/${r.id}`} className="list-title">
          {r.name}
        </Link>
        <span className="muted small">
          {r.strategy_label} · {r.lot_count} items ·{" "}
          {r.status === "open" ? relativeDeadline(r.request_deadline) : r.status}
        </span>
      </div>
      {cta ? (
        <Link to={`/rounds/${r.id}`} className="btn btn-secondary btn-sm">
          {cta}
        </Link>
      ) : (
        <StatusBadge status={r.status} />
      )}
    </li>
  );
}

function InboxCard() {
  const channels = useChannels();
  const list = [...(channels.data ?? [])]
    .sort(
      (a, b) =>
        b.unread_count - a.unread_count || b.last_activity_at.localeCompare(a.last_activity_at),
    )
    .slice(0, 4);
  const unread = (channels.data ?? []).reduce((n, c) => n + c.unread_count, 0);
  return (
    <Card
      title="Messages"
      description={unread ? `${unread} unread` : "You're all caught up."}
      actions={<Link to="/messages">Open messages</Link>}
    >
      {channels.isPending ? (
        <Loading />
      ) : list.length === 0 ? (
        <Empty>No conversations yet.</Empty>
      ) : (
        <ul className="list">
          {list.map((c) => (
            <li key={c.id} className="list-row">
              <div className="list-main">
                <Link to={`/messages/${c.id}`} className="list-title">
                  {c.title}
                </Link>
                <span className="muted small clamp">
                  {c.last_message
                    ? c.last_message.deleted
                      ? "Message deleted"
                      : `${c.last_message.sender ? `${firstName(c.last_message.sender.display_name)}: ` : ""}${c.last_message.body}`
                    : c.subtitle}
                </span>
              </div>
              {c.unread_count > 0 ? (
                <span className="unread-pill">
                  {c.unread_count}
                  <span className="sr-only"> unread</span>
                </span>
              ) : (
                c.last_message && (
                  <span className="muted small nowrap">{timeAgo(c.last_message.created_at)}</span>
                )
              )}
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

function PlanningTeaser({ openRounds }: { openRounds: number }) {
  // Shares its cache with the Planning page, so opening it next is instant.
  const plans = useQuery({
    queryKey: ["planning", "mine", "plans"],
    queryFn: () => api<Plans>("/api/planning/plans"),
    enabled: openRounds > 0,
  });
  return (
    <Card className="ai-hero" title="Planning assistant">
      <p>
        {openRounds
          ? `Sample request plans for ${openRounds === 1 ? "the open round" : `the ${openRounds} open rounds`}, drafted from your preferences, documents and history.`
          : "When a round opens, sample request plans are drafted here from your preferences, documents and history."}
      </p>
      {plans.data && (
        <ul className="plan-mini" aria-label="Sample plans">
          {plans.data.plans.map((p) => (
            <li key={p.key}>
              <strong>{p.total_units.toLocaleString()}</strong>
              {p.title}
            </li>
          ))}
        </ul>
      )}
      <div className="actions">
        <Link className="btn btn-ai btn-sm" to="/planning">
          <Icon name="sparkles" size={15} /> View sample plans
        </Link>
        <Link className="btn btn-ghost btn-sm" to="/planning?tab=documents">
          Upload needs
        </Link>
      </div>
    </Card>
  );
}

// ---------------------------------------------------------------- admin

const ACTIONS: Record<string, string> = {
  "round.create": "created round",
  "round.update": "updated round",
  "round.open": "opened round",
  "round.close": "closed round",
  "round.cancel": "cancelled round",
  "round.finalize": "finalized round",
  "round.lot_reserve": "reserved stock for round",
  "round.lot_change": "changed reserved stock in round",
  "round.lot_release": "released stock from round",
  "round.participant_set": "changed eligibility or weighting in round",
  "request.create": "submitted a request in round",
  "request.update": "edited a request in round",
  "request.withdraw": "withdrew a request in round",
  "lot.create": "added inventory lot",
  "lot.update": "adjusted inventory lot",
  "organization.create": "added organization",
  "organization.update": "updated organization",
  "user.create": "added user",
  "user.update": "updated user",
  "user.register": "registered as a viewer",
  "planning.preferences_update": "updated planning preferences for",
  "planning.document_upload": "uploaded",
  "planning.note_create": "added the planning note",
  "planning.document_delete": "deleted",
  "chat.message_moderate": "removed a message",
};

function describe(
  a: AuditEntry,
  roundNames: Map<number, string>,
  orgNames: Map<number, string>,
): string {
  const verb = ACTIONS[a.action] ?? a.action;
  const round = a.round_id ? roundNames.get(a.round_id) : undefined;
  if (round) return `${verb} “${round}”`;
  const named = a.after?.filename ?? a.after?.title ?? a.before?.title;
  if (typeof named === "string") return `${verb} “${named}”`;
  if (a.action === "chat.message_moderate") return verb;
  const org = a.target_type === "organization" ? orgNames.get(Number(a.target_id)) : undefined;
  if (org) return `${verb} ${org}`;
  return `${verb} #${a.target_id}`;
}

function AdminOverview() {
  const { me } = useAuth();
  const rounds = useRounds();
  const lots = useQuery({
    queryKey: ["admin", "lots"],
    queryFn: () => api<Lot[]>("/api/admin/lots"),
  });
  const orgs = useQuery({
    queryKey: ["admin", "orgs"],
    queryFn: () => api<Organization[]>("/api/admin/organizations"),
  });
  const audit = useQuery({
    queryKey: ["admin", "audit", "recent"],
    queryFn: () => api<AuditEntry[]>("/api/admin/audit?limit=8"),
  });

  if (rounds.isPending || lots.isPending || orgs.isPending) return <Loading />;
  if (rounds.isError) return <ErrorState error={rounds.error} onRetry={() => rounds.refetch()} />;
  const all = rounds.data;
  const open = all.filter((r) => r.status === "open");
  const closed = all.filter((r) => r.status === "closed");
  const overdue = open.filter((r) => r.deadline_passed);
  const lotList = lots.data ?? [];
  const available = lotList.reduce((n, l) => n + l.quantity_available, 0);
  const soon = Date.now() + 21 * 86_400_000;
  const expiring = lotList.filter(
    (l) => l.best_before && l.quantity_available > 0 && new Date(l.best_before).getTime() < soon,
  );
  const roundNames = new Map(all.map((r) => [r.id, r.name]));
  const orgNames = new Map((orgs.data ?? []).map((o) => [o.id, o.name]));
  const attention = closed.length + overdue.length + expiring.length;

  return (
    <section>
      <PageHeader
        title="Overview"
        subtitle={`Welcome back, ${firstName(me!.user.display_name)}. Here is where the network stands today.`}
        actions={
          <Link className="btn btn-primary" to="/admin/rounds/new">
            New round
          </Link>
        }
      />
      <div className="stats-grid">
        <StatCard label="Open rounds" value={open.length} hint="accepting requests" />
        <StatCard
          label="Awaiting finalization"
          value={closed.length}
          tone={closed.length ? "attention" : "default"}
          hint={closed.length ? "ready to preview" : "nothing pending"}
        />
        <StatCard
          label="Units available"
          value={available.toLocaleString()}
          hint={`${lotList.length} inventory lots`}
        />
        <StatCard
          label="Organizations"
          value={(orgs.data ?? []).filter((o) => o.is_active).length}
          hint="active partners"
        />
      </div>

      <div className="grid-2">
        <Card title="Needs attention" description={attention ? undefined : "All caught up."}>
          {attention === 0 ? (
            <Empty>Nothing needs your attention right now.</Empty>
          ) : (
            <ul className="list">
              {closed.map((r) => (
                <RoundLine key={r.id} r={r} cta="Preview & finalize" />
              ))}
              {overdue.map((r) => (
                <RoundLine key={r.id} r={r} cta="Close requests" />
              ))}
              {expiring.map((l) => (
                <li key={`lot-${l.id}`} className="list-row">
                  <div className="list-main">
                    <Link to="/admin/inventory" className="list-title">
                      {l.name}
                    </Link>
                    <span className="muted small">
                      {l.quantity_available} {l.unit} unreserved · best before{" "}
                      {formatDate(l.best_before)}
                    </span>
                  </div>
                  <span className="badge badge-closed">
                    <span className="badge-dot" aria-hidden="true" />
                    Expiring soon
                  </span>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card title="Recent activity" actions={<Link to="/admin/audit">Audit log</Link>}>
          {audit.isPending ? (
            <Loading />
          ) : (audit.data ?? []).length === 0 ? (
            <Empty>No activity yet.</Empty>
          ) : (
            <ul className="feed">
              {audit.data!.map((a) => (
                <li key={a.id}>
                  <span className="feed-who">{a.actor_name ?? "System"}</span>{" "}
                  {describe(a, roundNames, orgNames)}
                  {a.reason && <span className="muted"> — {a.reason}</span>}
                  <time className="muted small block" dateTime={a.created_at}>
                    {timeAgo(a.created_at)}
                  </time>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      <div className="grid-2">
        <Card title="Open rounds" actions={<Link to="/rounds">All rounds</Link>}>
          {open.length === 0 ? (
            <Empty>No rounds are open. Create one to start collecting requests.</Empty>
          ) : (
            <ul className="list">
              {open.map((r) => (
                <RoundLine key={r.id} r={r} />
              ))}
            </ul>
          )}
        </Card>
        <InboxCard />
      </div>
    </section>
  );
}

// ---------------------------------------------------------------- member

function MemberOverview() {
  const { me } = useAuth();
  const rounds = useRounds();
  const allocated = (rounds.data ?? [])
    .filter((r) => r.status === "allocated")
    .sort((a, b) => (b.allocated_at ?? "").localeCompare(a.allocated_at ?? ""))
    .slice(0, 3);
  const results = useQueries({
    queries: allocated.map((r) => ({
      queryKey: ["results", r.id],
      queryFn: () => api<Allocation>(`/api/rounds/${r.id}/results`),
    })),
  });

  if (rounds.isPending) return <Loading />;
  if (rounds.isError) return <ErrorState error={rounds.error} onRetry={() => rounds.refetch()} />;
  const open = rounds.data.filter((r) => r.status === "open");
  const active = open.reduce((n, r) => n + (r.my_request_count ?? 0), 0);

  return (
    <section>
      <PageHeader
        title="Overview"
        subtitle={`Welcome back, ${firstName(me!.user.display_name)} — ${me!.organization?.name ?? ""}`}
      />
      <div className="stats-grid">
        <StatCard label="Open rounds" value={open.length} hint="accepting requests" />
        <StatCard label="Your active requests" value={active} hint="in open rounds" />
        <StatCard
          label="Rounds allocated"
          value={rounds.data.filter((r) => r.status === "allocated").length}
          hint="with results available"
        />
      </div>

      <div className="grid-2">
        <PlanningTeaser openRounds={open.length} />
        <InboxCard />
      </div>

      <h2 className="section-title">Open for requests</h2>
      {open.length === 0 ? (
        <Empty>No rounds are open right now. New rounds appear here automatically.</Empty>
      ) : (
        <div className="cards-grid">
          {open.map((r) => (
            <article key={r.id} className="card round-card">
              <div className="round-card-head">
                <h3>
                  <Link to={`/rounds/${r.id}`}>{r.name}</Link>
                </h3>
                <span className={`deadline ${r.deadline_passed ? "deadline-passed" : ""}`}>
                  {relativeDeadline(r.request_deadline)}
                </span>
              </div>
              <p className="muted small">{r.description}</p>
              <dl className="mini-stats">
                <div>
                  <dt>Items</dt>
                  <dd>{r.lot_count}</dd>
                </div>
                <div>
                  <dt>Your requests</dt>
                  <dd>{r.my_request_count ?? 0}</dd>
                </div>
                <div>
                  <dt>Strategy</dt>
                  <dd>{r.strategy_label}</dd>
                </div>
              </dl>
              <Link className="btn btn-primary btn-sm" to={`/rounds/${r.id}`}>
                {r.my_request_count ? "Review your requests" : "Make requests"}
              </Link>
            </article>
          ))}
        </div>
      )}

      <h2 className="section-title">Recent results</h2>
      {allocated.length === 0 ? (
        <Empty>Results appear here once a round is finalized.</Empty>
      ) : (
        <Card>
          <ul className="list">
            {allocated.map((r, i) => {
              const data = results[i]?.data;
              const lines = data?.lots.flatMap((l) => l.lines) ?? [];
              const requested = lines.reduce((n, l) => n + l.requested, 0);
              const received = lines.reduce((n, l) => n + l.allocated, 0);
              return (
                <li key={r.id} className="list-row">
                  <div className="list-main">
                    <Link to={`/rounds/${r.id}/results`} className="list-title">
                      {r.name}
                    </Link>
                    <span className="muted small">
                      {data
                        ? lines.length
                          ? `Received ${received} of ${requested} units requested`
                          : "Your organization did not request in this round"
                        : "Loading…"}
                    </span>
                  </div>
                  {data && lines.length > 0 && (
                    <div className="list-meter">
                      <Progress value={received} max={requested} label={`${r.name} fulfilment`} />
                      <span className="small meter-pct">
                        {Math.round((received / requested) * 100)}%
                      </span>
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
        </Card>
      )}
    </section>
  );
}
