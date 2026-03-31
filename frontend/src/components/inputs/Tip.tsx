import { useState } from "react";

export function Tip({ children }: { children: React.ReactNode }) {
  const [visible, setVisible] = useState(false);
  const [pos, setPos] = useState({ x: 0, y: 0 });
  return (
    <span
      className="ml-1 text-gray-600 hover:text-gray-400 cursor-help"
      onMouseEnter={(e) => { setPos({ x: e.clientX, y: e.clientY }); setVisible(true); }}
      onMouseLeave={() => setVisible(false)}
      onMouseMove={(e) => setPos({ x: e.clientX, y: e.clientY })}
    >
      ⓘ
      {visible && (
        <span
          className="fixed z-50 max-w-xs rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-xs text-gray-300 shadow-lg pointer-events-none font-normal leading-relaxed"
          style={{ left: pos.x + 12, top: pos.y + 16 }}
        >
          {children}
        </span>
      )}
    </span>
  );
}
