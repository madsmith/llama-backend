interface ToggleSelectorProps {
  label: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
  stopPropagation?: boolean;
  disabled?: boolean;
  className?: string;
}

export default function ToggleSelector({
  label,
  checked,
  onChange,
  stopPropagation,
  disabled,
  className,
}: ToggleSelectorProps) {
  return (
    <label
      className={`flex items-center gap-2 cursor-pointer${className ? ` ${className}` : ""}`}
      onClick={
        stopPropagation
          ? (e) => {
            e.stopPropagation();
          }
          : undefined
      }
    >
      <span className="text-xs text-gray-500">{label}</span>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        aria-disabled={disabled ? true : undefined}
        disabled={disabled}
        onClick={() => {
          if (disabled) return;
          onChange(!checked);
        }}
        className={`relative inline-flex h-5 w-9 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out focus:outline-none ${checked ? "bg-green-600" : "bg-gray-600"}`}
      >
        <span
          className={`pointer-events-none inline-block h-4 w-4 rounded-full bg-white shadow-sm transition-transform duration-200 ease-in-out ${checked ? "translate-x-4" : "translate-x-0"}`}
        />
      </button>
    </label>
  );
}
