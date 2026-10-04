import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, Link, NavLink, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";
import { api } from "./lib/api";
import { useCopilotStatus } from "./lib/hooks";
import { LogoMark } from "./components/icons";
import { AgentsPage } from "./pages/AgentsPage";
import { Dashboard } from "./pages/Dashboard";
import { DomainPage } from "./pages/DomainPage";
import { DomainsPage } from "./pages/DomainsPage";
import { RunPage } from "./pages/RunPage";
import { SettingsPage } from "./pages/SettingsPage";
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
    <Link to="/settings" className="ml-auto flex items-center gap-2 rounded-full bg-white/10 px-3 py-1.5 text-xs text-white/90 ring-1 ring-white/20 hover:bg-white/20">
      <span className={`h-2 w-2 rounded-full ${status.signed_in ? "bg-emerald-300" : "bg-amber-300"}`} />
      {status.signed_in ? `Copilot: ${status.login}` : "Copilot: sign in"}
    </Link>
  );
}

function App() {
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
            <NavLink to="/workflows" className={navLink}>
              Workflows
            </NavLink>
            <NavLink to="/agents" className={navLink}>
              Agents
            </NavLink>
            <NavLink to="/settings" className={navLink}>
              Settings
            </NavLink>
          </nav>
          <CopilotChip />
        </div>
      </header>
      <div className="mx-auto max-w-7xl px-4 py-8">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/runs/:runId" element={<RunPage />} />
          <Route path="/domains" element={<DomainsPage />} />
          <Route path="/domains/:domainKey" element={<DomainPage />} />
          <Route path="/workflows" element={<WorkflowsPage />} />
          <Route path="/workflows/:workflowKey" element={<WorkflowsPage />} />
          <Route path="/agents" element={<AgentsPage />} />
          <Route path="/agents/:agentKey" element={<AgentsPage />} />
          <Route path="/settings" element={<SettingsPage />} />
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
