import type { ModelProps } from "../api/types";

interface Props {
  props: ModelProps | null;
}

export default function PropsPanel({ props }: Props) {
  return (
    <div className="rounded-xl border border-gray-800 bg-gray-900 p-5">
      <h3 className="mb-3 text-sm font-semibold text-gray-400 uppercase tracking-wider">
        Model Properties
      </h3>
      {!props ? (
        <p className="text-sm text-gray-500">No properties available.</p>
      ) : (
        <pre className="overflow-auto rounded-lg bg-black p-3 text-xs text-gray-300">
          {JSON.stringify(props, null, 2)}
        </pre>
      )}
    </div>
  );
}
