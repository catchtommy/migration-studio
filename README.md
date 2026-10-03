# Migration Studio

A web UI that drives the migration of the Java 8 / Spring MVC 3 / iBatis monolith into Spring Boot microservices,
one domain operation at a time. Each operation goes through a workflow of agent stages and human approval gates.
Workflows are editable (Workflows page); the built-in *Standard migration* workflow is:

AS-IS docs → Deep dive → 👤 → Boundary design → 👤 → Module in monolith → Spring Boot microservice → Independent tests → 👤 → Modernisation → Monolith integration → 👤

## Run it

```bash
./dev.sh            # backend on :8000, UI on http://localhost:5173
```

First-time setup:

```bash
cd backend && python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
cd ../frontend && PATH=../.tools/node/bin:$PATH npm install
```

Node 20 lives in `.tools/node` (the system Node is too old for Vite).

## How it works

- **Run** = one `(domain, operation)` through one workflow; one LangGraph thread, checkpointed in
  `backend/data/checkpoints.db`. The run keeps a snapshot of its workflow, so editing or deleting a workflow only
  affects runs started afterwards. The graph is built per workflow snapshot (`orchestrator/graph.py`).
- **Workflows** (Workflows page): create, copy, edit and delete named workflows. Steps are agents from the library and
  approval gates; each gate chooses which earlier agent steps it reviews (and can send back to on rejection).
  Each domain can set a default workflow; the run form preselects it.
- **Hand-off between steps**: all steps share the run's git workspace, so code from earlier steps is simply there.
  Documents are passed explicitly: each agent step *uses the output of* all earlier agent steps (default) or selected
  ones, and gets their latest documents' paths, or their contents with *Include contents in prompt* (up to 200k
  characters; larger documents are listed for the agent to read).
- **Workspace**: each run clones the monolith (Settings → Monolith repository) into `backend/data/workspaces/<run id>`
  on branch `migration/<domain>/<operation>`. Every stage commits its output, so each attempt has a reviewable diff.
- **Gates** pause the graph with `interrupt()`. *Approve* continues. *Reject* requires comments: the workspace is reset
  to where the chosen stage started, and the stage re-runs with the comments as feedback.
  Artifacts can be edited and committed from the UI while a gate is open.
- **Failures** reset the stage's partial changes. *Retry* resumes from the failed stage; completed stages are not repeated.
- **Agents** (Agents page): a library of agents; the eight built-in ones can be reset to their defaults, and custom
  agents can be added (and deleted once no workflow or unfinished run uses them). Each has a title, mode (*Stub* writes a placeholder; *Claude* runs for real),
  model, effort, JDK, instructions (its system prompt), enabled tools, required documents and a turn limit.
  The Claude agent (`backend/app/agents/llm.py`) works in the run workspace with the tools in `agents/tools.py`
  and is sent back if a required document is missing. Defaults live in `agents/defaults.py` (*Reset to default*).
  It needs Anthropic credentials in the backend's environment (e.g. `ANTHROPIC_API_KEY`).
- **Domains** (Domains page): add and edit domains; give every agent shared context, and each stage's agent its own
  *where to look* paths and notes. Starting a run can add operation-specific entry points and notes. The domain's
  *Documentation* tab shows the latest documents of every operation.
- **Questions**: agents can ask questions (`ask_user`). The run shows *Needs input* and an answer panel; the stage
  continues once answered (or skipped). A restart while waiting fails the run; *Retry* asks again, reusing answers
  already given to the same question.

## Tests

```bash
cd backend && .venv/bin/pytest            # workflow routing: approve, reject/rollback, retry, manual edits
cd frontend && npm test                   # unit tests
cd frontend && npm run e2e                # full run through the UI (needs ./dev.sh running)
# Question flow: start the backend with STUDIO_STUB_QUESTIONS=1 (stub agents then ask one question per stage)
# and run `E2E_STUB_QUESTIONS=1 npm run e2e`. STUDIO_API / E2E_BASE_URL point Vite / Playwright at another stack.
```
