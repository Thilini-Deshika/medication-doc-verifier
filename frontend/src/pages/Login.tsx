import { useMutation, useQueryClient } from "@tanstack/react-query";
import { FormEvent, useState } from "react";
import { api } from "../api";

export function Login() {
  const qc = useQueryClient();
  const [mode, setMode] = useState<"login" | "signup">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");

  const auth = useMutation({
    mutationFn: () => (mode === "login" ? api.login(email, password) : api.signup(email, password)),
    onSuccess: (user) => qc.setQueryData(["me"], user),
  });

  const submit = (e: FormEvent) => {
    e.preventDefault();
    auth.mutate();
  };

  return (
    <div className="center-screen">
      <form className="card narrow" onSubmit={submit}>
        <h1>{mode === "login" ? "Sign in" : "Create account"}</h1>
        <p className="muted">Verify discharge documents against an institutional reference.</p>
        <label>
          Email
          <input type="email" required autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} />
        </label>
        <label>
          Password
          <input
            type="password"
            required
            minLength={8}
            autoComplete={mode === "login" ? "current-password" : "new-password"}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>
        {mode === "signup" && <div className="muted small">At least 8 characters.</div>}
        {auth.error && (
          <div className="field-error" role="alert">
            {auth.error.message}
          </div>
        )}
        <button className="btn btn-primary" disabled={auth.isPending}>
          {auth.isPending ? "Please wait" : mode === "login" ? "Sign in" : "Create account"}
        </button>
        <button
          type="button"
          className="link"
          onClick={() => {
            auth.reset();
            setMode(mode === "login" ? "signup" : "login");
          }}
        >
          {mode === "login" ? "No account yet? Create one" : "Already have an account? Sign in"}
        </button>
      </form>
    </div>
  );
}
