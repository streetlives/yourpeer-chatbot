// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

interface ChatStatusProps {
  error: string | null;
  isLoading?: boolean;
}

/**
 * Below-the-chat status row.
 *
 * Surfaces two states above the input field:
 *   • Error — red text at the standard `text-sm` / `my-2` size on every
 *     viewport. Errors are rare and need to be read; the larger size
 *     and full vertical margin earn their space.
 *   • Searching — light-gray "Searching…" while a send is in flight
 *     (isLoading=true). The generic catch-all that covers the main
 *     typed-text path; useChat's location-confirmation flows
 *     additionally render their own more specific transient bot
 *     bubbles ("Getting your location…", "Finding where you are…")
 *     inside the chat scroll. The brief overlap on those paths is
 *     acceptable — the inline bubble conveys WHAT step is running and
 *     this row conveys THAT something is running, near where the user
 *     is looking (the input they just submitted from).
 *
 *     Tightened on mobile (`text-xs my-0.5`) — fires on every send, so
 *     conserving the ~17px difference vs the error row matters. Stays
 *     at `text-sm my-2` from the `sm:` breakpoint up where vertical
 *     room is plentiful.
 *
 *  Earlier history: a previous refactor moved the searching indicator
 *  inline-only and dropped it from this row, but the inline pattern
 *  was only wired up in the location-confirmation flows in use-chat,
 *  not the main typed-text send path. That left regular text sends
 *  with zero feedback while the API call was in flight, which users
 *  read as "the app froze." Restoring this catch-all here covers all
 *  send paths in one place.
 */
export function ChatStatus({ error, isLoading = false }: ChatStatusProps) {
  if (error) {
    return (
      <div
        role="status"
        aria-live="polite"
        aria-atomic="true"
        className="min-h-[20px] my-2 mx-1 text-sm text-red-600 dark:text-red-400"
      >
        {error}
      </div>
    );
  }
  if (isLoading) {
    return (
      <div
        role="status"
        aria-live="polite"
        aria-atomic="true"
        className="my-0.5 sm:my-2 mx-1 text-xs sm:text-sm text-neutral-400 dark:text-neutral-500"
      >
        Searching…
      </div>
    );
  }
  return null;
}
