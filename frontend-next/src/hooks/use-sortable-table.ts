// Copyright (c) 2024 Streetlives, Inc.
// Use of this source code is governed by an MIT-style license.

import { useState, useMemo } from "react";

export type SortDir = "asc" | "desc";

export interface SortState<K extends string = string> {
  key: K;
  dir: SortDir;
}

/**
 * Generic hook for client-side table sorting.
 *
 * Usage:
 *   const { sorted, sortKey, sortDir, onSort } = useSortableTable(data, "timestamp", "desc");
 *   <SortableHeader label="Time" field="timestamp" sortKey={sortKey} sortDir={sortDir} onSort={onSort} />
 */
export function useSortableTable<T extends Record<string, unknown>>(
  data: T[],
  defaultKey: string,
  defaultDir: SortDir = "desc",
) {
  const [sort, setSort] = useState<SortState>({ key: defaultKey, dir: defaultDir });

  const onSort = (field: string) => {
    setSort((prev) => ({
      key: field,
      dir: prev.key === field && prev.dir === "desc" ? "asc" : "desc",
    }));
  };

  const sorted = useMemo(() => {
    const { key, dir } = sort;
    // Decorate with the input-order index, sort, then strip the decoration.
    // The original index is the deterministic secondary sort key — without
    // it, two rows tied on the primary key would have an order dependent on
    // V8's array sort stability quirks for large arrays, which can manifest
    // as rows occasionally swapping on re-render with no apparent cause.
    const decorated = data.map((row, _orig) => ({ row, _orig }));
    decorated.sort((a, b) => {
      const av = a.row[key];
      const bv = b.row[key];
      let cmp: number;
      if (av == null && bv == null) cmp = 0;
      else if (av == null) cmp = 1;
      else if (bv == null) cmp = -1;
      else if (typeof av === "number" && typeof bv === "number") cmp = av - bv;
      else cmp = String(av).localeCompare(String(bv));
      const primary = dir === "asc" ? cmp : -cmp;
      // Tiebreaker: original input order, regardless of sort direction.
      // Reversing it when dir==="desc" would create a "stable but mirrored"
      // order that's surprising — leaving it stable means rows tied on the
      // primary key always appear in the same relative order, which is
      // what visual stability requires.
      return primary !== 0 ? primary : a._orig - b._orig;
    });
    return decorated.map((d) => d.row);
  }, [data, sort]);

  return { sorted, sortKey: sort.key, sortDir: sort.dir, onSort };
}
