import * as vscode from "vscode";
import { ApiError, TickHouseClient, TargetsResponse } from "./api";
import { ResultPanel } from "./resultPanel";

const SECRET_TOKEN_KEY = "tickhouse.token";
const STATE_ROLE_KEY = "tickhouse.role";
const STATE_TARGET_KEY = "tickhouse.target";

let cachedToken: string | undefined;
let client: TickHouseClient;
let statusItem: vscode.StatusBarItem;

function apiUrl(): string {
  return vscode.workspace.getConfiguration("tickhouse").get<string>("apiUrl", "http://localhost:8000");
}

function currentTarget(context: vscode.ExtensionContext): string {
  return context.workspaceState.get<string>(STATE_TARGET_KEY)
    || vscode.workspace.getConfiguration("tickhouse").get<string>("defaultTarget", "gateway");
}

function refreshStatusBar(context: vscode.ExtensionContext) {
  const role = context.workspaceState.get<string>(STATE_ROLE_KEY);
  const target = currentTarget(context);
  statusItem.text = `$(database) TickHouse: ${target}`;
  statusItem.tooltip = role
    ? `Logged in as ${role} · control-api ${apiUrl()}\nClick to change target`
    : `Not logged in · control-api ${apiUrl()}\nClick to change target (login prompts automatically when needed)`;
  statusItem.show();
}

/** 401s mid-command prompt a login right there rather than a dead error. */
async function withAuthRetry<T>(context: vscode.ExtensionContext, fn: () => Promise<T>): Promise<T> {
  try {
    return await fn();
  } catch (err) {
    if (err instanceof ApiError && err.status === 401) {
      const choice = await vscode.window.showWarningMessage(
        "TickHouse: not logged in (or your session expired).",
        "Log In",
      );
      if (choice === "Log In") {
        await loginFlow(context);
        return await fn();
      }
    }
    throw err;
  }
}

async function loginFlow(context: vscode.ExtensionContext): Promise<void> {
  const email = await vscode.window.showInputBox({
    prompt: "TickHouse email",
    placeHolder: "admin@demo-bank.local",
    ignoreFocusOut: true,
  });
  if (!email) return;
  const password = await vscode.window.showInputBox({
    prompt: `Password for ${email}`,
    password: true,
    ignoreFocusOut: true,
  });
  if (password === undefined) return;

  const token = await vscode.window.withProgress(
    { location: vscode.ProgressLocation.Notification, title: "TickHouse: logging in…" },
    () => client.login(email, password),
  );

  cachedToken = token.access_token;
  await context.secrets.store(SECRET_TOKEN_KEY, token.access_token);
  await context.workspaceState.update(STATE_ROLE_KEY, token.role);
  refreshStatusBar(context);
  vscode.window.showInformationMessage(`TickHouse: logged in as ${token.role}.`);
}

async function logoutFlow(context: vscode.ExtensionContext): Promise<void> {
  cachedToken = undefined;
  await context.secrets.delete(SECRET_TOKEN_KEY);
  await context.workspaceState.update(STATE_ROLE_KEY, undefined);
  refreshStatusBar(context);
  vscode.window.showInformationMessage("TickHouse: logged out.");
}

async function pickTargetFlow(context: vscode.ExtensionContext): Promise<void> {
  let targets: TargetsResponse;
  try {
    targets = await withAuthRetry(context, () => client.queryTargets());
  } catch (err) {
    vscode.window.showErrorMessage(`TickHouse: could not list targets - ${describeError(err)}`);
    return;
  }
  const picked = await vscode.window.showQuickPick(
    targets.targets.map((t) => ({ label: t.label, description: t.id, id: t.id })),
    { placeHolder: "Select a query target" },
  );
  if (!picked) return;
  await context.workspaceState.update(STATE_TARGET_KEY, picked.id);
  refreshStatusBar(context);
}

function activeQueryText(): { text: string; fromSelection: boolean } | undefined {
  const editor = vscode.window.activeTextEditor;
  if (!editor) return undefined;
  const sel = editor.selection;
  if (!sel.isEmpty) {
    return { text: editor.document.getText(sel), fromSelection: true };
  }
  const text = editor.document.getText();
  return text.trim() ? { text, fromSelection: false } : undefined;
}

function describeError(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  return err instanceof Error ? err.message : String(err);
}

