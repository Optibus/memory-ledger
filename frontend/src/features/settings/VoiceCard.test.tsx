import { describe, expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { VoiceCard } from "./VoiceCard";
import { renderApp } from "@/test/utils";
import { writes } from "@/test/server";

describe("VoiceCard", () => {
  it("shows what can listen and speak here, and saves the settings it was given", async () => {
    const { user } = renderApp(<VoiceCard writable />, { route: "/settings" });
    expect(await screen.findByText("faster-whisper · medium")).toBeInTheDocument();
    expect(screen.getByText("say")).toBeInTheDocument();
    expect(screen.queryByLabelText("Translate into")).not.toBeInTheDocument();
    const silence = screen.getByLabelText("Silence before sending");
    await user.clear(silence);
    await user.type(silence, "8");
    await user.click(screen.getByRole("checkbox", { name: /Translate every block/ }));
    expect(screen.getByLabelText("Translate into")).toBeInTheDocument();
    await user.click(screen.getByRole("checkbox", { name: /Read the agent's messages aloud/ }));
    await user.click(screen.getByRole("button", { name: "Save voice" }));
    await waitFor(() =>
      expect(writes.find((w) => w.path === "/api/voice-config")?.body).toMatchObject({
        silence_s: 8,
        translate: true,
        tts_enabled: true,
        stt_language: "pt",
      }),
    );
  });

  it("offers only the engine's voices for each language", async () => {
    renderApp(<VoiceCard writable />, { route: "/settings" });
    const pt = await screen.findByLabelText("Voice: Português");
    expect(Array.from((pt as HTMLSelectElement).options).map((o) => o.value)).toEqual(["", "Luciana"]);
  });

  it("is read-only on a read-only server", async () => {
    renderApp(<VoiceCard writable={false} />, { route: "/settings" });
    expect(await screen.findByRole("button", { name: "Save voice" })).toBeDisabled();
  });
});
