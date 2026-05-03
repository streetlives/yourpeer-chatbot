// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

import Link from "next/link";

export default function NotFound() {
  return (
    <div className="min-h-dvh flex items-center justify-center bg-neutral-50 px-4 dark:bg-neutral-950">
      <div className="text-center max-w-md">
        <h1 className="text-5xl font-bold text-neutral-300 mb-4 dark:text-neutral-700">404</h1>
        <h2 className="text-lg font-semibold text-neutral-900 mb-2 dark:text-neutral-100">
          Page not found
        </h2>
        <p className="text-sm text-neutral-500 mb-6 leading-relaxed dark:text-neutral-400">
          The page you&apos;re looking for doesn&apos;t exist. You can head back
          to the chat to find services.
        </p>
        <Link
          href="/chat"
          className="inline-block px-5 py-2.5 text-sm font-medium text-white bg-amber-500 rounded-lg hover:bg-amber-600 transition-colors dark:bg-amber-600 dark:hover:bg-amber-500"
        >
          Go to chat
        </Link>
      </div>
    </div>
  );
}
