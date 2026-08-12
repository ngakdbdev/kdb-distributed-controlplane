import * as vscode from "vscode";
import { QueryGrid } from "./api";

/**
 * Singleton results webview - reused across runs (like a REPL output pane)
 * rather than spawning a new tab per query. Renders the same grid shape
 * control-api returns to the web Query workspace (columns/rows/row_count/
 * kind/...), so a query behaves identically whether it's run here or there.
 */
export class ResultPanel {
  private static current: ResultPanel | undefined;
  private readonly panel: vscode.WebviewPanel;

  private constructor(extensionUri: vscode.Uri) {
    this.panel = vscode.window.createWebviewPanel(
      "tickhouseResult",
      "TickHouse Result",
      { viewColumn: vscode.ViewColumn.Beside, preserveFocus: true },
      { enableScripts: true, retainContextWhenHidden: true },
    );
    this.panel.onDidDispose(() => {
      ResultPanel.current = undefined;
    });
    this.panel.webview.html = this.shell();
  }

  static show(extensionUri: vscode.Uri): ResultPanel {
    if (!ResultPanel.current) {
      ResultPanel.current = new ResultPanel(extensionUri);
    } else {
      ResultPanel.current.panel.reveal(vscode.ViewColumn.Beside, true);
    }
    return ResultPanel.current;
  }

  showLoading(query: string) {
    this.panel.title = "TickHouse: running…";
    this.panel.webview.postMessage({ type: "loading", query });
  }

  showResult(grid: QueryGrid) {
    this.panel.title = `TickHouse: ${grid.row_count} row${grid.row_count === 1 ? "" : "s"}`;
    this.panel.webview.postMessage({ type: "result", grid });
  }

  showError(message: string) {
    this.panel.title = "TickHouse: error";
    this.panel.webview.postMessage({ type: "error", message });
  }

  private shell(): string {
    const nonce = String(Date.now()) + Math.random().toString(36).slice(2);
    const csp = [
      "default-src 'none'",
      `style-src 'unsafe-inline'`,
      `script-src 'nonce-${nonce}'`,
    ].join("; ");
    return /* html */ `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta http-equiv="Content-Security-Policy" content="${csp}">
<style>
  body {
    font-family: var(--vscode-font-family, sans-serif);
    font-size: var(--vscode-font-size, 13px);
    color: var(--vscode-foreground);
    background: var(--vscode-editor-background);
    padding: 0.75rem 1rem;
  }
  .meta { color: var(--vscode-descriptionForeground); margin-bottom: 0.5rem; font-size: 0.9em; }
  .warning { color: var(--vscode-editorWarning-foreground); margin-bottom: 0.5rem; }
  .error { color: var(--vscode-errorForeground); white-space: pre-wrap; font-family: var(--vscode-editor-font-family, monospace); }
  table { border-collapse: collapse; width: 100%; }
  th, td {
    text-align: left;
    padding: 2px 10px 2px 0;
    border-bottom: 1px solid var(--vscode-panel-border);
    white-space: nowrap;
    font-family: var(--vscode-editor-font-family, monospace);
    font-size: 0.92em;
  }
  th { color: var(--vscode-descriptionForeground); font-weight: 600; position: sticky; top: 0; background: var(--vscode-editor-background); }
  .null { opacity: 0.5; }
  .pill { display: inline-block; margin: 0 0.4rem 0.4rem 0; padding: 0.1rem 0.5rem; border-radius: 4px; font-size: 0.85em; }
  .pill.ok { background: var(--vscode-testing-iconPassed, #2ea043); color: white; }
  .pill.fail { background: var(--vscode-testing-iconFailed, #f14c4c); color: white; }
  .empty { color: var(--vscode-descriptionForeground); font-style: italic; }
</style>
</head>
<body>
  <div id="root"><div class="meta">Run a query to see results here.</div></div>
  <script nonce="${nonce}">
    const vscode = acquireVsCodeApi();
    const root = document.getElementById("root");
    function esc(s) {
      return String(s).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
    }
    window.addEventListener("message", (event) => {
      const msg = event.data;
      if (msg.type === "loading") {
        root.innerHTML = '<div class="meta">Running: <code>' + esc(msg.query) + "</code>…</div>";
      } else if (msg.type === "error") {
        root.innerHTML = '<div class="error">' + esc(msg.message) + "</div>";
      } else if (msg.type === "result") {
        root.innerHTML = render(msg.grid);
      }
    });
    function render(grid) {
      const { columns, rows, row_count, truncated, elapsed_ms, kind, warning, per_target, query } = grid;
      let html = "";
      if (per_target) {
        html += per_target.map((t) =>
          '<span class="pill ' + (t.ok ? "ok" : "fail") + '" title="' + esc(t.error || "") + '">' +
          esc(t.target) + ": " + (t.ok ? t.rows + " rows" : "failed") + "</span>"
        ).join("");
      }
      html += '<div class="meta">' + row_count + " row" + (row_count === 1 ? "" : "s") +
        " · " + esc(kind) + (elapsed_ms != null ? " · " + elapsed_ms + " ms" : "") +
        (truncated ? " · showing first " + rows.length : "") +
        (query ? " · <code>" + esc(query) + "</code>" : "") + "</div>";
      if (warning) html += '<div class="warning">' + esc(warning) + "</div>";
      if (!rows.length) {
        html += '<div class="empty">No rows.</div>';
        return html;
      }
      html += "<table><thead><tr>" + columns.map((c) => "<th>" + esc(c) + "</th>").join("") + "</tr></thead><tbody>";
      for (const r of rows) {
        html += "<tr>" + r.map((v) => v === null || v === undefined
          ? '<td class="null">∅</td>'
          : "<td>" + esc(v) + "</td>").join("") + "</tr>";
      }
      html += "</tbody></table>";
      return html;
    }
  </script>
</body>
</html>`;
  }
}
