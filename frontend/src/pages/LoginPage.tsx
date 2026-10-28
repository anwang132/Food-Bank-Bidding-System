import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";

import { errorMessage } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { Icon } from "../components/Icon";
import type { IconName } from "../components/Icon";
import { Alert, Field, Logo } from "../components/ui";

const POINTS: [IconName, string, string][] = [
  [
    "rounds",
    "Request what you need",
    "Submit, adjust or withdraw quantities until a round closes.",
  ],
  ["check", "Clear, explainable rules", "Every allocation shows exactly how it was decided."],
  ["messages", "One network, one conversation", "Message your region, a group, or anyone."],
  ["sparkles", "Plans drafted for you", "Sample requests built from your needs and history."],
];

export function LoginPage() {
  const { me, login, expired } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const from = (location.state as { from?: string } | null)?.from ?? "/";

  useEffect(() => {
    document.title = "Sign in · Food Bank Allocation";
  }, []);

  if (me) return <Navigate to={from} replace />;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(email, password);
      navigate(from, { replace: true });
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="login">
      <aside className="login-brand">
        <div className="brand brand-light">
          <Logo />
          <span>Food Bank Allocation</span>
        </div>
        <div>
          <h2>Share donated food fairly across your network.</h2>
          <ul className="login-points">
            {POINTS.map(([icon, title, body]) => (
              <li key={title}>
                <span className="point-icon" aria-hidden="true">
                  <Icon name={icon} />
                </span>
                <strong>{title}</strong>
                <span>{body}</span>
              </li>
            ))}
          </ul>
        </div>
        <p className="login-foot">For partner food banks and network coordinators.</p>
      </aside>
      <main id="main" className="login-main">
        <form className="login-card" onSubmit={submit}>
          <div className="brand login-mobile-brand">
            <Logo />
            <span>Food Bank Allocation</span>
          </div>
          <div>
            <h1>Sign in</h1>
            <p className="muted">Use the account your network administrator created for you.</p>
          </div>
          {expired && <Alert kind="warning">Your session ended. Please sign in again.</Alert>}
          {error && <Alert kind="error">{error}</Alert>}
          <Field label="Email">
            {(id) => (
              <input
                id={id}
                type="email"
                autoComplete="username"
                required
                placeholder="you@organization.org"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
              />
            )}
          </Field>
          <Field label="Password">
            {(id) => (
              <input
                id={id}
                type="password"
                autoComplete="current-password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
            )}
          </Field>
          <button type="submit" className="btn btn-primary btn-block" disabled={busy}>
            {busy ? "Signing in…" : "Sign in"}
          </button>
        </form>
      </main>
    </div>
  );
}
