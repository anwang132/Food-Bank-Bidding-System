import { useEffect, useState } from "react";
import { Link, NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";

import type { Role } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { useUnreadTotal } from "../chat/queries";
import { useRealtime } from "../realtime/RealtimeProvider";
import { Icon } from "./Icon";
import type { IconName } from "./Icon";
import { Avatar, Logo } from "./ui";

function LiveIndicator() {
  const { status, instance } = useRealtime();
  const text =
    status === "live"
      ? "Live updates on"
      : status === "connecting"
        ? "Connecting…"
        : "Reconnecting — refreshing every 15 s";
  return (
    <span
      className={`live live-${status}`}
      role="status"
      aria-live="polite"
      title={instance ? `Connected to ${instance}` : undefined}
      data-instance={instance ?? ""}
      data-testid="live-status"
    >
      <span className="dot" aria-hidden="true" />
      <span className="live-text">{text}</span>
    </span>
  );
}

const ROLE_LABEL = { admin: "Administrator", member: "Member", viewer: "Viewer" } as const;

interface NavItem {
  to: string;
  label: string;
  icon: IconName;
  roles: Role[];
  tag?: string;
  badge?: number;
}

function navGroups(unread: number): { label: string; items: NavItem[] }[] {
  const staff: Role[] = ["admin", "member"];
  return [
    {
      label: "Workspace",
      items: [
        { to: "/overview", label: "Overview", icon: "overview", roles: staff },
        { to: "/rounds", label: "Rounds", icon: "rounds", roles: staff },
        { to: "/messages", label: "Messages", icon: "messages", roles: staff, badge: unread },
        { to: "/planning", label: "Planning", icon: "sparkles", roles: staff, tag: "AI" },
      ],
    },
    {
      label: "Administration",
      items: [
        { to: "/admin/inventory", label: "Inventory", icon: "inventory", roles: ["admin"] },
        {
          to: "/admin/organizations",
          label: "Organizations",
          icon: "organizations",
          roles: ["admin"],
        },
        { to: "/admin/users", label: "Users", icon: "users", roles: ["admin"] },
        { to: "/admin/audit", label: "Audit log", icon: "audit", roles: ["admin"] },
      ],
    },
    {
      label: "Network",
      items: [
        {
          to: "/public",
          label: "Public summary",
          icon: "globe",
          roles: ["admin", "member", "viewer"],
        },
      ],
    },
  ];
}

export function Layout() {
  const { me, logout } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const unread = useUnreadTotal();
  const [menuOpen, setMenuOpen] = useState(false);

  // The drawer (phones) closes on navigation and on Escape.
  useEffect(() => setMenuOpen(false), [location.pathname]);
  useEffect(() => {
    if (!menuOpen) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setMenuOpen(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [menuOpen]);

  if (!me) return null;
  const role = me.user.role;
  const groups = navGroups(unread)
    .map((g) => ({ ...g, items: g.items.filter((i) => i.roles.includes(role)) }))
    .filter((g) => g.items.length > 0);

  return (
    <div className={`app ${menuOpen ? "menu-open" : ""}`} data-testid="app-shell">
      <a href="#main" className="skip-link">
        Skip to main content
      </a>
      <header className="mobile-bar">
        <button
          type="button"
          className="icon-btn"
          aria-label="Open menu"
          aria-expanded={menuOpen}
          aria-controls="sidebar"
          onClick={() => setMenuOpen(true)}
        >
          <Icon name="menu" size={20} />
          {unread > 0 && <span className="dot-badge" aria-hidden="true" />}
        </button>
        <Link to="/" className="brand" aria-label="Food Bank Allocation home">
          <Logo />
          <span>Food Bank Allocation</span>
        </Link>
        <Avatar name={me.user.display_name} />
      </header>

      <aside id="sidebar" className="sidebar" aria-label="Sidebar">
        <div className="sidebar-head">
          <Link to="/" className="brand" aria-label="Food Bank Allocation home">
            <Logo />
            <span>Food Bank Allocation</span>
          </Link>
          <button
            type="button"
            className="icon-btn sidebar-close"
            aria-label="Close menu"
            onClick={() => setMenuOpen(false)}
          >
            <Icon name="close" />
          </button>
        </div>
        <nav aria-label="Main" className="side-nav">
          {groups.map((group) => (
            <div key={group.label} className="nav-group">
              <p className="nav-group-label">{group.label}</p>
              <ul>
                {group.items.map((item) => (
                  <li key={item.to}>
                    <NavLink
                      to={item.to}
                      className={({ isActive }) => (isActive ? "nav-link active" : "nav-link")}
                    >
                      <Icon name={item.icon} />
                      <span className="nav-label">{item.label}</span>
                      {item.tag && <span className="nav-tag">{item.tag}</span>}
                      {item.badge ? (
                        <span className="nav-badge">
                          {item.badge > 99 ? "99+" : item.badge}
                          <span className="sr-only"> unread</span>
                        </span>
                      ) : null}
                    </NavLink>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </nav>
        <div className="sidebar-foot">
          <LiveIndicator />
          <div className="account">
            <Avatar name={me.user.display_name} />
            <span className="account-text">
              <span className="account-name">{me.user.display_name}</span>
              <small>{me.organization ? me.organization.name : ROLE_LABEL[role]}</small>
            </span>
            <button
              type="button"
              className="icon-btn"
              aria-label="Sign out"
              title="Sign out"
              onClick={async () => {
                await logout();
                navigate("/login");
              }}
            >
              <Icon name="logout" />
            </button>
          </div>
        </div>
      </aside>
      <div className="scrim" aria-hidden="true" onClick={() => setMenuOpen(false)} />

      <div className="app-main">
        <main id="main" tabIndex={-1}>
          <Outlet />
        </main>
        <footer className="footer">
          <span>© Food Bank Allocation network</span>
          <span>Requests are quantities, never bids for money · Every result is explained</span>
        </footer>
      </div>
    </div>
  );
}
