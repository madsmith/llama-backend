import { Tip } from "./Tip";

interface IntegerFieldProps {
  label: string;
  tip?: React.ReactNode;
  unit?: string;
  /** Muted text rendered below the label, inside the label element */
  description?: string;
  /** Small text rendered below the input row */
  note?: React.ReactNode;
  value: number | null;
  onChange: (value: number | null) => void;
  placeholder?: string;
  nullable?: boolean;
  min?: number;
  max?: number;
  bg?: "gray-800" | "gray-900";
}

export default function IntegerField({
  label,
  tip,
  unit,
  description,
  note,
  value,
  onChange,
  placeholder,
  nullable = false,
  min,
  max,
  bg = "gray-900",
}: IntegerFieldProps) {
  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const raw = e.target.value;
    if (raw === "") {
      if (nullable) onChange(null);
      return;
    }
    const n = parseInt(raw, 10);
    if (!isNaN(n)) onChange(n);
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key !== "ArrowUp" && e.key !== "ArrowDown") return;
    e.preventDefault();
    const parsedPlaceholder = placeholder !== undefined ? parseInt(placeholder, 10) : NaN;
    const base = value ?? (isNaN(parsedPlaceholder) ? (min ?? 0) : parsedPlaceholder);
    const next = e.key === "ArrowUp" ? base + 1 : base - 1;
    const clamped = Math.min(max ?? Infinity, Math.max(min ?? -Infinity, next));
    onChange(clamped);
  };

  return (
    <div className="relative group">
      <label className="flex items-center justify-between">
        <span className="text-sm font-medium text-gray-400">
          {label}
          {tip && <Tip>{tip}</Tip>}
          {unit && <span className="ml-1 text-xs text-gray-600">({unit})</span>}
          {description && <p className="text-xs text-gray-600 font-normal">{description}</p>}
        </span>
        <input
          type="number"
          step="1"
          min={min}
          max={max}
          value={value ?? ""}
          placeholder={placeholder}
          onChange={handleChange}
          onKeyDown={handleKeyDown}
          className={`w-28 rounded-md border border-gray-700 px-3 py-2 text-sm text-gray-100 font-mono text-right focus:border-blue-500 focus:outline-none [appearance:textfield] [&::-webkit-inner-spin-button]:appearance-none [&::-webkit-outer-spin-button]:appearance-none ${bg === "gray-800" ? "bg-gray-800" : "bg-gray-900"}`}
        />
      </label>
      {nullable && value !== null && (
        <button
          type="button"
          onClick={() => onChange(null)}
          aria-label="Clear"
          className="absolute right-0 top-1/2 -translate-y-1/2 translate-x-full pl-2 pr-4 py-2 invisible group-hover:visible text-red-500 hover:text-red-300 transition-colors"
        >
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 14 14" className="w-3.5 h-3.5" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
            <line x1="2" y1="2" x2="12" y2="12" />
            <line x1="12" y1="2" x2="2" y2="12" />
          </svg>
        </button>
      )}
      {note && <p className="mt-1 text-xs text-gray-600">{note}</p>}
    </div>
  );
}
