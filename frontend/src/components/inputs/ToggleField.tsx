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
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        onClick={() => onChange(!checked)}
        className={`relative inline-flex h-6 w-11 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 ${checked ? "bg-green-600" : "bg-gray-600"}`}
      >
        <span
          className={`pointer-events-none inline-block h-5 w-5 rounded-full bg-white shadow-sm transition-transform duration-200 ease-in-out ${checked ? "translate-x-5" : "translate-x-0"}`}
        />
      </button>
    </label>
  );
}
