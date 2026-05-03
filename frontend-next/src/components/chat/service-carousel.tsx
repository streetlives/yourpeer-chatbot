// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

"use client";

import { useRef, useState, useCallback } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { ServiceCard, LocationCard } from "./service-card";
import type { ServiceResult } from "@/lib/chat/types";

interface ServiceCarouselProps {
  services: ServiceResult[];
}

/** Group services by org+address so co-located services render as one card. */
type LocationGroup = { key: string; services: ServiceResult[] };

function groupByLocation(services: ServiceResult[]): LocationGroup[] {
  const map = new Map<string, ServiceResult[]>();
  const order: string[] = [];
  for (const svc of services) {
    const key = `${(svc.organization || "").toLowerCase().trim()}||${(svc.address || "").toLowerCase().trim()}`;
    if (!map.has(key)) {
      map.set(key, []);
      order.push(key);
    }
    map.get(key)!.push(svc);
  }
  return order.map((key) => ({ key, services: map.get(key)! }));
}

export function ServiceCarousel({ services }: ServiceCarouselProps) {
  const trackRef = useRef<HTMLDivElement>(null);
  const [currentIndex, setCurrentIndex] = useState(0);

  const groups = groupByLocation(services);

  const scrollToIndex = useCallback(
    (index: number) => {
      if (!trackRef.current || !trackRef.current.firstElementChild) return;
      const cardWidth = (trackRef.current.firstElementChild as HTMLElement).offsetWidth;
      trackRef.current.scrollLeft = index * (cardWidth + 12);
    },
    [],
  );

  function handleScroll() {
    if (!trackRef.current || !trackRef.current.firstElementChild) return;
    const cardWidth = (trackRef.current.firstElementChild as HTMLElement).offsetWidth;
    const idx = Math.round(trackRef.current.scrollLeft / (cardWidth + 12));
    const clamped = Math.min(idx, groups.length - 1);
    if (clamped !== currentIndex) setCurrentIndex(clamped);
  }

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === "ArrowLeft" && currentIndex > 0) {
      e.preventDefault();
      scrollToIndex(currentIndex - 1);
    } else if (e.key === "ArrowRight" && currentIndex < groups.length - 1) {
      e.preventDefault();
      scrollToIndex(currentIndex + 1);
    }
  }

  return (
    <div
      role="region"
      aria-label="Service results"
      aria-roledescription="carousel"
      className="self-start w-full animate-in fade-in slide-in-from-bottom-1"
      onKeyDown={handleKeyDown}
    >
      {/* Header */}
      <div className="flex items-center justify-between px-1 pb-2">
        <span
          aria-live="polite"
          aria-atomic="true"
          className="text-xs text-neutral-400 font-medium dark:text-neutral-500"
        >
          {groups.length === services.length
            ? `Result ${currentIndex + 1} of ${groups.length}`
            : `Location ${currentIndex + 1} of ${groups.length} (${services.length} services)`}
        </span>
        <div className="flex gap-1" role="group" aria-label="Carousel navigation">
          <button
            type="button"
            disabled={currentIndex === 0}
            onClick={() => scrollToIndex(currentIndex - 1)}
            aria-label="Previous result"
            className="w-8 h-8 rounded-full border border-neutral-200 bg-white text-neutral-500 flex items-center justify-center transition hover:bg-neutral-50 hover:border-neutral-300 disabled:opacity-30 disabled:cursor-default dark:bg-neutral-900 dark:border-neutral-700 dark:text-neutral-400 dark:hover:bg-neutral-800 dark:hover:border-neutral-600"
          >
            <ChevronLeft size={16} />
          </button>
          <button
            type="button"
            disabled={currentIndex >= groups.length - 1}
            onClick={() => scrollToIndex(currentIndex + 1)}
            aria-label="Next result"
            className="w-8 h-8 rounded-full border border-neutral-200 bg-white text-neutral-500 flex items-center justify-center transition hover:bg-neutral-50 hover:border-neutral-300 disabled:opacity-30 disabled:cursor-default dark:bg-neutral-900 dark:border-neutral-700 dark:text-neutral-400 dark:hover:bg-neutral-800 dark:hover:border-neutral-600"
          >
            <ChevronRight size={16} />
          </button>
        </div>
      </div>

      {/* Track */}
      <div
        ref={trackRef}
        onScroll={handleScroll}
        tabIndex={0}
        role="list"
        aria-label="Service cards"
        className="flex gap-3 overflow-x-auto snap-x snap-mandatory scroll-smooth pb-2 scrollbar-hide focus:outline-none focus:ring-2 focus:ring-amber-300/30 rounded-2xl"
      >
        {groups.map((group, i) =>
          group.services.length === 1 ? (
            <ServiceCard
              key={group.key}
              service={group.services[0]}
              isActive={i === currentIndex}
              index={i}
              total={groups.length}
            />
          ) : (
            <LocationCard
              key={group.key}
              services={group.services}
              isActive={i === currentIndex}
              index={i}
              total={groups.length}
            />
          ),
        )}
      </div>

      {/* Dots */}
      {groups.length > 1 && (
        <div className="flex justify-center gap-1.5 pt-1" aria-hidden="true">
          {groups.map((_, i) => (
            <div
              key={i}
              className={`h-1.5 rounded-full transition-all ${
                i === currentIndex
                  ? "w-4 bg-neutral-900 dark:bg-neutral-100"
                  : "w-1.5 bg-neutral-300 dark:bg-neutral-700"
              }`}
            />
          ))}
        </div>
      )}
    </div>
  );
}
