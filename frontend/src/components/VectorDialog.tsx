import { useEffect, useState } from "react";

function stats(v: number[]) {
  let sum = 0, sq = 0, min = Infinity, max = -Infinity;
  for (const x of v) {
    sum += x;
    sq += x * x;
    if (x < min) min = x;
    if (x > max) max = x;
  }
  return { norm: Math.sqrt(sq), mean: v.length ? sum / v.length : 0, min, max };
}

/** Full view of a numeric vector: summary stats and every value by index. */
function VectorDialog({ title, vector, onClose }: {
  title: string;
  vector: number[];
  onClose: () => void;
}) {
  const [copied, setCopied] = useState(false);
  const s = stats(vector);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(JSON.stringify(vector));
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard needs a secure context; nothing useful to do here.
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4" onClick={onClose}>
      <div
        className="flex flex-col w-full max-w-3xl max-h-[80vh] bg-gray-950 border border-gray-700 rounded-lg shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center gap-3 px-4 py-2.5 border-b border-gray-800 shrink-0 text-xs">
          <span className="text-gray-200 font-medium">{title}</span>
          <span className="text-gray-500">{vector.length} values</span>
          <div className="flex-1" />
          <button onClick={copy} className="text-gray-500 hover:text-gray-200 transition">
            {copied ? "Copied" : "Copy JSON"}
          </button>
          <button onClick={onClose} className="text-gray-500 hover:text-gray-200 transition">
            Close
          </button>
        </div>
        <div className="flex flex-wrap gap-x-6 gap-y-1 px-4 py-2 border-b border-gray-800 shrink-0 text-xs font-mono text-gray-400">
          <span>norm {s.norm.toFixed(4)}</span>
          <span>mean {s.mean.toFixed(5)}</span>
          <span>min {s.min.toFixed(4)}</span>
          <span>max {s.max.toFixed(4)}</span>
        </div>
        <div className="overflow-auto p-4">
          <div className="grid grid-cols-[repeat(auto-fill,minmax(8.5rem,1fr))] gap-x-4 font-mono text-xs leading-5">
            {vector.map((x, i) => (
              <div key={i} className="flex gap-2">
                <span className="text-gray-600 w-10 text-right shrink-0">{i}</span>
                <span className={x < 0 ? "text-rose-300" : "text-blue-300"}>{x.toFixed(6)}</span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

/** A compact "[1024 floats]" link that opens the vector in a dialog. */
export default function VectorLink({ vector, title = "Vector" }: { vector: number[]; title?: string }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button
        onClick={(e) => { e.stopPropagation(); setOpen(true); }}
        className="text-blue-400 hover:text-blue-300 hover:underline"
        title="Show all values"
      >
        [{vector.length} floats]
      </button>
      {open && <VectorDialog title={title} vector={vector} onClose={() => setOpen(false)} />}
    </>
  );
}
