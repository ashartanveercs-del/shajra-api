import { ApiProblem } from "./http";

const SAFE_PUBLIC_MESSAGES: Readonly<Record<string, string>> = {
  PUBLIC_WRITES_DISABLED: "Submissions are temporarily unavailable.",
  RELATIONSHIP_WRITES_DISABLED: "Relationship editing is temporarily unavailable.",
};

export type Loadable<T, TEmpty = T> =
  | { status: "loading" }
  | { status: "ready"; data: T }
  | { status: "empty"; data: TEmpty }
  | { status: "partial"; data: T; problem: ApiProblem }
  | { status: "error"; problem: ApiProblem };

export function asApiProblem(error: unknown, fallbackMessage: string): ApiProblem {
  if (error instanceof ApiProblem) {
    return new ApiProblem(
      error.status,
      error.code,
      SAFE_PUBLIC_MESSAGES[error.code] ?? fallbackMessage,
      error.requestId,
    );
  }
  return new ApiProblem(0, "CLIENT_REQUEST_FAILED", fallbackMessage);
}
