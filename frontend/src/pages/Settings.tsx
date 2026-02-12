import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { ServerConfig } from "../api/types";
import ConfigEditor, { defaultConfig } from "../components/ConfigEditor";
import type { SettingsTab } from "../components/ConfigEditor";
import { TabButton, PANEL_BG } from "../components/TabBar";

export default function Settings() {
  const [config, setConfig] = useState<ServerConfig>(defaultConfig);
  const [tab, setTab] = useState<SettingsTab>("api-server");

  useEffect(() => {
    api.getConfig().then(setConfig).catch(() => {});
  }, []);

  return (
    <div>
      <h1 className="text-2xl font-bold mb-6">Server Configuration</h1>
      <div className="flex items-end gap-1.5">
        <TabButton label="API Server" active={tab === "api-server"} onClick={() => setTab("api-server")} />
        <TabButton label="Llama Manager UI" active={tab === "manager"} onClick={() => setTab("manager")} />
      </div>
      <div className={`${PANEL_BG} rounded-b-lg rounded-tr-lg p-6`}>
        <ConfigEditor tab={tab} config={config} setConfig={setConfig} />
      </div>
    </div>
  );
}
