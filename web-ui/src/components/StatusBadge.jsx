const COLORS = {
  running: "#1a7f37",
  exited: "#cf222e",
  restarting: "#9a6700",
  not_found: "#6e7781",
  dead: "#cf222e",
  paused: "#9a6700",
};

export default function StatusBadge({ status }) {
  const color = COLORS[status] || "#6e7781";
  return (
    <span className="badge" style={{ backgroundColor: color }}>
      {status}
    </span>
  );
}
