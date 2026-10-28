import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";

import { api } from "../../api/client";
import type { Organization } from "../../api/types";
import { useAuth } from "../../auth/AuthContext";
import { Icon } from "../../components/Icon";
import { ErrorState, Loading, PageHeader } from "../../components/ui";
import { DocumentsTab } from "./DocumentsTab";
import { PlansTab } from "./PlansTab";
import { PreferencesTab } from "./PreferencesTab";

const TABS = [
  { key: "plans", label: "Sample plans" },
  { key: "preferences", label: "Preferences" },
  { key: "documents", label: "Documents & notes" },
] as const;
type Tab = (typeof TABS)[number]["key"];

export function PlanningPage() {
  const { me } = useAuth();
  const isAdmin = me?.user.role === "admin";
  const [params, setParams] = useSearchParams();
  const tab: Tab = TABS.some((t) => t.key === params.get("tab"))
    ? (params.get("tab") as Tab)
    : "plans";

  const orgs = useQuery({
    queryKey: ["admin", "orgs"],
    queryFn: () => api<Organization[]>("/api/admin/organizations"),
    enabled: isAdmin,
  });
  const active = (orgs.data ?? []).filter((o) => o.is_active);
  const chosen = Number(params.get("org")) || null;
  const orgId = isAdmin ? (chosen ?? active[0]?.id ?? null) : null;
  const orgName = isAdmin
    ? active.find((o) => o.id === orgId)?.name
    : (me?.organization?.name ?? undefined);

  const update = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    next.set(key, value);
    setParams(next, { replace: true });
  };

  return (
    <section>
      <PageHeader
        title="Planning assistant"
        badge={
          <span className="badge badge-ai">
            <Icon name="sparkles" size={14} /> Simulated AI
          </span>
        }
        subtitle={
          <>
            Draft request plans for {orgName ?? "your food bank"}, built from your preferences,
            documents and history. Nothing is submitted until you choose to apply a plan.
          </>
        }
        actions={
          isAdmin && active.length > 0 ? (
            <div className="org-picker">
              <label htmlFor="planning-org">Food bank</label>
              <select
                id="planning-org"
                value={orgId ?? ""}
                onChange={(e) => update("org", e.target.value)}
              >
                {active.map((o) => (
                  <option key={o.id} value={o.id}>
                    {o.name}
                  </option>
                ))}
              </select>
            </div>
          ) : undefined
        }
      />
      {isAdmin && orgs.isPending ? (
        <Loading />
      ) : isAdmin && orgs.isError ? (
        <ErrorState error={orgs.error} onRetry={() => orgs.refetch()} />
      ) : (
        <>
          <div
            className="tabs"
            role="tablist"
            aria-label="Planning sections"
            onKeyDown={(e) => {
              const i = TABS.findIndex((t) => t.key === tab);
              const next =
                e.key === "ArrowRight"
                  ? (i + 1) % TABS.length
                  : e.key === "ArrowLeft"
                    ? (i - 1 + TABS.length) % TABS.length
                    : e.key === "Home"
                      ? 0
                      : e.key === "End"
                        ? TABS.length - 1
                        : null;
              if (next === null) return;
              e.preventDefault();
              update("tab", TABS[next]!.key);
              document.getElementById(`tab-${TABS[next]!.key}`)?.focus();
            }}
          >
            {TABS.map((t) => (
              <button
                key={t.key}
                type="button"
                role="tab"
                id={`tab-${t.key}`}
                aria-selected={tab === t.key}
                aria-controls={`panel-${t.key}`}
                tabIndex={tab === t.key ? 0 : -1}
                onClick={() => update("tab", t.key)}
              >
                {t.label}
              </button>
            ))}
          </div>
          <div role="tabpanel" id={`panel-${tab}`} aria-labelledby={`tab-${tab}`}>
            {tab === "plans" && (
              <PlansTab orgId={orgId} canApply={!isAdmin} onGoTo={(t) => update("tab", t)} />
            )}
            {tab === "preferences" && <PreferencesTab key={orgId ?? "mine"} orgId={orgId} />}
            {tab === "documents" && <DocumentsTab orgId={orgId} />}
          </div>
        </>
      )}
    </section>
  );
}
