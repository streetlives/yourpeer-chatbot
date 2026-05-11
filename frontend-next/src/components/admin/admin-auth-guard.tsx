// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

"use client";

import { useEffect, useState, useCallback } from "react";
import { LoginForm } from "./login-form";

type AuthState = "loading" | "authenticated" | "unauthenticated" | "infra-error";

export function AdminAuthGuard({ children }: { children: React.ReactNode }) {
  const [authState, setAuthState] = useState<AuthState>("loading");

  const checkAuth = useCallback(async () => {
    try {
      // `cache: "no-store"` — without it Next.js/the browser can serve
      // a stale "authenticated" response, letting an expired session
      // appear past the login screen. See lib/chat/api.ts for the same
      // fix on admin GET endpoints.
      const res = await fetch("/api/admin/auth", { cache: "no-store" });
      // A non-OK response (typically 5xx) is an infrastructure problem,
      // not an auth signal. Rendering the login form in that case would
      // be misleading — the user thinks "I'm not signed in" when actually
      // the backend can't tell us either way. The "infra-error" state
      // surfaces a retry prompt instead.
      //
      // 401 is conventionally "you're not authenticated, log in" — fetch
      // treats it as `res.ok === false`, but the auth API returns 200
      // with `{authenticated: false}` for that case (the existing
      // contract). So we only fall into the infra-error branch on 5xx
      // and other transport-level failures.
      if (!res.ok) {
        setAuthState("infra-error");
        return;
      }
      const data = await res.json();
      setAuthState(data.authenticated ? "authenticated" : "unauthenticated");
    } catch {
      setAuthState("infra-error");
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- one-shot auth check on mount; would be overkill to pull in a data-fetching library for this
    checkAuth();
  }, [checkAuth]);

  if (authState === "loading") {
    return (
      <div className="min-h-dvh bg-neutral-100 dark:bg-neutral-950 flex items-center justify-center">
        <p className="text-neutral-400 dark:text-neutral-500 text-sm">Checking access…</p>
      </div>
    );
  }

  if (authState === "infra-error") {
    return (
      <div className="min-h-dvh bg-neutral-100 dark:bg-neutral-950 flex items-center justify-center px-4">
        <div className="w-full max-w-sm text-center" role="alert">
          <div className="text-3xl mb-3">⚠️</div>
          <h2 className="text-base font-semibold text-neutral-900 dark:text-neutral-100 mb-1">
            Backend unreachable
          </h2>
          <p className="text-sm text-neutral-500 dark:text-neutral-400 mb-5">
            We can&apos;t verify your sign-in right now. The server may be down
            or restarting.
          </p>
          <button
            onClick={() => {
              setAuthState("loading");
              checkAuth();
            }}
            className="px-4 py-2 rounded-lg text-sm font-medium border border-neutral-200 dark:border-neutral-800 bg-white dark:bg-neutral-900 text-neutral-700 dark:text-neutral-200 hover:bg-neutral-50 dark:hover:bg-neutral-800/40 transition"
          >
            Retry
          </button>
        </div>
      </div>
    );
  }

  if (authState === "unauthenticated") {
    return <LoginForm onSuccess={() => setAuthState("authenticated")} />;
  }

  return <>{children}</>;
}
