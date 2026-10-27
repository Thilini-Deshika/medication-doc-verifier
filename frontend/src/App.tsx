import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, Navigate, Route, Routes } from "react-router-dom";
import { api, ApiError } from "./api";
import { Dashboard } from "./pages/Dashboard";
import { JobPage } from "./pages/JobPage";
import { Login } from "./pages/Login";

export function App() {
  const qc = useQueryClient();
  const me = useQuery({
    queryKey: ["me"],
    queryFn: api.me,
    retry: false,
    staleTime: 5 * 60 * 1000,
  });

  if (me.isLoading) return <div className="center-screen">Loading</div>;
  if (me.error && !(me.error instanceof ApiError && me.error.status === 401)) {
    return (
      <div className="center-screen">
        <div className="card narrow" role="alert">
          <h2>Cannot reach the server</h2>
          <p className="muted">{me.error.message}</p>
          <button className="btn" onClick={() => me.refetch()}>
            Try again
          </button>
        </div>
      </div>
    );
  }

  const user = me.data;
  if (!user) {
    return (
      <Routes>
        <Route path="*" element={<Login />} />
      </Routes>
    );
  }

  const logout = async () => {
    await api.logout().catch(() => undefined);
    qc.clear();
    window.location.assign("/");
  };

  return (
    <div className="app">
      <header className="topbar">
        <Link to="/" className="brand">
          Medication Doc Verifier
        </Link>
        <div className="topbar-right">
          <span className="muted">{user.email}</span>
          <button className="btn btn-ghost" onClick={logout}>
            Sign out
          </button>
        </div>
      </header>
      <main>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/jobs/:id" element={<JobPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </div>
  );
}
