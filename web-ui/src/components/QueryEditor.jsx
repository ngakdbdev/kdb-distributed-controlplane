import { useEffect, useMemo, useRef } from "react";
import CodeMirror from "@uiw/react-codemirror";
import { autocompletion } from "@codemirror/autocomplete";
import { keymap } from "@codemirror/view";
import { oneDark } from "@codemirror/theme-one-dark";
import { qStreamLanguage, createQCompletionSource } from "../lib/qLanguage.js";

/**
 * The query workspace's editor: a real code editor (CodeMirror 6) instead
 * of a plain textarea, so autocomplete gets proper IDE behavior for free -
 * a popup that tracks the cursor, fuzzy prefix matching, Tab/Enter to
 * accept, arrow keys to navigate, Escape to dismiss - all from
 * @codemirror/autocomplete rather than hand-rolled position math.
 *
 * `tables`/`liveSchema` come from Query.jsx's live `cols <table>` fetches
 * and change over time as they load; they're threaded through a ref (not a
 * dependency of the extensions array) so the completion source always sees
 * fresh data without tearing down/rebuilding the editor state - doing that
 * on every fetch would reset undo history and can drop cursor position.
 */
export default function QueryEditor({ value, onChange, onRun, tables, liveSchema, placeholder }) {
  const liveStateRef = useRef({ tables, liveSchema });
  useEffect(() => {
    liveStateRef.current = { tables, liveSchema };
  }, [tables, liveSchema]);

  const onRunRef = useRef(onRun);
  useEffect(() => {
    onRunRef.current = onRun;
  });

  const completionSource = useMemo(() => createQCompletionSource(liveStateRef), []);

  const extensions = useMemo(() => [
    qStreamLanguage,
    autocompletion({ override: [completionSource] }),
    keymap.of([
      {
        key: "Mod-Enter",
        run: () => {
          onRunRef.current?.();
          return true;
        },
      },
    ]),
  ], [completionSource]);

  return (
    <CodeMirror
      value={value}
      onChange={onChange}
      theme={oneDark}
      extensions={extensions}
      height="auto"
      minHeight="140px"
      maxHeight="420px"
      placeholder={placeholder}
      basicSetup={{ foldGutter: false, highlightActiveLine: false }}
    />
  );
}
