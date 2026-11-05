import assert from "node:assert/strict";

export async function assertExcludedModuleApis(fetchApi) {
  for (const path of ["/api/v1/ext/proxmox/widgets/overview", "/api/v1/ext/documents/documents"]) {
    assert.equal((await fetchApi(path)).status, 404, path);
  }
  const capabilities = await fetchApi("/api/v1/capabilities");
  assert.equal(capabilities.status, 200, "/api/v1/capabilities");
  assert.equal(
    (await capabilities.json()).extensions.some(entry => entry.id === "terminal"),
    false,
    "terminal darf keine Capabilities bereitstellen",
  );
  const hosts = await fetchApi("/api/v1/terminal/hosts");
  assert.equal(hosts.status, 200, "/api/v1/terminal/hosts");
  assert.deepEqual(await hosts.json(), [], "terminal darf keine Hosts anbieten");
}
