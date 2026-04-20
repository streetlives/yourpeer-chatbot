// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

"use client";

import { useState, useEffect } from "react";

/**
 * Track browser online/offline status.
 * Returns true when the browser reports navigator.onLine,
 * and listens for online/offline events to update reactively.
 */
export function useOnlineStatus(): boolean {
  const [isOnline, setIsOnline] = useState(true);

  useEffect(() => {
    // Set initial state from browser (safe for SSR — defaults to true)
    // eslint-disable-next-line react-hooks/set-state-in-effect -- navigator is undefined during SSR; one-time bridge from SSR default to real client state
    setIsOnline(navigator.onLine);

    const handleOnline = () => setIsOnline(true);
    const handleOffline = () => setIsOnline(false);

    window.addEventListener("online", handleOnline);
    window.addEventListener("offline", handleOffline);

    return () => {
      window.removeEventListener("online", handleOnline);
      window.removeEventListener("offline", handleOffline);
    };
  }, []);

  return isOnline;
}
