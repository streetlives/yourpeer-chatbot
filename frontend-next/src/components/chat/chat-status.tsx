// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

interface ChatStatusProps {
  isLoading: boolean;
  error: string | null;
}

export function ChatStatus({ isLoading, error }: ChatStatusProps) {
  return (
    <div
      role="status"
      aria-live="polite"
      aria-atomic="true"
      className={`min-h-[20px] my-2 mx-1 text-sm ${
        error ? "text-red-600 dark:text-red-400" : "text-neutral-400 dark:text-neutral-500"
      }`}
    >
      {error || (isLoading ? "Searching..." : "")}
    </div>
  );
}
