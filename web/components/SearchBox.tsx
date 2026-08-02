"use client";

import { useEffect, useRef, useState } from "react";
import { isTypingTarget } from "@/lib/shortcuts";
import { SearchIcon } from "./icons";

export function SearchBox({
  initial = "",
  loading,
  size = "md",
  onSearch,
}: {
  initial?: string;
  loading: boolean;
  size?: "md" | "lg";
  onSearch: (q: string) => void;
}) {
  const [value, setValue] = useState(initial);
  const ref = useRef<HTMLInputElement>(null);

  useEffect(() => setValue(initial), [initial]);

  // "/" focuses the box from anywhere
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "/" && !isTypingTarget(e.target)) {
        e.preventDefault();
        ref.current?.focus();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  return (
    <form
      className={`searchbox${size === "lg" ? " lg" : ""}`}
      onSubmit={(e) => {
        e.preventDefault();
        if (value.trim()) onSearch(value.trim());
      }}
    >
      <span className="glass">
        <SearchIcon size={size === "lg" ? 20 : 18} />
      </span>
      <input
        ref={ref}
        value={value}
        placeholder="Ask about PostgreSQL, MySQL, SQLite, Redis…"
        onChange={(e) => setValue(e.target.value)}
        autoFocus={size === "lg"}
        spellCheck={false}
        aria-label="Search query"
      />
      <button className="go" type="submit" disabled={loading || !value.trim()}>
        {loading ? <span className="spinner" /> : "Search"}
      </button>
    </form>
  );
}
