interface SegmentedControlProps<T extends string> {
  options: readonly T[];
  value: T;
  onChange: (value: T) => void;
  labels?: Partial<Record<T, string>>;
}

export default function SegmentedControl<T extends string>({
  options,
  value,
  onChange,
  labels,
}: SegmentedControlProps<T>) {
  return (
    <div className="flex items-center rounded-full bg-gray-800 p-0.5 gap-0.5">
      {options.map((opt) => (
        <button
          key={opt}
          onClick={() => onChange(opt)}
          className={`px-2.5 py-0.5 rounded-full text-xs font-medium transition-colors ${value === opt ? "bg-gray-600 text-gray-100" : "text-gray-500 hover:text-gray-300"}`}
        >
          {labels?.[opt] ?? opt}
        </button>
      ))}
    </div>
  );
}
