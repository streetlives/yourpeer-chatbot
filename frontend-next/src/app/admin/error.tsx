// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

"use client";

import { useEffect } from "react";

export default function AdminError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error("Admin error:", error);
  }, [error]);

  return (
    <div className="mt-10 text-center">
      <div className="inline-block bg-white dark:bg-neutral-900 border border-neutral-200 dark:border-neutral-800 rounded-xl p-8 shadow-sm max-w-md">
        <h2 className="text-lg font-semibold text-neutral-900 dark:text-neutral-100 mb-2">
          Failed to load this page
        </h2>
        <p className="text-sm text-neutral-500 dark:text-neutral-400 mb-1.5 leading-relaxed">
          {error.message || "An unexpected error occurred."}
        </p>
        {error.digest && (
          <p className="text-xs text-neutral-400 dark:text-neutral-500 mb-5">
            Error ID: {error.digest}
          </p>
        )}

        <div className="flex gap-2.5 justify-center">
          <button
            onClick={reset}
            className="px-4 py-2 text-sm font-medium text-white bg-amber-500 dark:bg-amber-500 rounded-lg hover:bg-amber-600 dark:hover:bg-amber-400 transition-colors"
          >
            Retry
          </button>
          <a
            href="/admin/overview"
            className="px-4 py-2 text-sm font-medium text-neutral-700 dark:text-neutral-200 bg-neutral-100 dark:bg-neutral-800 rounded-lg hover:bg-neutral-200 dark:hover:bg-neutral-700 transition-colors inline-block"
          >
            Back to overview
          </a>
        </div>
      </div>
    </div>
  );
}
