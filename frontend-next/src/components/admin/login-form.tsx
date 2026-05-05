// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

"use client";

import { useState } from "react";

interface LoginFormProps {
  onSuccess: () => void;
}

export function LoginForm({ onSuccess }: LoginFormProps) {
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);

    try {
      const res = await fetch("/api/admin/auth", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ password }),
      });

      if (res.ok) {
        onSuccess();
      } else {
        const data = await res.json().catch(() => null);
        setError(data?.error || "Invalid password");
        setPassword("");
      }
    } catch {
      setError("Connection error. Please try again.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="min-h-dvh bg-neutral-100 flex items-center justify-center px-4">
      <div className="w-full max-w-sm">
        <div className="text-center mb-8">
          <h1 className="text-xl font-bold tracking-tight text-amber-500">
            YourPeer AI
          </h1>
          <p className="text-sm text-neutral-400 mt-1">Staff Review Console</p>
        </div>

        <div className="bg-white rounded-xl border border-neutral-200 shadow-sm p-6">
          <h2 className="text-base font-semibold text-neutral-800 mb-4">
            Sign in
          </h2>

          <form onSubmit={handleSubmit}>
            <label
              htmlFor="admin-password"
              className="block text-sm font-medium text-neutral-600 mb-1.5"
            >
              Admin password
            </label>
            <input
              id="admin-password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoFocus
              autoComplete="current-password"
              className="w-full px-3 py-2 border border-neutral-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-amber-300/50 focus:border-amber-400"
              placeholder="Enter password"
            />

            {error && (
              <p className="text-red-600 text-sm mt-2" role="alert">{error}</p>
            )}

            <button
              type="submit"
              disabled={loading || !password}
              className="w-full mt-4 px-4 py-2 bg-amber-500 text-white text-sm font-medium rounded-lg hover:bg-amber-600 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            >
              {loading ? "Signing in…" : "Sign in"}
            </button>
          </form>
        </div>
      </div>
    </div>
  );
}
