import { beforeEach, describe, expect, it } from "vitest";

import { useAuthStore } from "./auth";
import { useDialogsStore } from "./dialogs";

const USER = { id: "u1", username: "nico", display_name: null, email: null, is_owner: false, locale: "de", permissions: ["*"] };

beforeEach(() => {
  useAuthStore.setState({ accessToken: "tok", user: USER, status: "authenticated", mfaToken: null });
  useDialogsStore.setState({ confirmPending: null, promptPending: null, identityPending: null });
});

describe("offene Dialoge bei Sitzungsende", () => {
  it("eine abgelaufene Anmeldung bricht offene Dialoge ab, ein neues Konto sieht sie nicht", async () => {
    const confirm = useDialogsStore.getState().requestConfirm("Ordner löschen?", { danger: true });
    const prompt = useDialogsStore.getState().requestPrompt("Neuer Name", "a");

    useAuthStore.setState({ accessToken: null, user: null, status: "anonymous" });

    await expect(confirm).resolves.toBe(false);
    await expect(prompt).resolves.toBeNull();
    useAuthStore.setState({ accessToken: "tok-2", user: { ...USER, id: "u2" }, status: "authenticated" });
    expect(useDialogsStore.getState().confirmPending).toBeNull();
    expect(useDialogsStore.getState().promptPending).toBeNull();
  });

  it("ein Kontowechsel ohne Zwischenschritt bricht ebenfalls ab", async () => {
    const confirm = useDialogsStore.getState().requestConfirm("Löschen?", {});
    useAuthStore.setState({ accessToken: "tok-2", user: { ...USER, id: "u2" } });
    await expect(confirm).resolves.toBe(false);
  });

  it("ein erneuertes Token desselben Kontos lässt den Dialog offen", () => {
    void useDialogsStore.getState().requestConfirm("Löschen?", {});
    useAuthStore.setState({ accessToken: "tok-neu", user: { ...USER } });
    expect(useDialogsStore.getState().confirmPending).not.toBeNull();
  });
});
