// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

"use client";

import { Component } from "react";
import type { ReactNode } from "react";

interface Props {
  children: ReactNode;
}

interface State {
  hasError: boolean;
}

/**
 * Error boundary for individual chat messages.
 *
 * If a single message fails to render (malformed data, unexpected field
 * types, etc.), this catches the error and shows a minimal fallback
 * instead of crashing the entire chat UI. The user can continue the
 * conversation normally — only the broken message is affected.
 */
export class ChatMessageBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { hasError: false };
  }

  static getDerivedStateFromError(): State {
    return { hasError: true };
  }

  componentDidCatch(error: Error) {
    console.error("ChatMessage render error:", error);
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="self-start max-w-[82%] px-4 py-3 rounded-2xl bg-neutral-100 text-sm text-neutral-400 italic dark:bg-neutral-800 dark:text-neutral-500">
          This message couldn&apos;t be displayed.
        </div>
      );
    }

    return this.props.children;
  }
}
