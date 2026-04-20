// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

"use client";

import { useEffect, useState, useCallback } from "react";
import { LoginForm } from "./login-form";

type AuthState = "loading" | "authenticated" | "unauthenticated";

export function AdminAuthGuard({ children }: { children: React.ReactNode }) {
  const [authState, setAuthState] = useState<AuthState>("loading");

  const checkAuth = useCallback(async () => {
    try {
      const res = await fetch("/api/admin/auth");
      const data = await res.json();
      setAuthState(data.authenticated ? "authenticated" : "unauthenticated");
    } catch {
      setAuthState("unauthenticated");
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- one-shot auth check on mount; would be overkill to pull in a data-fetching library for this
    checkAuth();
  }, [checkAuth]);

  if (authState === "loading") {
    return (
      <div className="min-h-dvh bg-neutral-100 flex items-center justify-center">
        <p className="text-neutral-400 text-sm">Checking access…</p>
      </div>
    );
  }

  if (authState === "unauthenticated") {
    return <LoginForm onSuccess={() => setAuthState("authenticated")} />;
  }

  return <>{children}</>;
}
