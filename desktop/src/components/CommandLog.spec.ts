// @vitest-environment jsdom
import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, describe, expect, it } from "vitest";
import { emitEvent, resetTauri } from "../testing/tauri";
import CommandLog from "./CommandLog.vue";

afterEach(resetTauri);

/**
 * Mount the log, expanded, with its subscription established.
 *
 * `listen` is an IPC round trip even here, so an event emitted in the same tick as the
 * mount lands before anyone is listening — the same race `useCommandActivity` exposes
 * `listening` to close.
 */
async function mountLog() {
  const log = mount(CommandLog);
  await flushPromises();
  await log.find(".command-log__toggle").trigger("click");
  return log;
}

/**
 * The command stream is module state that outlives every component, so these cases share
 * one growing history rather than each starting from empty. Asserted against the newest
 * rows for that reason, and the empty case has to be the first one in the file.
 */
describe("CommandLog", () => {
  it("says nothing has run rather than showing an empty list", async () => {
    const log = await mountLog();

    // The log is the only safeguard the app has in place of an approval gate (D5), so an
    // empty panel has to read as "nothing has happened" and not as "the log is broken".
    expect(log.find(".command-log__empty").text()).toBe("Nothing has run yet.");
    expect(log.find(".command-log__count").text()).toBe("0");
  });

  it("shows a running command verbatim, with no exit code yet", async () => {
    const log = await mountLog();

    emitEvent("command://started", { id: 1, argv: ["library", "use", "grilling", "--json"] });
    await flushPromises();

    // Verbatim argv, `--json` included: naming the command exactly is the transparency
    // the app trades for not asking permission before it runs one.
    expect(log.find(".command-log__argv").text()).toBe("library use grilling --json");
    expect(log.find(".command-log__status").text()).toBe("…");
    expect(log.find(".command-log__empty").exists()).toBe(false);
  });

  it("marks a finished command with its exit code and duration", async () => {
    const log = await mountLog();

    emitEvent("command://started", { id: 7, argv: ["library", "list", "--json"] });
    emitEvent("command://finished", { id: 7, code: 0, duration_ms: 214 });
    await flushPromises();

    // Scoped to the newest row: the still-running command from the case above is a real
    // row further down the shared history, and it is supposed to still be marked running.
    const newest = log.findAll(".command-log__row")[0];
    expect(newest.find(".command-log__status").text()).toBe("0");
    expect(newest.find(".command-log__time").text()).toContain("214ms");
    expect(newest.find(".command-log__status--running").exists()).toBe(false);
  });

  it("marks a non-zero exit as failed, which zero is not", async () => {
    const log = await mountLog();

    emitEvent("command://started", { id: 3, argv: ["library", "add", "x"] });
    emitEvent("command://finished", { id: 3, code: 2, duration_ms: 30 });
    await flushPromises();

    expect(log.find(".command-log__status--failed").exists()).toBe(true);
    expect(log.find(".command-log__status").text()).toBe("2");
  });

  it("ignores a finish for a command it never saw start", async () => {
    const log = await mountLog();
    const before = log.find(".command-log__count").text();

    // Reachable: the log subscribes on first mount, and a command fired before that lands
    // with no row to update. Dropping it beats inventing a row with no argv to show.
    emitEvent("command://finished", { id: 9999, code: 0, duration_ms: 5 });
    await flushPromises();

    expect(log.find(".command-log__count").text()).toBe(before);
  });

  it("lists the newest command first", async () => {
    const log = await mountLog();

    emitEvent("command://started", { id: 101, argv: ["library", "list"] });
    emitEvent("command://started", { id: 102, argv: ["library", "doctor"] });
    await flushPromises();

    expect(log.findAll(".command-log__argv").slice(0, 2).map((row) => row.text())).toEqual([
      "library doctor",
      "library list",
    ]);
  });

  /**
   * The agent spawn, which is the one command in the app that does not fit on a line.
   *
   * `claude -p <prompt>` carries the whole walkthrough prompt as a single argv element — around
   * two thousand characters. Rendered whole it filled the window, buried every other entry, and
   * left the page showing through this panel from behind. Twice reported from using the app.
   */
  it("folds a command too long to read, and still holds all of it", async () => {
    const log = await mountLog();
    const prompt = "You are running inside The Library. ".repeat(60);

    emitEvent("command://started", { id: 201, argv: ["claude", "-p", prompt, "--verbose"] });
    await flushPromises();

    const row = log.findAll(".command-log__argv")[0];
    const more = log.findAll(".command-log__more")[0];
    // Folded by default, and the control says there is more rather than being on every row.
    expect(row.text().length).toBeLessThan(200);
    expect(row.text().endsWith("…")).toBe(true);
    expect(more.text()).toBe("full");

    await more.trigger("click");

    // D5's verbatim record, in the same row: nothing is lost, it is asked for.
    expect(log.findAll(".command-log__argv")[0].text()).toContain("--verbose");
    expect(log.findAll(".command-log__argv")[0].text()).toContain(prompt.trim());
    expect(log.findAll(".command-log__more")[0].text()).toBe("less");
  });

  it("leaves a command that already fits alone", async () => {
    const log = await mountLog();

    emitEvent("command://started", { id: 202, argv: ["library", "list", "--json"] });
    await flushPromises();

    // No control where nothing is hidden: its presence has to mean something.
    expect(log.findAll(".command-log__argv")[0].text()).toBe("library list --json");
    expect(log.findAll(".command-log__row")[0].find(".command-log__more").exists()).toBe(false);
  });
});
