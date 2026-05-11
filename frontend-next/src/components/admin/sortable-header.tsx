// Copyright (c) 2024 Streetlives, Inc.
// Use of this source code is governed by an MIT-style license.

"use client";

import type { SortDir } from "@/hooks/use-sortable-table";

interface SortableHeaderProps {
  label: string;
  field: string;
  sortKey: string;
  sortDir: SortDir;
  onSort: (field: string) => void;
  className?: string;
}

export function SortableHeader({
  label, field, sortKey, sortDir, onSort, className = "",
}: SortableHeaderProps) {
  const isActive = sortKey === field;
  const arrow = isActive ? (sortDir === "asc" ? " ▲" : " ▼") : "";

  return (
    <th
      className={`text-left px-0 py-0 border-b border-neutral-200 dark:border-neutral-800 ${className}`}
      aria-sort={isActive ? (sortDir === "asc" ? "ascending" : "descending") : "none"}
      scope="col"
    >
      <button
        type="button"
        onClick={() => onSort(field)}
        className={`w-full text-left px-4 py-3 text-xs font-semibold cursor-pointer select-none transition-colors hover:text-neutral-600 dark:hover:text-neutral-200 focus:outline-none focus-visible:ring-2 focus-visible:ring-amber-400 focus-visible:ring-inset ${
          isActive ? "text-amber-600 dark:text-amber-400" : "text-neutral-500 dark:text-neutral-400"
        }`}
        aria-label={`Sort by ${label}${isActive ? `, currently ${sortDir === "asc" ? "ascending" : "descending"}` : ""}`}
      >
        {label}{arrow}
      </button>
    </th>
  );
}
