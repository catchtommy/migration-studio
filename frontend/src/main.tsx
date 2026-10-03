import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, NavLink, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AgentsPage } from "./pages/AgentsPage";
import { Dashboard } from "./pages/Dashboard";
import { DomainPage } from "./pages/DomainPage";
import { DomainsPage } from "./pages/DomainsPage";
import { RunPage } from "./pages/RunPage";
import { SettingsPage } from "./pages/SettingsPage";
import { WorkflowsPage } from "./pages/WorkflowsPage";
import "./index.css";

const queryClient = new QueryClient({ defaultOptions: { queries: { refetchOnWindowFocus: false } } });

const navLink = ({ isActive }: { isActive: boolean }) => `text-sm ${isActive ? "font-medium text-fg" : "text-subtle hover:text-fg"}`;

function App() {
  return (
    <div className="min-h-screen">
      <header className="border-b border-border bg-surface">
        <div className="mx-auto flex max-w-7xl items-center gap-6 px-4 py-3">
          <span className="font-semibold">Migration Studio</span>
          <nav className="flex gap-4">
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
        </div>
      </header>
      <div className="mx-auto max-w-7xl px-4 py-6">
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
