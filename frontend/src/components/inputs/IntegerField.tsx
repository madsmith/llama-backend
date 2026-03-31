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

  return (
    <div>
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
          className={`w-28 rounded-md border border-gray-700 px-3 py-2 text-sm text-gray-100 font-mono text-right focus:border-blue-500 focus:outline-none ${bg === "gray-800" ? "bg-gray-800" : "bg-gray-900"}`}
        />
      </label>
      {note && <p className="mt-1 text-xs text-gray-600">{note}</p>}
    </div>
  );
}
