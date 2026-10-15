import { QueryClient } from "@tanstack/react-query";
import { afterEach, describe, expect, it } from "vitest";

import { type CurrentUser, useAuthStore } from "../state/auth";
import { clearQueriesOnAccountChange } from "./accountCache";

const user = (id: string, username: string): CurrentUser => ({
  id, username, display_name: username, email: null, is_owner: false, locale: "de", permissions: ["hosts.read"],
});

describe("clearQueriesOnAccountChange", () => {
  let stop: () => void = () => {};
  afterEach(() => {
    stop();
    useAuthStore.setState({ accessToken: null, user: null, status: "anonymous", mfaToken: null });
  });

  function start(first: CurrentUser | null) {
    useAuthStore.setState({ accessToken: first ? "tok" : null, user: first, status: first ? "authenticated" : "anonymous" });
    const client = new QueryClient();
    client.setQueryData(["me", "preferences"], { first_steps_dismissed: false, theme: "classic" });
    stop = clearQueriesOnAccountChange(client);
    return client;
  }

  it("Abmelden leert den Zwischenspeicher", () => {
    const client = start(user("u1", "anna"));
    useAuthStore.setState({ accessToken: null, user: null, status: "anonymous" });
    expect(client.getQueryData(["me", "preferences"])).toBeUndefined();
  });

  it("ein anderes Konto im selben Tab sieht nichts vom vorigen", () => {
    const client = start(user("u1", "anna"));
    useAuthStore.setState({ user: user("u2", "ben") });
    expect(client.getQueryData(["me", "preferences"])).toBeUndefined();
  });

  it("ein neues Token für dasselbe Konto lässt alles stehen", () => {
    const client = start(user("u1", "anna"));
    useAuthStore.setState({ accessToken: "neu", user: user("u1", "anna") });
    expect(client.getQueryData(["me", "preferences"])).toEqual({ first_steps_dismissed: false, theme: "classic" });
  });
});
