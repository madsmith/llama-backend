import { type BackendEntry, type BackendGroup, type BackendRef, findBackend } from "../api/chat";
import GroupedSelect from "./inputs/GroupedSelect";

const KIND_LABEL: Record<BackendEntry["kind"], string> = {
  local: "local",
  remote_api: "remote API",
  manager: "remote manager",
};

function entryDetail(e: BackendEntry): string {
  const parts = [e.name && e.name !== e.model_id ? e.name : null, KIND_LABEL[e.kind]];
  if (e.priority !== null) parts.push(`priority ${e.priority}`);
  if (e.state && e.state !== "remote") parts.push(e.state);
  return parts.filter(Boolean).join(" · ");
}

/**
 * Advanced-mode picker: every backend grouped under the llama-manager that
 * owns it, so a chat can be pinned to one provider instead of proxy routing.
 */
export default function BackendPicker({ groups, selected, model, onSelect }: {
  groups: BackendGroup[];
  selected: string | null | undefined;
  /** Chat's model, shown when the selected backend isn't (or is no longer) listed. */
  model: string;
  onSelect: (ref: BackendRef) => void;
}) {
  const current = findBackend(groups, selected);
  const total = groups.reduce((n, g) => n + g.backends.length, 0);

  const label = (ref: BackendRef) => (
    <>
      <span className="font-mono">{ref.entry.model_id}</span>
      <span className="text-gray-500"> · {ref.group.name} / {ref.entry.name ?? ref.entry.suid.slice(0, 8)}</span>
    </>
  );

  const trigger = total === 0 ? (
    <span className="text-gray-500">No backends</span>
  ) : current ? (
    label(current)
  ) : (
    <>
      <span className="font-mono">{model || "Select backend"}</span>
      {selected && <span className="text-gray-500"> · unavailable</span>}
    </>
  );

  return (
    <GroupedSelect
      ariaLabel="Backend"
      disabled={total === 0}
      selected={selected}
      trigger={trigger}
      sizeTo={groups.flatMap((group) => group.backends.map((entry) => label({ group, entry })))}
      onSelect={(suid) => { const ref = findBackend(groups, suid); if (ref) onSelect(ref); }}
      sections={groups.map((g) => ({
        key: g.id,
        empty: "No models",
        header: (
          <>
            <span className="truncate">{g.name}</span>
            {g.local && <span className="normal-case tracking-normal text-gray-600">this manager</span>}
            {!g.local && g.connection_state !== "connected" && (
              <span className="normal-case tracking-normal text-yellow-600">{g.connection_state}</span>
            )}
          </>
        ),
        options: g.backends.map((b) => ({ key: b.suid, label: b.model_id, detail: entryDetail(b) })),
      }))}
    />
  );
}
