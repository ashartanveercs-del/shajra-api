import { AlertTriangle, Inbox, Loader2, RefreshCw } from "lucide-react";

export type AsyncStateProps = {
  state: "loading" | "empty" | "error" | "partial";
  title: string;
  message?: string;
  actionLabel?: string;
  onAction?: () => void;
};

const ICONS = {
  loading: Loader2,
  empty: Inbox,
  error: AlertTriangle,
  partial: AlertTriangle,
};

export default function AsyncState({
  state,
  title,
  message,
  actionLabel,
  onAction,
}: AsyncStateProps) {
  const Icon = ICONS[state];
  const isLoading = state === "loading";
  const isError = state === "error";

  return (
    <div
      role={isError ? "alert" : isLoading ? "status" : undefined}
      aria-live={isError ? "assertive" : isLoading ? "polite" : undefined}
      className="flex h-full min-h-[11rem] w-full flex-col items-center justify-center px-5 py-8 text-center"
    >
      <div className="mb-4 flex h-12 w-12 items-center justify-center rounded-lg bg-bg-secondary text-text-light">
        <Icon
          aria-hidden="true"
          className={`h-5 w-5 ${isLoading ? "animate-spin text-accent" : ""}`}
        />
      </div>
      <h2 className="font-serif text-xl font-semibold text-text-primary">{title}</h2>
      {message && (
        <p className="mt-2 max-w-md text-sm leading-relaxed text-text-muted">{message}</p>
      )}
      {!isLoading && actionLabel && onAction && (
        <button
          type="button"
          onClick={onAction}
          className="btn-secondary mt-5 min-h-10 px-4"
        >
          <RefreshCw aria-hidden="true" className="h-4 w-4" />
          {actionLabel}
        </button>
      )}
    </div>
  );
}
