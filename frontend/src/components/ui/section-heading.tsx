export function SectionHeading({
  eyebrow,
  title,
  description,
  actions,
}: {
  eyebrow: string;
  title: string;
  description: string;
  actions?: React.ReactNode;
}) {
  return (
    <div className="mb-5 flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
      <div>
        <div className="eyebrow">{eyebrow}</div>
        <h1 className="mt-2 text-balance text-3xl leading-tight">{title}</h1>
        <p className="mt-2 max-w-3xl text-sm leading-6 text-ink-secondary">{description}</p>
      </div>
      {actions ? <div className="flex flex-wrap gap-2">{actions}</div> : null}
    </div>
  );
}
