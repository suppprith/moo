"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import type { Command } from "@/lib/shortcuts";
import { filterCommands, moveSelection } from "@/lib/shortcuts";

/**
 * Ctrl+K. Everything the toolbar can do, plus the pages, without reaching for
 * the mouse. Commands come from the page so the palette never has to know what
 * state the app is in.
 */
export function CommandPalette({
  commands,
  onRun,
  onClose,
}: {
  commands: Command[];
  onRun: (id: string) => void;
  onClose: () => void;
}) {
  const [query, setQuery] = useState("");
  const [index, setIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  const matches = useMemo(() => filterCommands(commands, query), [commands, query]);

  useEffect(() => inputRef.current?.focus(), []);
  useEffect(() => setIndex(0), [query]);

  useEffect(() => {
    const selected = listRef.current?.querySelector<HTMLElement>("[data-selected='true']");
    selected?.scrollIntoView({ block: "nearest" });
  }, [index]);

  const grouped = useMemo(() => {
    const order: Command["group"][] = ["Search", "View", "Go"];
    return order
      .map((group) => ({ group, items: matches.filter((c) => c.group === group) }))
      .filter((section) => section.items.length > 0);
  }, [matches]);

  function onKeyDown(event: React.KeyboardEvent) {
    if (event.key === "ArrowDown" || (event.key === "n" && event.ctrlKey)) {
      event.preventDefault();
      setIndex((i) => moveSelection(i, 1, matches.length));
    } else if (event.key === "ArrowUp" || (event.key === "p" && event.ctrlKey)) {
      event.preventDefault();
      setIndex((i) => moveSelection(i, -1, matches.length));
    } else if (event.key === "Enter") {
      event.preventDefault();
      const command = matches[index];
      if (command) onRun(command.id);
    } else if (event.key === "Escape") {
      event.preventDefault();
      onClose();
    }
  }

  let running = -1;
  return (
    <>
      <div className="palette-scrim" onClick={onClose} aria-hidden />
      <div className="palette" role="dialog" aria-label="Command palette">
        <input
          ref={inputRef}
          className="palette-input"
          value={query}
          placeholder="Type a command…"
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={onKeyDown}
          spellCheck={false}
          aria-label="Command"
        />
        <div className="palette-list" ref={listRef}>
          {grouped.length === 0 && <div className="palette-empty">Nothing matches.</div>}
          {grouped.map((section) => (
            <div className="palette-group" key={section.group}>
              <div className="palette-group-title">{section.group}</div>
              {section.items.map((command) => {
                running += 1;
                const selected = running === index;
                return (
                  <button
                    className="palette-item"
                    data-selected={selected}
                    key={command.id}
                    onMouseEnter={() => setIndex(matches.indexOf(command))}
                    onClick={() => onRun(command.id)}
                  >
                    <span className="palette-label">{command.label}</span>
                    {command.hint && <span className="palette-hint">{command.hint}</span>}
                    {command.keys && <kbd>{command.keys}</kbd>}
                  </button>
                );
              })}
            </div>
          ))}
        </div>
      </div>
    </>
  );
}

/** The "?" list. Same source of truth as the keys the page actually binds. */
export function ShortcutHelp({
  shortcuts,
  onClose,
}: {
  shortcuts: { keys: string; does: string }[];
  onClose: () => void;
}) {
  return (
    <>
      <div className="palette-scrim" onClick={onClose} aria-hidden />
      <div className="shortcut-help" role="dialog" aria-label="Keyboard shortcuts">
        <div className="sh-title">Keyboard</div>
        <dl>
          {shortcuts.map((entry) => (
            <div className="sh-row" key={entry.keys}>
              <dt>
                {entry.keys.split(" ").map((key) => (
                  <kbd key={key}>{key}</kbd>
                ))}
              </dt>
              <dd>{entry.does}</dd>
            </div>
          ))}
        </dl>
      </div>
    </>
  );
}
