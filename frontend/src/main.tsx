import React, { useEffect, useRef, useState } from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, Link, Navigate, NavLink, Route, Routes, useNavigate } from "react-router-dom";
import { QueryClient, QueryClientProvider, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, getCurrentProject, setCurrentProject, SIGNED_OUT_EVENT, type Me, type Permission, type Project } from "./lib/api";
import { useCopilotStatus, useMe, useProjects } from "./lib/hooks";
import { ProjectSwitch, rememberedProject, rememberProject } from "./lib/projectContext";
import { ProjectSwitcher } from "./components/ProjectSwitcher";
import { ProjectSettingsPage } from "./pages/ProjectSettingsPage";
import { ProjectsPage } from "./pages/ProjectsPage";
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
import { AccountPage } from "./pages/AccountPage";
import { WorkflowsPage } from "./pages/WorkflowsPage";
import "./index.css";

// Cached project data is kept per project: the current project is part of its cache key, so switching projects can
// never show another project's data. Data that isn't about a project is cached once.
const GLOBAL_QUERIES = new Set(["me", "accountGit", "projects", "database", "settings", "copilotStatus", "users", "roles", "permissions", "agentCatalog"]);
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
      queryKeyHashFn: (key) => JSON.stringify(GLOBAL_QUERIES.has(key[0] as string) ? key : [getCurrentProject(), ...key]),
    },
  },
});

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
  return <WithProject user={auth.user} sqlite={sqlite} />;
}

/** Picks the project to work in (the one used last, else the first) and lets the user switch. */
function WithProject({ user, sqlite }: { user: Me; sqlite: boolean }) {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const { data: projects, isLoading } = useProjects();
  const [selected, setSelected] = useState<string | null>(() => rememberedProject());
  const switching = useRef(false); // a switch to a project the list may not include yet (just created)
  if (isLoading || !projects) return null;
  const open = projects.filter((p) => p.member); // only members can open a project
  const chosen = open.find((p) => p.key === selected);
  if (!chosen && switching.current) return null; // switched to a project the list doesn't include yet: wait for it
  const current = chosen ?? open[0];
  const switchTo = (key: string) => {
    rememberProject(key);
    setCurrentProject(key);
    switching.current = true;
    setSelected(key);
    void queryClient.invalidateQueries({ queryKey: ["projects"] });
    navigate("/");
  };
  if (!current) {
    setCurrentProject(null);
    return (
      <ProjectSwitch.Provider value={switchTo}>
        <NoProjectShell user={user} sqlite={sqlite} />
      </ProjectSwitch.Provider>
    );
  }
  setCurrentProject(current.key); // before any child query runs
  if (chosen) switching.current = false;
  return (
    <ProjectSwitch.Provider value={switchTo}>
      <Shell key={current.key} user={user} project={current} projects={open} sqlite={sqlite} onSwitch={switchTo} />
    </ProjectSwitch.Provider>
  );
}

/** For someone who can't open any project: their account menu, and the global pages they may use (to create a project
 * or give people access), instead of a dead end. */
function NoProjectShell({ user, sqlite }: { user: Me; sqlite: boolean }) {
  const canGlobally = (p: Permission) => user.permissions.includes(p);
  return (
    <div className="min-h-screen">
      <header className="bg-gradient-to-r from-indigo-600 via-violet-600 to-fuchsia-600 shadow-lg shadow-indigo-500/20 dark:from-indigo-950 dark:via-violet-950 dark:to-fuchsia-950">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-x-6 gap-y-2 px-4 py-3">
          <Link to="/" className="flex items-center gap-2.5 text-white">
            <LogoMark />
            <span className="text-lg font-semibold tracking-tight">Migration Studio</span>
          </Link>
          <nav className="flex flex-wrap gap-1">
            {canGlobally("projects.manage") && (
              <NavLink to="/" end className={navLink}>
                Projects
              </NavLink>
            )}
            {canGlobally("users.manage") && (
              <NavLink to="/users" className={navLink}>
                Users
              </NavLink>
            )}
            {canGlobally("settings.manage") && (
              <NavLink to="/settings" className={navLink}>
                Settings
              </NavLink>
            )}
          </nav>
          <div className="ml-auto flex items-center gap-2">
            <AccountMenu user={user} />
          </div>
        </div>
      </header>
      {sqlite && <SqliteBanner />}
      <div className="mx-auto max-w-7xl px-4 py-8">
        <Routes>
          <Route
            path="/"
            element={
              canGlobally("projects.manage") ? (
                <ProjectsPage />
              ) : (
                <div className="mx-auto max-w-xl py-12 text-center">
                  <h1 className="text-xl font-semibold">You don't have access to any project yet</h1>
                  <p className="mt-2 text-sm text-subtle">Ask a project's admin to add you as a member, then reload this page. You can sign out from the menu at the top right.</p>
                </div>
              )
            }
          />
          {canGlobally("users.manage") && <Route path="/users" element={<UsersPage />} />}
          {canGlobally("settings.manage") && <Route path="/settings" element={<SettingsPage />} />}
          <Route path="/account" element={<AccountPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </div>
    </div>
  );
}

function Shell({ user, project, projects, sqlite, onSwitch }: { user: Me; project: Project; projects: Project[]; sqlite: boolean; onSwitch: (key: string) => void }) {
  const can = (p: Permission) => project.permissions.includes(p);
  const canGlobally = (p: Permission) => user.permissions.includes(p);
  const canManageUsers = canGlobally("users.manage");
  return (
    <div className="min-h-screen">
      <header className="bg-gradient-to-r from-indigo-600 via-violet-600 to-fuchsia-600 shadow-lg shadow-indigo-500/20 dark:from-indigo-950 dark:via-violet-950 dark:to-fuchsia-950">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-x-6 gap-y-2 px-4 py-3">
          <Link to="/" className="flex items-center gap-2.5 text-white">
            <LogoMark />
            <span className="text-lg font-semibold tracking-tight">Migration Studio</span>
          </Link>
          <ProjectSwitcher projects={projects} current={project} onSwitch={onSwitch} />
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
            <NavLink to="/project" className={navLink}>
              Project
            </NavLink>
            {canManageUsers && (
              <NavLink to="/users" className={navLink}>
                Users
              </NavLink>
            )}
            {canGlobally("settings.manage") && (
              <NavLink to="/settings" className={navLink}>
                Settings
              </NavLink>
            )}
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
          <Route path="/projects" element={<ProjectsPage />} />
          <Route path="/project" element={<ProjectSettingsPage />} />
          {canGlobally("settings.manage") && <Route path="/settings" element={<SettingsPage />} />}
          {canManageUsers && <Route path="/users" element={<UsersPage />} />}
          <Route path="/account" element={<AccountPage />} />
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
