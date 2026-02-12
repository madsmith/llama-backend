export const PANEL_BG = "bg-gray-900";

export function TabButton({ label, active, onClick }: {
  label: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className={`px-5 py-2.5 text-sm font-medium rounded-t-lg transition ${
        active
          ? `${PANEL_BG} text-gray-100`
          : "bg-[#111419] text-gray-500 hover:text-gray-300"
      }`}
    >
      {label}
    </button>
  );
}
