import assert from "node:assert/strict";

// Diese Routen fehlen in DOCK_ROUTES in api/edition.py.
export const BLOCKED_DECK_ROUTES = [
  "/api/v1/apps",
  "/api/v1/overview",
  "/api/v1/host-groups",
  "/api/v1/console/hosts",
  "/api/v1/files/sources",
  "/api/v1/terminal/hosts",
  "/api/v1/system/updates/helper",
  "/api/v1/hosts/metrics/latest",
  "/api/v1/dashboard/layouts",
  "/api/v1/widgets",
  "/api/v1/demo",
  "/api/v1/jobs",
  "/api/v1/system/openapi.json",
];

export const DOCKER_ROUTES = [
  "/api/v1/health",
  "/api/v1/edition",
  "/api/v1/me",
  "/api/v1/hosts",
  "/api/v1/settings",
  "/api/v1/pages",
  "/api/v1/notifications",
  "/api/v1/app/changelog",
  "/api/v1/system/updates",
  "/api/v1/extensions/docker/frontend/index.js",
  "/api/v1/ext/docker/widgets/matrix",
];

export async function assertExcludedModuleApis(fetchApi) {
  for (const path of [
    "/api/v1/ext/proxmox/widgets/overview",
    "/api/v1/ext/documents/documents",
  ]) {
    const res = await fetchApi(path);
    assert.equal(res.status, 404, path);
  }
  const capabilities = await fetchApi("/api/v1/capabilities");
  assert.equal(capabilities.status, 200, "/api/v1/capabilities");
  assert.equal(
    (await capabilities.json()).extensions.some(entry => entry.id === "terminal"),
    false,
    "terminal darf keine Capabilities bereitstellen",
  );
}

export async function assertBlockedDeckRoutes(fetchApi) {
  for (const path of BLOCKED_DECK_ROUTES) {
    const res = await fetchApi(path);
    assert.equal(res.status, 404, path);
  }
}

export async function assertDockerRoutes(fetchApi) {
  for (const path of DOCKER_ROUTES) {
    const res = await fetchApi(path);
    assert.equal(res.status, 200, path);
  }
}
