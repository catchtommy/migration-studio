import type { Permission } from "../lib/api";
import { useCan } from "../lib/hooks";

export const PERMISSION_LABELS: Record<Permission, string> = {
  "agents.manage": "Manage agents",
  "workflows.manage": "Manage workflows",
  "domains.manage": "Manage domains & features",
  "runs.start": "Run migrations",
  "runs.approve": "Approve workflow runs",
  "runs.edit": "Edit documents & answer agents",
  "project.manage": "Manage project",
  "settings.manage": "Manage global settings & database",
  "users.manage": "Manage users & roles",
  "projects.manage": "Manage projects",
};

/** Controls inside are disabled for users without the permission (the backend refuses the change anyway).
 * `quiet` leaves out the note, for repeated rows. */
export function Guarded({ permission, quiet, children }: { permission: Permission; quiet?: boolean; children: React.ReactNode }) {
  const can = useCan(permission);
  return (
    <fieldset disabled={!can} className="contents">
      {!can && !quiet && (
        <p className="mb-2 rounded-md border border-border bg-muted/50 px-3 py-1.5 text-xs text-subtle">
          View only: changing this needs the “{PERMISSION_LABELS[permission]}” permission.
        </p>
      )}
      {children}
    </fieldset>
  );
}
