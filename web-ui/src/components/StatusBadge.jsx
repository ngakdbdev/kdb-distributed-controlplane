const COLORS = {
  running: "var(--success)",
  exited: "var(--danger)",
  restarting: "var(--warn)",
  not_found: "var(--muted)",
  dead: "var(--danger)",
  paused: "var(--warn)",
};
const TEXT_ON = { running: "#06281c", exited: "#2c0a0f", restarting: "#2e1e02", paused: "#2e1e02" };

export default function StatusBadge({ status }) {
  const color = COLORS[status] || "var(--muted)";
  return (
    <span className="badge" style={{ backgroundColor: color, color: TEXT_ON[status] || "#0a0d14", fontWeight: 700 }}>
      {status === "restarting" && <span className="status-pulse-dot" />}
      {status}
    </span>
  );
}
