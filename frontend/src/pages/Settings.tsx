import ConfigEditor from "../components/ConfigEditor";

export default function Settings() {
  return (
    <div>
      <h1 className="text-2xl font-bold mb-6">Server Configuration</h1>
      <ConfigEditor />
    </div>
  );
}
