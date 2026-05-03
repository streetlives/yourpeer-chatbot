// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

"use client";

import { useEffect } from "react";

export default function ChatError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error("Chat error:", error);
  }, [error]);

  return (
    <div className="flex flex-col items-center justify-center max-w-[820px] mx-auto px-4 min-h-dvh text-center">
      <div className="bg-white border border-neutral-200 rounded-2xl p-8 shadow-sm max-w-md w-full dark:bg-neutral-900 dark:border-neutral-800">
        <h2 className="text-lg font-semibold text-neutral-900 mb-2 dark:text-neutral-100">
          Chat isn&apos;t loading right now
        </h2>
        <p className="text-sm text-neutral-500 mb-6 leading-relaxed dark:text-neutral-400">
          Something unexpected happened. You can try reloading, or visit{" "}
          <a
            href="https://yourpeer.nyc"
            className="text-amber-500 underline hover:text-amber-600 dark:text-amber-400 dark:hover:text-amber-300"
          >
            yourpeer.nyc
          </a>{" "}
          to find services directly.
        </p>

        <div className="flex flex-col gap-2.5">
          <button
            onClick={reset}
            className="w-full px-4 py-2.5 text-sm font-medium text-white bg-amber-500 rounded-lg hover:bg-amber-600 transition-colors dark:bg-amber-600 dark:hover:bg-amber-500"
          >
            Try again
          </button>
          <a
            href="/chat"
            className="w-full px-4 py-2.5 text-sm font-medium text-neutral-700 bg-neutral-100 rounded-lg hover:bg-neutral-200 transition-colors inline-block dark:text-neutral-200 dark:bg-neutral-800 dark:hover:bg-neutral-700"
          >
            Restart chat
          </a>
        </div>

        <p className="text-xs text-neutral-400 mt-5 dark:text-neutral-500">
          If you&apos;re in crisis, call or text{" "}
          <a href="tel:988" className="underline">
            988
          </a>{" "}
          (Suicide &amp; Crisis Lifeline) anytime.
        </p>
      </div>
    </div>
  );
}
