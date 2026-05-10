// Copyright (c) 2024 Streetlives, Inc.
// Use of this source code is governed by an MIT-style license.

"use client";

import { useEffect, useState } from "react";
import { isAdminApiError } from "@/lib/admin/locations-types";

/**
 * Shared fetch state for all locations-admin (and broader admin)
 * components. Eleven components used to inline the same ~25 LOC of
 * `useState` + `useEffect` + cancelled-check + isAdminApiError
 * + setLoading scaffolding. Extracting here:
 *
 *   1. Reduces each component's fetch boilerplate to ONE line.
 *   2. Centralizes the `isAdminApiError` check so the error contract
 *      stays in one place (matches the M3 / H3 single-source-of-truth
 *      pattern).
 *   3. Centralizes the eslint-disable for the legitimate
 *      mount-time-setLoading pattern, instead of 11 disable comments.
 *
 * Usage:
 *
 *     const { data, loading, error } = useAdminFetch<MyResponse>(
 *       "/api/admin/locations/something",
 *     );
 *     if (error) return <ErrorBlock error={error} ... />;
 *     if (loading || !data) return <Skeleton />;
 *     // render data
 *
 * The cancelled-on-unmount pattern protects against state updates
 * after a navigation away during fetch — React would warn in
 * development, and in some cases would leak the promise chain.
 *
 * `url` participates in the effect dependency array, so passing a
 * URL that includes query-string state (filters etc.) re-fetches on
 * change. Components that need that just put filter values in the
 * URL string; no additional plumbing needed. Components with a stable
 * URL get a single mount-time fetch by default.
 */
export interface AdminFetchState<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
}

export function useAdminFetch<T>(url: string): AdminFetchState<T> {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- mount-time fetch needs to mark loading state; standard pattern in this codebase. Centralized here so per-component disable comments aren't needed.
    setLoading(true);
    setError(null);
    fetch(url)
      .then((r) => r.json())
      .then((body) => {
        if (cancelled) return;
        if (isAdminApiError(body)) {
          setError(body.detail);
        } else {
          setData(body as T);
        }
        setLoading(false);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(String(err));
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [url]);

  return { data, loading, error };
}
