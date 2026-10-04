/** Page heading with a coloured icon tile. */
export function PageTitle({ icon, title, subtitle, tone = "from-accent to-accent-2", children }: {
  icon: React.ReactNode;
  title: React.ReactNode;
  subtitle?: React.ReactNode;
  tone?: string; // gradient classes for the icon tile
  children?: React.ReactNode; // actions on the right
}) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div className="flex items-center gap-3">
        <span className={`flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-gradient-to-br ${tone} text-white shadow-md shadow-accent/20`}>
          {icon}
        </span>
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
          {subtitle && <p className="mt-0.5 text-sm text-subtle">{subtitle}</p>}
        </div>
      </div>
      {children}
    </div>
  );
}
