export function resolveApiBase(
  env: Record<string, string | undefined> = process.env,
): string {
  const configured = env.NEXT_PUBLIC_API_URL?.trim();
  if (configured) return configured.replace(/\/+$/, "");
  if (env.NODE_ENV === "development" || env.NODE_ENV === "test") {
    return "http://127.0.0.1:8000";
  }
  throw new Error("NEXT_PUBLIC_API_URL is required outside development and test");
}

export const API_BASE = resolveApiBase();
