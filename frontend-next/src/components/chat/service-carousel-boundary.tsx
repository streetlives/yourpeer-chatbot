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

export class ServiceCarouselBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { hasError: false };
  }

  static getDerivedStateFromError(): State {
    return { hasError: true };
  }

  componentDidCatch(error: Error) {
    console.error("ServiceCarousel render error:", error);
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="self-start w-full bg-neutral-100 border border-neutral-200 rounded-2xl px-5 py-4 text-sm text-neutral-500 dark:bg-neutral-900 dark:border-neutral-800 dark:text-neutral-400">
          <p className="font-medium text-neutral-700 mb-1 dark:text-neutral-200">
            Couldn&apos;t display service results
          </p>
          <p>
            Try asking again, or visit{" "}
            <a
              href="https://yourpeer.nyc"
              className="text-amber-500 underline hover:text-amber-600 dark:text-amber-400 dark:hover:text-amber-300"
              target="_blank"
              rel="noopener noreferrer"
            >
              yourpeer.nyc
            </a>{" "}
            to search directly.
          </p>
        </div>
      );
    }

    return this.props.children;
  }
}
