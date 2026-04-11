interface ErrorToastProps {
  message: string | null;
  /** If true, renders as an absolutely-positioned floating overlay at bottom-center */
  float?: boolean;
}

export default function ErrorToast({ message, float }: ErrorToastProps) {
  if (!message) return null;
  if (float) {
    return (
      <div className="absolute bottom-4 left-1/2 -translate-x-1/2 z-30 rounded-md bg-red-900/80 border border-red-700 px-4 py-2 text-sm text-red-200 shadow-lg">
        {message}
      </div>
    );
  }
  return (
    <div className="rounded-md bg-red-900/50 border border-red-700 px-3 py-2 text-sm text-red-300">
      {message}
    </div>
  );
}
