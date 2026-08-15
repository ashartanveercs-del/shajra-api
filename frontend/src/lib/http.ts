import { API_BASE } from "./env";

type JsonObject = Record<string, unknown>;

function isJsonObject(value: unknown): value is JsonObject {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function problemFrom(response: Response, body: unknown): ApiProblem {
  const detail = isJsonObject(body) ? body.detail : undefined;
  const structuredDetail = isJsonObject(detail) ? detail : undefined;
  const message =
    (typeof detail === "string" && detail) ||
    (typeof structuredDetail?.message === "string" && structuredDetail.message) ||
    (isJsonObject(body) && typeof body.message === "string" && body.message) ||
    `Request failed with ${response.status}`;
  const code =
    (typeof structuredDetail?.code === "string" && structuredDetail.code) ||
    (isJsonObject(body) && typeof body.code === "string" && body.code) ||
    "REQUEST_FAILED";

  return new ApiProblem(
    response.status,
    code,
    message,
    response.headers.get("x-request-id") ?? undefined,
  );
}

export class ApiProblem extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public requestId?: string,
  ) {
    super(message);
    this.name = "ApiProblem";
  }
}

export async function requestJson<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, init);
  const text = await response.text();
  const body = text ? tryParseJson(text) : undefined;

  if (!response.ok) {
    throw problemFrom(response, body);
  }
  if (!text || response.status === 204) {
    return undefined as T;
  }
  if (body === undefined) {
    throw new ApiProblem(
      response.status,
      "INVALID_RESPONSE",
      "Expected a JSON response",
      response.headers.get("x-request-id") ?? undefined,
    );
  }
  return body as T;
}

function tryParseJson(text: string): unknown | undefined {
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return undefined;
  }
}
