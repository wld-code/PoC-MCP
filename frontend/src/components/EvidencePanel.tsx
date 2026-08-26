export function EvidencePanel({ toolCalls }: { toolCalls: { name: string; arguments: Record<string, unknown> }[] }) {
  if (!toolCalls?.length) return null;
  return (
    <div className="evidence">
      <strong>🔧 Evidence — tools called:</strong>
      <ul style={{ margin: "6px 0 0", paddingLeft: 18 }}>
        {toolCalls.map((t, i) => (
          <li key={i}>
            <code>{t.name}</code>
            {Object.keys(t.arguments || {}).length > 0 && (
              <span style={{ color: "var(--muted)" }}> {JSON.stringify(t.arguments)}</span>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}
