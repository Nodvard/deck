import { describe, expect, it } from "vitest";
import {
  assertBlockedDeckRoutes,
  assertDockerRoutes,
  assertExcludedModuleApis,
  BLOCKED_DECK_ROUTES,
  DOCKER_ROUTES,
} from "./dockModules.mjs";

describe("dockModules", () => {
  describe("assertExcludedModuleApis", () => {
    it("akzeptiert 404 für ausgeschlossene Modul-APIs und prüft Capabilities", async () => {
      const fetchApi = async (path: string) => {
        if (path === "/api/v1/capabilities") {
          return {
            status: 200,
            json: async () => ({ extensions: [{ id: "docker" }] }),
          };
        }
        if (
          path === "/api/v1/ext/proxmox/widgets/overview" ||
          path === "/api/v1/ext/documents/documents"
        ) {
          return { status: 404 };
        }
        return { status: 500 };
      };

      await expect(assertExcludedModuleApis(fetchApi)).resolves.toBeUndefined();
    });

    it("scheitert nicht mehr an /api/v1/terminal/hosts", async () => {
      const fetchApi = async (path: string) => {
        if (path === "/api/v1/capabilities") {
          return {
            status: 200,
            json: async () => ({ extensions: [] }),
          };
        }
        if (path === "/api/v1/terminal/hosts") {
          return { status: 200, json: async () => [] };
        }
        if (
          path === "/api/v1/ext/proxmox/widgets/overview" ||
          path === "/api/v1/ext/documents/documents"
        ) {
          return { status: 404 };
        }
        return { status: 500 };
      };

      await expect(assertExcludedModuleApis(fetchApi)).resolves.toBeUndefined();
    });

    it("scheitert, wenn ein ausgeschlossenes Modul verfügbar ist", async () => {
      const fetchApi = async (path: string) => {
        if (path === "/api/v1/capabilities") {
          return {
            status: 200,
            json: async () => ({ extensions: [] }),
          };
        }
        if (path === "/api/v1/ext/proxmox/widgets/overview") {
          return { status: 200 };
        }
        return { status: 404 };
      };

      await expect(assertExcludedModuleApis(fetchApi)).rejects.toThrow();
    });

    it("scheitert, wenn terminal in den Capabilities vorhanden ist", async () => {
      const fetchApi = async (path: string) => {
        if (path === "/api/v1/capabilities") {
          return {
            status: 200,
            json: async () => ({ extensions: [{ id: "terminal" }] }),
          };
        }
        return { status: 404 };
      };

      await expect(assertExcludedModuleApis(fetchApi)).rejects.toThrow();
    });
  });

  describe("assertBlockedDeckRoutes", () => {
    it.each(["/dashboard/layouts", "/widgets", "/demo", "/jobs", "/system/openapi.json"])(
      "scheitert, wenn %s verfügbar ist", async (suffix) => {
        const fetchApi = async (path: string) => ({ status: path === `/api/v1${suffix}` ? 200 : 404 });
        await expect(assertBlockedDeckRoutes(fetchApi)).rejects.toThrow();
      },
    );

    it("akzeptiert 404 für alle gesperrten Deck-Routen", async () => {
      const fetchApi = async (path: string) => {
        if (BLOCKED_DECK_ROUTES.includes(path)) {
          return { status: 404 };
        }
        return { status: 500 };
      };

      await expect(assertBlockedDeckRoutes(fetchApi)).resolves.toBeUndefined();
    });

    it("scheitert, wenn /api/v1/terminal/hosts mit 200 und leerer Liste antwortet", async () => {
      const fetchApi = async (path: string) => {
        if (path === "/api/v1/terminal/hosts") {
          return { status: 200, json: async () => [] };
        }
        if (BLOCKED_DECK_ROUTES.includes(path)) {
          return { status: 404 };
        }
        return { status: 500 };
      };

      await expect(assertBlockedDeckRoutes(fetchApi)).rejects.toThrow();
    });

    it("scheitert, wenn /api/v1/apps mit 200 antwortet", async () => {
      const fetchApi = async (path: string) => {
        if (path === "/api/v1/apps") {
          return { status: 200 };
        }
        if (BLOCKED_DECK_ROUTES.includes(path)) {
          return { status: 404 };
        }
        return { status: 500 };
      };

      await expect(assertBlockedDeckRoutes(fetchApi)).rejects.toThrow();
    });
  });

  describe("assertDockerRoutes", () => {
    it.each(["/health", "/edition", "/me", "/hosts", "/settings", "/pages", "/notifications", "/app/changelog", "/system/updates"])(
      "scheitert, wenn %s gesperrt ist", async (suffix) => {
        const fetchApi = async (path: string) => ({ status: path === `/api/v1${suffix}` ? 404 : 200 });
        await expect(assertDockerRoutes(fetchApi)).rejects.toThrow();
      },
    );

    it("akzeptiert 200 für Docker-Routen", async () => {
      const fetchApi = async (path: string) => {
        if (DOCKER_ROUTES.includes(path)) {
          return { status: 200 };
        }
        return { status: 404 };
      };

      await expect(assertDockerRoutes(fetchApi)).resolves.toBeUndefined();
    });

    it("scheitert bei 404 für eine Docker-Route", async () => {
      const fetchApi = async (path: string) => {
        if (path === DOCKER_ROUTES[0]) {
          return { status: 404 };
        }
        return { status: 200 };
      };

      await expect(assertDockerRoutes(fetchApi)).rejects.toThrow();
    });
  });
});
