import ServerStatusCard from "../components/ServerStatusCard";
import ServerControls from "../components/ServerControls";
import LogViewer from "../components/LogViewer";
import { useServerStatus, useLogs, useSlots } from "../api/hooks";

export default function Logs() {
  const { status, refresh } = useServerStatus();
  const { lines, connected, clear } = useLogs();
  const slots = useSlots();

  return (
    <div className="flex flex-col h-full">
      <h1 className="text-2xl font-bold mb-4">Logs</h1>
      <div className="space-y-4 mb-4">
        <ServerStatusCard status={status} slots={slots} />
        <ServerControls status={status} onAction={refresh} />
      </div>
      <div className="flex-1 min-h-0">
        <LogViewer lines={lines} connected={connected} onClear={clear} />
      </div>
    </div>
  );
}
