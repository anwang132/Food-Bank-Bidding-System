import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import type { ReactNode } from "react";

import type { Role } from "./api/types";
import { useAuth } from "./auth/AuthContext";
import { Layout } from "./components/Layout";
import { Loading } from "./components/ui";
import { AuditPage } from "./pages/admin/AuditPage";
import { InventoryPage } from "./pages/admin/InventoryPage";
import { NewRoundPage } from "./pages/admin/NewRoundPage";
import { OrganizationsPage } from "./pages/admin/OrganizationsPage";
import { UsersPage } from "./pages/admin/UsersPage";
import { LoginPage } from "./pages/LoginPage";
import { MessagesPage } from "./pages/messages/MessagesPage";
import { OverviewPage } from "./pages/OverviewPage";
import { PlanningPage } from "./pages/planning/PlanningPage";
import { PublicPage } from "./pages/PublicPage";
import { ResultsPage } from "./pages/ResultsPage";
import { RoundPage } from "./pages/RoundPage";
import { RoundsPage } from "./pages/RoundsPage";

function RequireAuth({ children }: { children: ReactNode }) {
  const { me, loading } = useAuth();
  const location = useLocation();
  if (loading) return <Loading label="Checking your session…" />;
  if (!me) return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  return <>{children}</>;
}

/** Hides screens the role cannot use. The server enforces the same rules. */
function RequireRole({ roles, children }: { roles: Role[]; children: ReactNode }) {
  const { me } = useAuth();
  if (!me || !roles.includes(me.user.role)) {
    return (
      <section className="state-page">
        <h1>Not available</h1>
        <p role="alert">Your account does not have access to this page.</p>
      </section>
    );
  }
  return <>{children}</>;
}

function Home() {
  const { me } = useAuth();
  return <Navigate to={me?.user.role === "viewer" ? "/public" : "/overview"} replace />;
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route
        element={
          <RequireAuth>
            <Layout />
          </RequireAuth>
        }
      >
        <Route index element={<Home />} />
        <Route path="/public" element={<PublicPage />} />
        <Route
          path="/overview"
          element={
            <RequireRole roles={["admin", "member"]}>
              <OverviewPage />
            </RequireRole>
          }
        />
        <Route
          path="/rounds"
          element={
            <RequireRole roles={["admin", "member"]}>
              <RoundsPage />
            </RequireRole>
          }
        />
        <Route
          path="/rounds/:id"
          element={
            <RequireRole roles={["admin", "member"]}>
              <RoundPage />
            </RequireRole>
          }
        />
        <Route
          path="/rounds/:id/results"
          element={
            <RequireRole roles={["admin", "member"]}>
              <ResultsPage />
            </RequireRole>
          }
        />
        <Route
          path="/messages/:channelId?"
          element={
            <RequireRole roles={["admin", "member"]}>
              <MessagesPage />
            </RequireRole>
          }
        />
        <Route
          path="/planning"
          element={
            <RequireRole roles={["admin", "member"]}>
              <PlanningPage />
            </RequireRole>
          }
        />
        <Route
          path="/admin/rounds/new"
          element={
            <RequireRole roles={["admin"]}>
              <NewRoundPage />
            </RequireRole>
          }
        />
        <Route
          path="/admin/inventory"
          element={
            <RequireRole roles={["admin"]}>
              <InventoryPage />
            </RequireRole>
          }
        />
        <Route
          path="/admin/organizations"
          element={
            <RequireRole roles={["admin"]}>
              <OrganizationsPage />
            </RequireRole>
          }
        />
        <Route
          path="/admin/users"
          element={
            <RequireRole roles={["admin"]}>
              <UsersPage />
            </RequireRole>
          }
        />
        <Route
          path="/admin/audit"
          element={
            <RequireRole roles={["admin"]}>
              <AuditPage />
            </RequireRole>
          }
        />
        <Route
          path="*"
          element={
            <section className="state-page">
              <h1>Page not found</h1>
              <p className="muted">Check the address, or use the navigation above.</p>
            </section>
          }
        />
      </Route>
    </Routes>
  );
}
