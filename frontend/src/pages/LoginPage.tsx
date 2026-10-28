import { FormEvent, useState } from "react";
import { api, errorMessage } from "../lib/api";
import type { SessionInfo } from "../lib/types";

export default function LoginPage({ onLogin }: { onLogin: (s: SessionInfo) => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const session = await api<SessionInfo>("/api/v1/auth/login", {
        method: "POST",
        body: JSON.stringify({ username, password }),
      });
      onLogin(session);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="card narrow">
      <h1>Sign in</h1>
      <p className="badge" data-testid="synthetic-badge">Synthetic demo data</p>
      <p>Operator sign-in for the EnquiryTrail demo. All data is synthetic.</p>
      {error && <div role="alert" className="error">{error}</div>}
      <form onSubmit={submit} noValidate>
        <label htmlFor="login-username">Username</label>
        <input id="login-username" name="username" autoComplete="username" value={username}
          onChange={(e) => setUsername(e.target.value)} required />
        <label htmlFor="login-password">Password</label>
        <input id="login-password" name="password" type="password" autoComplete="current-password"
          value={password} onChange={(e) => setPassword(e.target.value)} required />
        <button type="submit" disabled={busy}>{busy ? "Signing in..." : "Sign in"}</button>
      </form>
    </main>
  );
}
