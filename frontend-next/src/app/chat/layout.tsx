// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

import { QuickExit } from "@/components/chat/quick-exit";

export default function ChatLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <main className="min-h-dvh bg-neutral-50 dark:bg-neutral-950">
      {/* Quick Exit lives at the layout level so it's present on every
          chat-area route and isn't tied to the ChatContainer's
          hydration state — users in a DV/surveillance situation must
          be able to leave even before the chat finishes loading. */}
      <QuickExit />
      {children}
    </main>
  );
}
