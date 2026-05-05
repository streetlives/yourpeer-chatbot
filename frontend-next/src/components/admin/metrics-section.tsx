// Copyright (c) 2024 Streetlives, Inc.
// Use of this source code is governed by an MIT-style license.

"use client";

import { useState } from "react";
import { ChevronDown } from "lucide-react";
import { METRIC_GRID_COLS } from "./metric-row-grid";

interface MetricsSectionProps {
  title: string;
  description?: string;
  children: React.ReactNode;
  defaultOpen?: boolean;
}

export function MetricsSection({
  title, description, children, defaultOpen = true,
}: MetricsSectionProps) {
  const [open, setOpen] = useState(defaultOpen);

  return (
    <div className="mb-9">
      <button
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-center justify-between text-xs font-bold uppercase tracking-widest text-neutral-400 mb-3 pb-2 border-b border-neutral-200 cursor-pointer hover:text-neutral-600 transition-colors"
        aria-expanded={open}
      >
        <span>{title}</span>
        <ChevronDown
          size={16}
          className={`transition-transform duration-200 ${open ? "" : "-rotate-90"}`}
        />
      </button>
      {open && (
        <>
          {description && (
            <p className="text-sm text-neutral-500 mb-4">{description}</p>
          )}
          {/* Header row — uses the same grid template as MetricRow so the
              header columns align with the data rows below. */}
          <div className={`grid ${METRIC_GRID_COLS} gap-3.5 pb-2 text-[0.7rem] uppercase tracking-wider text-neutral-400 font-semibold`}>
            <span>Metric</span>
            <span>Target</span>
            <span className="text-right">Current</span>
            <span className="text-right">Status</span>
            <span className="text-right">Phase</span>
          </div>
          {children}
        </>
      )}
    </div>
  );
}
