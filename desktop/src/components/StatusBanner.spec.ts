// @vitest-environment jsdom
import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";
import StatusBanner from "./StatusBanner.vue";

/**
 * The one surface every command's outcome passes through, so the properties asserted here
 * are the ones the rest of the app is entitled to assume.
 */
describe("StatusBanner", () => {
  it("announces a failure assertively and a success politely", () => {
    // Two different `role`s for a reason: an error interrupts, a success does not. A
    // screen reader user who has just been told the install failed should not have to go
    // looking for the sentence that said so.
    const error = mount(StatusBanner, { props: { kind: "error" }, slots: { default: "Nope" } });
    const success = mount(StatusBanner, { props: { kind: "success" }, slots: { default: "Done" } });

    expect(error.attributes("role")).toBe("alert");
    expect(success.attributes("role")).toBe("status");
  });

  it("renders the detail in a pre, because stderr's line breaks carry meaning", () => {
    const detail = "library exited 1.\nTraceback:\n  line one\n  line two";

    const banner = mount(StatusBanner, { props: { kind: "error", detail } });

    // Verbatim (R1.4), inside a `<pre>`. Rendered into a `<p>` the traceback would come
    // out as one run-on line, which is the shape stderr is least readable in.
    expect(banner.find("pre").text()).toBe(detail);
  });

  it("omits the detail element entirely when there is no detail", () => {
    const banner = mount(StatusBanner, { props: { kind: "success" }, slots: { default: "Done" } });

    // Not an empty `<pre>`: it carries its own top margin, so an empty one is visible as
    // a gap under every success message in the app.
    expect(banner.find("pre").exists()).toBe(false);
  });

  it("shows the slot and the detail together", () => {
    const banner = mount(StatusBanner, {
      props: { kind: "error", detail: "exit 2" },
      slots: { default: "Could not add the entry." },
    });

    expect(banner.text()).toContain("Could not add the entry.");
    expect(banner.text()).toContain("exit 2");
  });
});
