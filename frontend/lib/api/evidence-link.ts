// Only result-scoped API paths may be appended to the configured API origin.
const SNAPSHOT = /^\/api\/v1\/(?:memos|historical-analyses)\/[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}\/evidence\/[a-f0-9]{64}$/;
export function evidencePath(path: string | null | undefined): string | null {
  return path && SNAPSHOT.test(path) ? path : null;
}
