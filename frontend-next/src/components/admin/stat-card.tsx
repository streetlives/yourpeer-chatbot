// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

interface StatCardProps {
  label: string;
  value: string | number;
  note?: string | null;
  colorClass?: string;
}

export function StatCard({ label, value, note, colorClass }: StatCardProps) {
  return (
    <div className="bg-white dark:bg-neutral-900 border border-neutral-200 dark:border-neutral-800 rounded-lg p-4">
      <div className="text-sm font-semibold text-neutral-500 dark:text-neutral-400 mb-1.5">
        {label}
        {note && (
          <span className="ml-1.5 text-xs font-normal text-neutral-400 dark:text-neutral-500">
            {note}
          </span>
        )}
      </div>
      <div className={`text-2xl font-bold tracking-tight ${colorClass || "text-neutral-900 dark:text-neutral-100"}`}>
        {value ?? "—"}
      </div>
    </div>
  );
}
