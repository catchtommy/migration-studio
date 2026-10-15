import React, { useEffect } from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, Link, Navigate, NavLink, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, SIGNED_OUT_EVENT, type Permission } from "./lib/api";
import { useCopilotStatus, useMe } from "./lib/hooks";
import { AccountMenu } from "./components/AccountMenu";
import { LogoMark } from "./components/icons";
import { AgentsPage } from "./pages/AgentsPage";
import { Dashboard } from "./pages/Dashboard";
import { DomainPage } from "./pages/DomainPage";
import { DomainsPage } from "./pages/DomainsPage";
import { ContractsPage } from "./pages/ContractsPage";
import { RunPage } from "./pages/RunPage";
import { SettingsPage } from "./pages/SettingsPage";
import { SetupPage } from "./pages/SetupPage";
import { CreateAdminPage, SignInPage } from "./pages/SignInPage";
import { UsersPage } from "./pages/UsersPage";
import { WorkflowsPage } from "./pages/WorkflowsPage";
import "./index.css";

const queryClient = new QueryClient({ defaultOptions: { queries: { refetchOnWindowFocus: false } } });

const navLink = ({ isActive }: { isActive: boolean }) =>
  `rounded-full px-3 py-1.5 text-sm transition ${isActive ? "bg-white/20 font-medium text-white shadow-inner" : "text-white/75 hover:bg-white/10 hover:text-white"}`;

/** Whether agents can reach GitHub Copilot, shown on every page when Copilot is the AI provider. */
function CopilotChip() {
  const { data: settings } = useQuery({ queryKey: ["settings"], queryFn: api.settings });
  const { data: status } = useCopilotStatus();
  if (settings?.ai_provider !== "copilot" || !status) return null;
  return (
    <Link to="/settings" className="flex items-center gap-2 rounded-full bg-white/10 px-3 py-1.5 text-xs text-white/90 ring-1 ring-white/20 hover:bg-white/20">
      <span className={`h-2 w-2 rounded-full ${status.signed_in ? "bg-emerald-300" : "bg-amber-300"}`} />
      {status.signed_in ? `Copilot: ${status.login}` : "Copilot: sign in"}
    </Link>
  );
}

/** SQLite is fine for a try-out, not for a shared installation. */
function SqliteBanner() {
  return (
    <div className="border-b border-warn/40 bg-warn/10">
      <p className="mx-auto max-w-7xl px-4 py-2 text-sm">
        This studio stores its data in SQLite, which is for local development.{" "}
        <Link to="/settings#database" className="font-medium underline">
          Use PostgreSQL or Oracle
        </Link>{" "}
        for a shared or production installation.
      </p>
    </div>
  );
}

function App() {
  const { data: database, isLoading, error } = useQuery({ queryKey: ["database"], queryFn: api.databaseStatus });
  if (isLoading) return null;
  if (error) return <p className="p-8 text-bad">The backend is not reachable: {(error as Error).message}</p>;
  if (!database?.configured)
    return (
      <div className="min-h-screen px-4 py-12">
        <SetupPage />
      </div>
    );
  return <SignedIn sqlite={database.dialect === "sqlite"} />;
}

/** Everything past setup needs a signed-in user; the first visitor creates the admin. */
function SignedIn({ sqlite }: { sqlite: boolean }) {
  const queryClient = useQueryClient();
  const { data: auth, isLoading } = useMe();
  useEffect(() => {
    const signedOut = () => queryClient.invalidateQueries({ queryKey: ["me"] });
    window.addEventListener(SIGNED_OUT_EVENT, signedOut);
    return () => window.removeEventListener(SIGNED_OUT_EVENT, signedOut);
  }, [queryClient]);
  if (isLoading || !auth) return null;
  if (auth.needs_bootstrap) return <CreateAdminPage />;
  if (!auth.user) return <SignInPage />;
  const user = auth.user;
  const can = (p: Permission) => user.permissions.includes(p);
  const canManageUsers = can("users.manage");
  return (
    <div className="min-h-screen">
      <header className="bg-gradient-to-r from-indigo-600 via-violet-600 to-fuchsia-600 shadow-lg shadow-indigo-500/20 dark:from-indigo-950 dark:via-violet-950 dark:to-fuchsia-950">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-x-6 gap-y-2 px-4 py-3">
          <Link to="/" className="flex items-center gap-2.5 text-white">
            <LogoMark />
            <span className="text-lg font-semibold tracking-tight">Migration Studio</span>
          </Link>
          <nav className="flex flex-wrap gap-1">
            <NavLink to="/" end className={navLink}>
              Migrations
            </NavLink>
            <NavLink to="/domains" className={navLink}>
              Domains
            </NavLink>
            {can("workflows.manage") && (
              <NavLink to="/workflows" className={navLink}>
                Workflows
              </NavLink>
            )}
            <NavLink to="/contracts" className={navLink}>
              Contracts
            </NavLink>
            {can("agents.manage") && (
              <NavLink to="/agents" className={navLink}>
                Agents
              </NavLink>
            )}
            {canManageUsers && (
              <NavLink to="/users" className={navLink}>
                Users
              </NavLink>
            )}
            <NavLink to="/settings" className={navLink}>
              Settings
            </NavLink>
          </nav>
          <div className="ml-auto flex items-center gap-2">
            <CopilotChip />
            <AccountMenu user={user} />
          </div>
        </div>
      </header>
      {sqlite && <SqliteBanner />}
      <div className="mx-auto max-w-7xl px-4 py-8">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/runs/:runId" element={<RunPage />} />
          <Route path="/domains" element={<DomainsPage />} />
          <Route path="/domains/:domainKey" element={<DomainPage />} />
          {can("workflows.manage") && <Route path="/workflows" element={<WorkflowsPage />} />}
          {can("workflows.manage") && <Route path="/workflows/:workflowKey" element={<WorkflowsPage />} />}
          <Route path="/contracts" element={<ContractsPage />} />
          {can("agents.manage") && <Route path="/agents" element={<AgentsPage />} />}
          {can("agents.manage") && <Route path="/agents/:agentKey" element={<AgentsPage />} />}
          <Route path="/settings" element={<SettingsPage />} />
          {canManageUsers && <Route path="/users" element={<UsersPage />} />}
          {/* Pages the user may not open (or that don't exist) lead back to the start. */}
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </div>
    </div>
  );
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>,
);
