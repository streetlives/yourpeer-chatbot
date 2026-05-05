// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

"use client";

import { useState } from "react";

export function LogoutButton() {
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function handleLogout() {
    setError(null);
    setPending(true);
    try {
      const res = await fetch("/api/admin/auth", { method: "DELETE" });
      if (!res.ok) {
        // Don't reload if logout failed — the cookie is still valid, the
        // user would just land back on the dashboard thinking it worked.
        // Surface the error inline so they know to try again.
        setError("Sign out failed");
        setPending(false);
        return;
      }
      window.location.reload();
    } catch {
      setError("Sign out failed (network error)");
      setPending(false);
    }
  }

  return (
    <span className="flex items-center gap-2">
      {error && (
        <span className="text-xs text-red-500" role="alert">
          {error}
        </span>
      )}
      <button
        onClick={handleLogout}
        disabled={pending}
        className="text-sm text-neutral-400 hover:text-neutral-600 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
      >
        {pending ? "Signing out…" : "Sign out"}
      </button>
    </span>
  );
}
