import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { activeToasts, dismiss, hold, LINGER, notify, release, resetToasts } from "./toasts";

beforeEach(() => vi.useFakeTimers());
afterEach(() => {
  vi.useRealTimers();
  resetToasts();
});

describe("toasts", () => {
  // Driven off LINGER rather than literals: the durations are a tuning knob, and a spec
  // that pins them fails on a change that is not a regression. What is asserted is the
  // contract — a notice goes on its own, and a failure outlasts a success.
  it("takes a success back on its own, without anyone pressing anything", () => {
    notify({ kind: "success", message: "grilling is switched off." });
    expect(activeToasts.value).toHaveLength(1);

    vi.advanceTimersByTime(LINGER.success - 1);
    expect(activeToasts.value).toHaveLength(1);
    vi.advanceTimersByTime(1);
    expect(activeToasts.value).toEqual([]);
  });

  it("keeps a failure up longer, because it is unexpected and carries the reason", () => {
    expect(LINGER.error).toBeGreaterThan(LINGER.success);
    notify({ kind: "error", message: "Could not switch grilling.", detail: "refused" });

    vi.advanceTimersByTime(LINGER.success);
    expect(activeToasts.value).toHaveLength(1);
    vi.advanceTimersByTime(LINGER.error - LINGER.success);
    expect(activeToasts.value).toEqual([]);
  });

  it("can be dismissed before its time is up", () => {
    const id = notify({ kind: "error", message: "Could not switch grilling." });

    dismiss(id);

    expect(activeToasts.value).toEqual([]);
    // The timer went with it: firing later against a dismissed id must not take out
    // whatever notice happens to be on screen by then.
    notify({ kind: "success", message: "second" });
    vi.advanceTimersByTime(LINGER.error);
    expect(activeToasts.value.map((toast) => toast.message)).toEqual([]);
  });

  it("stops the clock while the pointer is on it, and restarts it after", () => {
    // A stderr dump that vanishes mid-sentence takes the only copy of the reason with it.
    const id = notify({ kind: "error", message: "Could not switch grilling." });

    vi.advanceTimersByTime(LINGER.error - 1_000);
    hold(id);
    vi.advanceTimersByTime(60_000);
    expect(activeToasts.value).toHaveLength(1);

    // Released, the clock starts over rather than resuming where it stopped.
    release(id);
    vi.advanceTimersByTime(LINGER.error - 1);
    expect(activeToasts.value).toHaveLength(1);
    vi.advanceTimersByTime(1);
    expect(activeToasts.value).toEqual([]);
  });

  it("stacks oldest first, and each keeps its own clock", () => {
    const gap = Math.round(LINGER.success / 2);
    notify({ kind: "success", message: "first" });
    vi.advanceTimersByTime(gap);
    notify({ kind: "success", message: "second" });

    expect(activeToasts.value.map((toast) => toast.message)).toEqual(["first", "second"]);

    vi.advanceTimersByTime(LINGER.success - gap);
    expect(activeToasts.value.map((toast) => toast.message)).toEqual(["second"]);
    vi.advanceTimersByTime(gap);
    expect(activeToasts.value).toEqual([]);
  });
});
