// Host capability decisions are server-owned. Missing/malformed values deny UI controls.
export function capabilityAvailable(payload, id) {
  const matches = Array.isArray(payload?.capabilities)
    ? payload.capabilities.filter(item => item?.id === id) : [];
  return matches.length === 1 && matches[0].state === "available";
}

export function routeCapability(path) {
  if (path === "/research" || path.startsWith("/research/")) return "research";
  if (path === "/data" || path.startsWith("/data/")) return "data";
  if (path === "/system") return "test_execution";
  if (path === "/system/benchmarks") return "benchmarks";
  if (path === "/system/connections") return "connection_inspection";
  return null;
}
