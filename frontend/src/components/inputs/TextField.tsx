import { Tip } from "./Tip";

interface TextFieldProps {
  label: string;
  tip?: React.ReactNode;
  /** Muted text rendered inline after the label */
  sublabel?: string;
  /** Small text rendered below the input */
  note?: React.ReactNode;
  value: string;
  onChange: (value: string) => void;
  onBlur?: (value: string) => void;
  placeholder?: string;
  mono?: boolean;
  /** Elements (e.g. buttons) rendered next to the input in a flex row */
  actions?: React.ReactNode;
  bg?: "gray-800" | "gray-900";
}

export default function TextField({
  label,
  tip,
  sublabel,
  note,
  value,
  onChange,
  onBlur,
  placeholder,
  mono,
  actions,
  bg = "gray-900",
}: TextFieldProps) {
  return (
    <div>
      <label className="block text-sm font-medium text-gray-400 mb-1">
        {label}
        {tip && <Tip>{tip}</Tip>}
        {sublabel && (
          <span className="ml-1 text-xs text-gray-600 font-normal">{sublabel}</span>
        )}
      </label>
      <div className={actions ? "flex gap-2" : undefined}>
        <input
          type="text"
          value={value}
          placeholder={placeholder}
          onChange={(e) => onChange(e.target.value)}
          onBlur={onBlur ? (e) => onBlur(e.target.value) : undefined}
          className={[
            "rounded-md border border-gray-700 px-3 py-2 text-sm text-gray-100 focus:border-blue-500 focus:outline-none",
            actions ? "flex-1" : "w-full",
            mono ? "font-mono" : "",
            bg === "gray-800" ? "bg-gray-800" : "bg-gray-900",
          ].filter(Boolean).join(" ")}
        />
        {actions}
      </div>
      {note && <p className="mt-1 text-xs text-gray-600">{note}</p>}
    </div>
  );
}
