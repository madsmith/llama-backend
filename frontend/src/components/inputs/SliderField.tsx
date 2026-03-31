import { Tip } from "./Tip";

interface SliderFieldProps {
  label: string;
  tip?: React.ReactNode;
  note?: React.ReactNode;
  value: number | null;
  onChange: (value: number | null) => void;
  sliderMin: number;
  sliderMax: number;
  sliderStep?: number;
  /** value = sliderPos / scale + offset  (default scale=1, offset=0) */
  scale?: number;
  offset?: number;
  /** Decimal places shown in number input; also sets its step to 10^-precision (default 0 = integer) */
  precision?: number;
  placeholder?: string;
  /** Slider position when value is null — defaults to sliderMin */
  nullSliderPosition?: number;
  /** Moving slider to sliderMin sets value to null */
  nullAtSliderMin?: boolean;
  /** Starting value for arrow key increment when field is null — defaults to numberMin */
  nullDefault?: number;
  /** Snap slider changes to nearest multiple of this value (slider units) */
  snap?: number;
  bg?: "gray-800" | "gray-900";
}

export default function SliderField({
  label,
  tip,
  note,
  value,
  onChange,
  sliderMin,
  sliderMax,
  sliderStep = 1,
  scale = 1,
  offset = 0,
  precision = 0,
  placeholder,
  nullSliderPosition,
  nullAtSliderMin = false,
  nullDefault,
  snap,
  bg = "gray-900",
}: SliderFieldProps) {
  const numberStep = precision === 0 ? 1 : Math.pow(10, -precision);
  const numberMin = sliderMin / scale + offset;
  const numberMax = sliderMax / scale + offset;

  const sliderPos =
    value === null
      ? (nullSliderPosition ?? sliderMin)
      : Math.round((value - offset) * scale);

  const handleSliderChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    let pos = Number(e.target.value);
    if (snap) pos = Math.round(pos / snap) * snap;
    if (nullAtSliderMin && pos === sliderMin) {
      onChange(null);
    } else {
      const raw = pos / scale + offset;
      onChange(parseFloat(raw.toFixed(precision)));
    }
  };

  const handleNumberChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const raw = e.target.value;
    if (raw === "") {
      onChange(null);
      return;
    }
    onChange(Number(raw));
  };

  const handleNumberKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key !== "ArrowUp" && e.key !== "ArrowDown") return;
    e.preventDefault();
    const parsedPlaceholder = placeholder !== undefined ? parseFloat(placeholder) : NaN;
    const base = value ?? nullDefault ?? (isNaN(parsedPlaceholder) ? numberMin : parsedPlaceholder);
    const next = e.key === "ArrowUp" ? base + numberStep : base - numberStep;
    const clamped = Math.min(numberMax, Math.max(numberMin, next));
    onChange(parseFloat(clamped.toFixed(precision)));
  };

  return (
    <div className="relative group">
      <label className="block text-sm font-medium text-gray-400 mb-1">
        {label}
        {tip && <Tip>{tip}</Tip>}
        {value === null && (
          <span className="ml-1 text-xs text-gray-600 font-normal">off</span>
        )}
      </label>
      <div className="relative flex items-center gap-3">
        <input
          type="range"
          min={sliderMin}
          max={sliderMax}
          step={sliderStep}
          value={sliderPos}
          onChange={handleSliderChange}
          className={`flex-1 ${value === null ? "opacity-30" : "accent-blue-500"}`}
        />
        <input
          type="number"
          step={numberStep}
          min={numberMin}
          max={numberMax}
          value={value ?? ""}
          placeholder={placeholder}
          onChange={handleNumberChange}
          onKeyDown={handleNumberKeyDown}
          className={`w-24 rounded-md border border-gray-700 pl-3 pr-1 py-2 text-sm text-gray-100 font-mono text-right focus:border-blue-500 focus:outline-none [appearance:textfield] [&::-webkit-inner-spin-button]:appearance-none [&::-webkit-outer-spin-button]:appearance-none ${bg === "gray-800" ? "bg-gray-800" : "bg-gray-900"}`}
        />
        {value !== null && <button
          type="button"
          onClick={() => onChange(null)}
          aria-label="Clear"
          className="absolute right-0 translate-x-full pl-2 pr-4 py-2 invisible group-hover:visible text-red-500 hover:text-red-300 transition-colors"
        >
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 14 14" className="w-3.5 h-3.5" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
            <line x1="2" y1="2" x2="12" y2="12" />
            <line x1="12" y1="2" x2="2" y2="12" />
          </svg>
        </button>}
      </div>
      {note && <div className="mt-1 text-xs text-gray-500">{note}</div>}
    </div>
  );
}