async function runSelectionFlow(context: vscode.ExtensionContext, extensionUri: vscode.Uri): Promise<void> {
  const picked = activeQueryText();
  if (!picked || !picked.text.trim()) {
    vscode.window.showWarningMessage("TickHouse: no query - open a .q file (or select text) first.");
    return;
  }
  const target = currentTarget(context);
  const limit = vscode.workspace.getConfiguration("tickhouse").get<number>("rowLimit", 1000);
  const panel = ResultPanel.show(extensionUri);
  panel.showLoading(picked.text.trim());
  try {
    const grid = await withAuthRetry(context, () =>
      client.runQuery({ target, query: picked.text, limit }),
    );
    panel.showResult(grid);
  } catch (err) {
    panel.showError(describeError(err));
  }
}

async function generateFlow(context: vscode.ExtensionContext): Promise<void> {
  const nl = await vscode.window.showInputBox({
    prompt: "Describe the query in plain English",
    placeHolder: "e.g. vwap by symbol for AAPL, or last 100 trades",
    ignoreFocusOut: true,
  });
  if (!nl || !nl.trim()) return;
  const target = currentTarget(context);

  let generated;
  try {
    generated = await vscode.window.withProgress(
      { location: vscode.ProgressLocation.Notification, title: "TickHouse: generating q…" },
      () => withAuthRetry(context, () => client.nl2q(nl, target)),
    );
  } catch (err) {
    vscode.window.showErrorMessage(`TickHouse: generation failed - ${describeError(err)}`);
    return;
  }
  if (!generated.ok || !generated.q) {
    vscode.window.showWarningMessage(`TickHouse: could not generate q${generated.error ? " - " + generated.error : ""}.`);
    return;
  }

  const editor = vscode.window.activeTextEditor;
  if (editor && editor.document.languageId === "q") {
    await editor.edit((e) => e.replace(editor.selection, generated!.q!));
  } else {
    const doc = await vscode.workspace.openTextDocument({ language: "q", content: generated.q });
    await vscode.window.showTextDocument(doc);
  }
  vscode.window.showInformationMessage(`TickHouse: generated via ${generated.provider ?? "offline"}.`);
}

async function newQueryFileFlow(): Promise<void> {
  const doc = await vscode.workspace.openTextDocument({ language: "q", content: "select from trade\n" });
  await vscode.window.showTextDocument(doc);
}

async function setApiUrlFlow(): Promise<void> {
  const cfg = vscode.workspace.getConfiguration("tickhouse");
  const value = await vscode.window.showInputBox({
    prompt: "control-api base URL",
    value: cfg.get<string>("apiUrl", "http://localhost:8000"),
    ignoreFocusOut: true,
  });
  if (value === undefined) return;
  await cfg.update("apiUrl", value, vscode.ConfigurationTarget.Workspace);
}

export function activate(context: vscode.ExtensionContext): void {
  client = new TickHouseClient(apiUrl(), () => cachedToken);

  statusItem = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
  statusItem.command = "tickhouse.pickTarget";
  context.subscriptions.push(statusItem);

  context.secrets.get(SECRET_TOKEN_KEY).then((t) => {
    cachedToken = t;
    if (t) context.workspaceState.update(STATE_ROLE_KEY, context.workspaceState.get(STATE_ROLE_KEY) || "session");
    refreshStatusBar(context);
  });

  context.subscriptions.push(
    vscode.workspace.onDidChangeConfiguration((e) => {
      if (e.affectsConfiguration("tickhouse.apiUrl")) {
        client = new TickHouseClient(apiUrl(), () => cachedToken);
      }
      if (e.affectsConfiguration("tickhouse.apiUrl") || e.affectsConfiguration("tickhouse.defaultTarget")) {
        refreshStatusBar(context);
      }
    }),
  );

  context.subscriptions.push(
    vscode.commands.registerCommand("tickhouse.login", () => loginFlow(context)),
    vscode.commands.registerCommand("tickhouse.logout", () => logoutFlow(context)),
    vscode.commands.registerCommand("tickhouse.pickTarget", () => pickTargetFlow(context)),
    vscode.commands.registerCommand("tickhouse.runSelection", () => runSelectionFlow(context, context.extensionUri)),
    vscode.commands.registerCommand("tickhouse.generate", () => generateFlow(context)),
    vscode.commands.registerCommand("tickhouse.newQueryFile", () => newQueryFileFlow()),
    vscode.commands.registerCommand("tickhouse.setApiUrl", () => setApiUrlFlow()),
  );

  refreshStatusBar(context);
}

export function deactivate(): void {
  // nothing to tear down - the webview panel and status bar item are
  // disposed automatically via context.subscriptions.
}
