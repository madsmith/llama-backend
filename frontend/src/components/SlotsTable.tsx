import type { SlotInfo } from "../api/types";

interface Props {
  slots: SlotInfo[];
}

export default function SlotsTable({ slots }: Props) {
  return (
    <div className="rounded-xl border border-gray-800 bg-gray-900 p-5">
      <h3 className="mb-3 text-sm font-semibold text-gray-400 uppercase tracking-wider">
        Slots
      </h3>
      {slots.length === 0 ? (
        <p className="text-sm text-gray-500">No slot data available.</p>
      ) : (
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-gray-800 text-left text-gray-500">
              <th className="pb-2 pr-4">ID</th>
              <th className="pb-2 pr-4">State</th>
              <th className="pb-2 pr-4">Context</th>
              <th className="pb-2">Processing</th>
            </tr>
          </thead>
          <tbody>
            {slots.map((s) => (
              <tr key={s.id} className="border-b border-gray-800/50">
                <td className="py-2 pr-4 font-mono">{s.id}</td>
                <td className="py-2 pr-4">
                  <span
                    className={`inline-block h-2 w-2 rounded-full mr-2 ${s.is_processing ? "bg-yellow-500" : "bg-green-500"}`}
                  />
                  {s.is_processing ? "busy" : "idle"}
                </td>
                <td className="py-2 pr-4 font-mono">{s.n_ctx}</td>
                <td className="py-2">
                  {s.is_processing ? "yes" : "no"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
