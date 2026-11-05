export default function SectionDivider({ title, first = false }: { title: string; first?: boolean }) {
  return (
    <div className={first ? "" : "border-t border-gray-700 pt-4"}>
      <h4 className="text-xs font-semibold uppercase tracking-wider text-gray-500">{title}</h4>
    </div>
  );
}
