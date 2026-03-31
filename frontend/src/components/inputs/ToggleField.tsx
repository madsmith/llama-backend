import { Tip } from "./Tip";

interface ToggleFieldProps {
  label: string;
  tip?: React.ReactNode;
  description?: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
  bg?: "gray-800" | "gray-900";
}

export default function ToggleField({
  label,
  tip,
  description,
  checked,
  onChange,
  bg = "gray-900",
}: ToggleFieldProps) {
  return (
    <label className="flex items-center justify-between cursor-pointer">
      <span className="text-sm font-medium text-gray-400">
        {label}
        {tip && <Tip>{tip}</Tip>}
        {description && (
          <p className="text-xs text-gray-600">{description}</p>
        )}
      </span>
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        className={`h-4 w-4 rounded border-gray-700 accent-blue-500 ${bg === "gray-800" ? "bg-gray-800" : "bg-gray-900"}`}
      />
    </label>
  );
}
