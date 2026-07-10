"use client";

import { useEffect, useRef, useState } from "react";

export function SearchBox({
  initial = "",
  loading,
  onSearch,
}: {
  initial?: string;
  loading: boolean;
  onSearch: (q: string) => void;
}) {
  const [value, setValue] = useState(initial);
  const ref = useRef<HTMLInputElement>(null);

  useEffect(() => setValue(initial), [initial]);

  // "/" focuses the box from anywhere (fuller keyboard model lands in SUP-101)
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "/" && document.activeElement !== ref.current) {
        e.preventDefault();
        ref.current?.focus();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  return (
    <form
      className="searchbox"
      onSubmit={(e) => {
        e.preventDefault();
        if (value.trim()) onSearch(value.trim());
      }}
    >
      <input
        ref={ref}
        value={value}
        placeholder="Search the databases corpus…  (press / to focus)"
        onChange={(e) => setValue(e.target.value)}
        autoFocus
        spellCheck={false}
        aria-label="Search query"
      />
      <button type="submit" disabled={loading || !value.trim()}>
        {loading ? <span className="spinner" /> : "Search"}
      </button>
    </form>
  );
}
