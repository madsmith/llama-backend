import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { ServerConfig } from "../api/types";
import ConfigEditor from "../components/ConfigEditor";
import { defaultConfig } from "../components/config-defaults";
import type { SettingsTab } from "../components/config-defaults";
import { TabButton, PANEL_BG } from "../components/TabBar";

export default function Settings() {
  const [config, setConfig] = useState<ServerConfig>(defaultConfig);
  const [tab, setTab] = useState<SettingsTab>("manager");

  useEffect(() => {
    document.title = "Llama Manager - Settings";
  }, []);

  useEffect(() => {
    api
      .getConfig()
      .then(setConfig)
      .catch(() => {});
  }, []);

  const modelIndex = tab.startsWith("model-") ? Number(tab.split("-")[1]) : 0;

  const addModel = () => {
    const blank = {
      ...defaultConfig.models[0],
      advanced: { ...defaultConfig.models[0].advanced, extra_args: [] },
    };
    setConfig({ ...config, models: [...config.models, blank] });
    setTab(`model-${config.models.length}`);
  };

  const deleteModel = (index: number) => {
    setConfig({
      ...config,
      models: config.models.filter((_, i) => i !== index),
    });
    setTab("model-0");
  };

  return (
    <div>
      <h1 className="text-2xl font-bold mb-6">Server Configuration</h1>
      <div className="flex items-end gap-1.5">
        <TabButton
          label="General"
          active={tab === "manager"}
          onClick={() => setTab("manager")}
        />
        <TabButton
          label="Proxy Server"
          active={tab === "proxy"}
          onClick={() => setTab("proxy")}
        />
        {config.models.map((m, i) => (
          <TabButton
            key={i}
            label={m.name ?? `Model ${i + 1}`}
            active={tab === `model-${i}`}
            onClick={() => setTab(`model-${i}`)}
          />
        ))}
        <button
          className="min-w-[4em] px-3 py-2.5 text-sm font-medium rounded-t-lg bg-[#111419] text-gray-600 hover:text-gray-400 transition"
          title="Add model"
          onClick={addModel}
        >
          ⊕
        </button>
      </div>
      <div className={`${PANEL_BG} rounded-b-lg rounded-tr-lg p-6`}>
        <ConfigEditor
          tab={tab}
          config={config}
          setConfig={setConfig}
          modelIndex={modelIndex}
          onDeleteModel={deleteModel}
        />
      </div>
    </div>
  );
}
