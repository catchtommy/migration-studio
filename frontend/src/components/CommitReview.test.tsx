import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";
import { api, ApiError, type RunChanges, type RunDetail, type RunRepository } from "../lib/api";
import { CommitReview } from "./CommitReview";

vi.mock("../lib/hooks", () => ({ useCan: () => true }));

const run = { id: "r1", branch: "migration/loan/approve", stages: [], status: "awaiting_commit", current_stage: null, pending_commit: null } as unknown as RunDetail;

const monolith: RunRepository = {
  key: "monolith", name: "Monolith", url: "https://git.example.com/monolith.git", base_branch: "main", root: true, changed: true, pending: true, delivery: null,
};

const changes = (files: RunChanges["files"], extra: Partial<RunChanges> = {}): RunChanges => ({
  key: "monolith", name: "Monolith", repository: monolith.url, base_branch: "main", feature_branch: "migration/loan/approve", base_sha: "a".repeat(40),
  head_sha: "b".repeat(40), latest_sha: "c".repeat(40), behind: 0, remote_branch: null, previous: null, warning: null, message: "Migrate approve",
  files, conflicts: files.filter((f) => f.conflict).length, ...extra,
});

const file = (path: string, more: Partial<RunChanges["files"][number]> = {}) => ({
  path, status: "M", document: false, binary: false, upstream_changed: false, conflict: null, additions: 1, deletions: 0, ...more,
});

function show() {
  vi.spyOn(api, "accountGit").mockResolvedValue({ name: "Ada", email: "ada@example.com", credentials: [] } as never);
  return render(
    <MemoryRouter>
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <CommitReview run={run} />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

afterEach(() => vi.restoreAllMocks());

test("while the run's workspace is prepared, the page says so and shows the review once it is ready", async () => {
  const preparing = new ApiError(503, "Preparing the run's workspace: checking out its repositories and replaying its steps' changes…");
  const repositories = vi.spyOn(api, "runRepositories").mockRejectedValueOnce(preparing).mockResolvedValue([monolith]);
  vi.spyOn(api, "runChanges").mockResolvedValue(changes([file("src/App.java")]));
  show();
  expect(await screen.findByText(/Preparing the run's workspace/)).toBeInTheDocument();
  expect(await screen.findByRole("checkbox", { name: "Commit src/App.java" }, { timeout: 5000 })).toBeChecked();
  expect(repositories).toHaveBeenCalledTimes(2); // asked again, not shown as an error
}, 10_000);

test("another error is shown at once, not retried", async () => {
  const repositories = vi.spyOn(api, "runRepositories").mockRejectedValue(new ApiError(409, "This run doesn't work on a repository"));
  show();
  expect(await screen.findByText("This run doesn't work on a repository")).toBeInTheDocument();
  expect(repositories).toHaveBeenCalledTimes(1);
});

test("a run that changed no repository says so", async () => {
  vi.spyOn(api, "runRepositories").mockResolvedValue([{ ...monolith, changed: false, pending: false }]);
  show();
  expect(await screen.findByText("The run didn't change any repository.")).toBeInTheDocument();
});

test("the changed files are listed: code ticked to commit, documents not, conflicts called out", async () => {
  vi.spyOn(api, "runRepositories").mockResolvedValue([monolith]);
  vi.spyOn(api, "runChanges").mockResolvedValue(
    changes([file("src/App.java"), file("docs/as-is.md", { document: true }), file("src/Clash.java", { conflict: "content" })], { behind: 2 }),
  );
  show();
  expect(await screen.findByRole("checkbox", { name: "Commit src/App.java" })).toBeChecked();
  expect(screen.getByRole("checkbox", { name: "Commit docs/as-is.md" })).not.toBeChecked();
  expect(screen.getByText(/1 file conflict with main: resolve them before committing/)).toBeInTheDocument();
  expect(screen.getByText(/which has 2 new commits since the run started/)).toBeInTheDocument();
});

test("when everything is in git already there is nothing to commit", async () => {
  vi.spyOn(api, "runRepositories").mockResolvedValue([monolith]);
  vi.spyOn(api, "runChanges").mockResolvedValue(changes([], { unchanged_in_git: 3 }));
  show();
  expect(await screen.findByText(/Nothing new to commit here/)).toBeInTheDocument();
  await waitFor(() => expect(screen.getByText(/3 other files are already in git/)).toBeInTheDocument());
});
