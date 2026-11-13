import { authedFetch, errorFromBody } from "../../../_shared/frontend/src/api";
import { UNREADABLE_ANSWER_TEXT } from "../../../_shared/frontend/src/loadError";

export class SystemQueryError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
  }
}

export async function fetchSystem<T>(id: string, view: "live" | "info"): Promise<T> {
  const res = await authedFetch(`/ext/system/hosts/${id}/${view}`);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new SystemQueryError(errorFromBody(body, res.status), res.status);
  }
  try {
    return await res.json() as T;
  } catch {
    throw new Error(UNREADABLE_ANSWER_TEXT);
  }
}
