import { useQueryClient } from "@tanstack/react-query";
import { DatabaseForm } from "../components/DatabaseForm";
import { DatabaseIcon } from "../components/icons";
import { PageTitle } from "../components/PageTitle";

/** First start: nothing works until the studio has a database to keep its data in. */
export function SetupPage() {
  const queryClient = useQueryClient();
  return (
    <div className="mx-auto max-w-2xl space-y-6">
      <PageTitle icon={<DatabaseIcon />} title="Set up Migration Studio" subtitle="Choose the database that holds everything the studio records." />
      <div className="card space-y-3 p-5">
        <p className="text-sm text-subtle">
          Runs, documents, agents, workflows, domains, contracts and settings are all stored in this database, so backing it up backs up the studio. You can
          switch to another database later in Settings.
        </p>
        <DatabaseForm
          current={null}
          switching={false}
          onDone={(status) => {
            queryClient.setQueryData(["database"], status);
            queryClient.invalidateQueries();
          }}
        />
      </div>
    </div>
  );
}
