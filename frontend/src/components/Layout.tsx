import { useEffect, useState } from "react";
import { Link, NavLink, Outlet, useLocation } from "react-router-dom";

const links = [
  { to: "/", label: "Dashboard", match: (p: string) => p === "/" },
  { to: "/logs", label: "Logs", match: (p: string) => p.startsWith("/logs") },
  {
    to: "/settings",
    label: "Settings",
    match: (p: string) => p === "/settings",
  },
];

function NavContent({ onNavigate }: { onNavigate?: () => void }) {
  const { pathname } = useLocation();

  return (
    <>
      <Link to="/" onClick={onNavigate} className="px-4 py-5 flex items-center gap-2.5">
        <img src="/llama-icon.png" alt="llama.cpp" className="h-8 w-8" />
        <span className="text-lg font-bold tracking-tight">
          Llama Manager
        </span>
      </Link>
      <ul className="flex-1 space-y-1 px-2">
        {links.map((l) => {
          const active = l.match(pathname);
          return (
            <li key={l.to}>
              <NavLink
                to={l.to}
                onClick={onNavigate}
                className={`block rounded-md px-3 py-2 text-sm font-medium transition ${
                  active
                    ? "bg-gray-800 text-white"
                    : "text-gray-400 hover:bg-gray-800 hover:text-white"
                }`}
              >
                {l.label}
              </NavLink>
            </li>
          );
        })}
      </ul>
    </>
  );
}

export default function Layout() {
  const [menuOpen, setMenuOpen] = useState(false);

  useEffect(() => {
    if (!menuOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setMenuOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [menuOpen]);

  return (
    <div className="flex h-screen bg-gray-950 text-gray-100">
      {/* full sidebar (wide screens) */}
      <nav className="hidden min-[1000px]:flex w-52 shrink-0 border-r border-gray-800 bg-gray-900 flex-col">
        <NavContent />
      </nav>

      {/* collapsed rail (narrow screens) */}
      <div className="min-[1000px]:hidden w-14 shrink-0 border-r border-gray-800 bg-gray-900 flex flex-col items-center py-4">
        <button
          onClick={() => setMenuOpen(true)}
          aria-label="Open menu"
          aria-expanded={menuOpen}
          className="flex flex-col items-center gap-1.5 rounded-md p-1.5 text-gray-400 hover:bg-gray-800 hover:text-white transition"
        >
          <img src="/llama-icon.png" alt="llama.cpp" className="h-8 w-8" />
          <svg
            xmlns="http://www.w3.org/2000/svg"
            viewBox="0 0 14 14"
            className="w-4 h-4"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.75"
            strokeLinecap="round"
          >
            <line x1="2" y1="3.5" x2="12" y2="3.5" />
            <line x1="2" y1="7" x2="12" y2="7" />
            <line x1="2" y1="10.5" x2="12" y2="10.5" />
          </svg>
        </button>
      </div>

      {/* flyout (narrow screens) */}
      <div className="min-[1000px]:hidden" inert={!menuOpen}>
        <div
          onClick={() => setMenuOpen(false)}
          className={`fixed inset-0 z-40 bg-black/50 transition-opacity duration-200 ${
            menuOpen ? "opacity-100" : "opacity-0 pointer-events-none"
          }`}
        />
        <nav
          className={`fixed inset-y-0 left-0 z-50 w-52 border-r border-gray-800 bg-gray-900 flex flex-col shadow-xl transition-transform duration-200 ease-out ${
            menuOpen ? "translate-x-0" : "-translate-x-full"
          }`}
        >
          <NavContent onNavigate={() => setMenuOpen(false)} />
        </nav>
      </div>

      <main className="flex-1 overflow-auto p-6">
        <Outlet />
      </main>
    </div>
  );
}
