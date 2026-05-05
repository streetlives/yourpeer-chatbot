// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

interface ChatStatusProps {
  error: string | null;
}

/**
 * Below-the-chat status row.
 *
 * Originally also showed "Searching..." during isLoading, but that
 * indicator sits below the chat scroll area where users don't look.
 * The searching indicator now renders inline as a transient bot
 * bubble inside ChatContainer (same visual treatment as the
 * geolocation flow's "Getting your location..." message), and this
 * component is only responsible for surfacing errors.
 *
 */
export function ChatStatus({ error }: ChatStatusProps) {
  if (!error) return null;
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
