import { act, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import type { AuditEvent, RunDetail } from "./api";
import { RUN_EVENTS_FLUSH_MS, useRunEvents } from "./hooks";

/** A stand-in for the browser's EventSource: the test sends the server's events. */
class FakeEventSource {
  static last: FakeEventSource;
  listeners: Record<string, ((e: MessageEvent) => void)[]> = {};
  closed = false;
  constructor(public url: string) {
    FakeEventSource.last = this;
  }
  addEventListener(type: string, fn: (e: MessageEvent) => void) {
    (this.listeners[type] ??= []).push(fn);
  }
  close() {
    this.closed = true;
  }
  send(event: Partial<AuditEvent> & { id: number }) {
    const data = JSON.stringify({ stage: null, kind: "log", message: `event ${event.id}`, created_at: "2026-11-22T10:00:00Z", ...event });
    for (const fn of this.listeners.audit ?? []) fn({ data } as MessageEvent);
  }
}

function Events({ runId }: { runId: string }) {
  const events = useRunEvents(runId);
  return <p data-testid="count">{events.length}</p>;
}

let client: QueryClient;
let invalidated: unknown[];

function show(lastEventId: number) {
  client = new QueryClient();
  client.setQueryData<Partial<RunDetail>>(["run", "r1"], { id: "r1", last_event_id: lastEventId });
  invalidated = [];
  vi.spyOn(client, "invalidateQueries").mockImplementation(async (filters) => {
    invalidated.push(filters?.queryKey);
  });
  return render(
    <QueryClientProvider client={client}>
      <Events runId="r1" />
    </QueryClientProvider>,
  );
}

const tick = () => act(() => vi.advanceTimersByTime(RUN_EVENTS_FLUSH_MS));

beforeEach(() => {
  vi.useFakeTimers();
  vi.stubGlobal("EventSource", FakeEventSource);
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

test("the stored history is shown in one batch and causes no refetch", () => {
  show(50);
  for (let id = 1; id <= 50; id++) FakeEventSource.last.send({ id, kind: id % 5 ? "log" : "status" });
  expect(screen.getByTestId("count")).toHaveTextContent("0"); // not one render per event
  tick();
  expect(screen.getByTestId("count")).toHaveTextContent("50");
  expect(invalidated).toEqual([]); // the loaded run already includes every one of them
});

test("a new event refreshes the run once, however many arrive together", () => {
  show(10);
  FakeEventSource.last.send({ id: 11, kind: "status" });
  FakeEventSource.last.send({ id: 12, kind: "artifact" });
  FakeEventSource.last.send({ id: 13, kind: "status" });
  tick();
  expect(invalidated).toEqual([["run", "r1"], ["artifacts", "r1"], ["questions", "r1"], ["runs"]]);
});

test("log lines alone never refresh the run", () => {
  show(10);
  for (let id = 11; id <= 30; id++) FakeEventSource.last.send({ id, kind: "log" });
  tick();
  expect(screen.getByTestId("count")).toHaveTextContent("20");
  expect(invalidated).toEqual([]);
});

test("an event already seen (a reconnect sends it again) is shown once", () => {
  show(0);
  FakeEventSource.last.send({ id: 1 });
  FakeEventSource.last.send({ id: 1 });
  tick();
  expect(screen.getByTestId("count")).toHaveTextContent("1");
});

test("no refresh is asked while the run is being fetched", () => {
  show(10);
  client.getQueryCache().find({ queryKey: ["run", "r1"] })!.setState({ fetchStatus: "fetching" });
  FakeEventSource.last.send({ id: 11, kind: "status" });
  tick();
  expect(invalidated).toEqual([]);
  client.getQueryCache().find({ queryKey: ["run", "r1"] })!.setState({ fetchStatus: "idle" });
  tick();
  expect(invalidated).toContainEqual(["run", "r1"]); // asked once it is done
});

test("the stream is closed when the page goes", () => {
  const { unmount } = show(0);
  const source = FakeEventSource.last;
  unmount();
  expect(source.closed).toBe(true);
});
