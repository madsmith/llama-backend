import { NavLink, Outlet } from "react-router-dom";

const links = [
  { to: "/", label: "Dashboard" },
  { to: "/logs", label: "Logs" },
  { to: "/properties", label: "Properties" },
  { to: "/settings", label: "Settings" },
];

export default function Layout() {
  return (
    <div className="flex h-screen bg-gray-950 text-gray-100">
      <nav className="w-52 shrink-0 border-r border-gray-800 bg-gray-900 flex flex-col">
        <div className="px-4 py-5 flex items-center gap-2.5">
          <img src="/llama-icon.png" alt="llama.cpp" className="h-8 w-8" />
          <span className="text-lg font-bold tracking-tight">Llama Manager</span>
        </div>
        <ul className="flex-1 space-y-1 px-2">
          {links.map((l) => (
            <li key={l.to}>
              <NavLink
                to={l.to}
                end={l.to === "/"}
                className={({ isActive }) =>
                  `block rounded-md px-3 py-2 text-sm font-medium transition ${
                    isActive
                      ? "bg-gray-800 text-white"
                      : "text-gray-400 hover:bg-gray-800 hover:text-white"
                  }`
                }
              >
                {l.label}
              </NavLink>
            </li>
          ))}
        </ul>
      </nav>
      <main className="flex-1 overflow-auto p-6">
        <Outlet />
      </main>
    </div>
  );
}
