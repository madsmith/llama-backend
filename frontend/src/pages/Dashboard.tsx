import ServerStatusCard from "../components/ServerStatusCard";
import ServerControls from "../components/ServerControls";
import HealthCard from "../components/HealthCard";
import PropsPanel from "../components/PropsPanel";
import { useServerStatus, useHealth, useSlots, useProps } from "../api/hooks";

export default function Dashboard() {
  const { status, refresh } = useServerStatus();
  const health = useHealth();
  const slots = useSlots();
  const props = useProps();

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold">Dashboard</h1>
      <div className="space-y-4">
        <ServerStatusCard status={status} slots={slots} />
        <ServerControls status={status} onAction={refresh} />
      </div>
      <HealthCard health={health} />
      <PropsPanel props={props} />
    </div>
  );
}
