// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

"use client";

import { useRef, useState, useCallback, useEffect } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { ServiceCard, LocationCard, useReviewTruncate } from "./service-card";
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
  // Track scroll boundaries directly. With snap-mandatory + many
  // cards, the last "snap point" (groups.length-1 * cardWidth) sits
  // beyond maxScrollLeft and is unreachable, which broke the
  // currentIndex-based button enable/disable logic. Detecting end
  // by comparing scrollLeft to maxScrollLeft is the reliable signal.
  const [atStart, setAtStart] = useState(true);
  const [atEnd, setAtEnd] = useState(false);

  // Computed once here and passed to each card so the hook's
  // window.innerWidth read + effect firing happens once per carousel
  // rather than once per card.
  const reviewTruncate = useReviewTruncate();

  const groups = groupByLocation(services);

  /** Scroll by exactly one card width in the given direction. Uses
   * scrollBy + smooth instead of assigning scrollLeft so it composes
   * correctly with snap-mandatory: the browser handles the snap
   * after the scroll lands, rather than fighting our assignment
   * mid-animation. */
  const scrollByCards = useCallback((direction: -1 | 1) => {
    if (!trackRef.current || !trackRef.current.firstElementChild) return;
    const cardWidth = (trackRef.current.firstElementChild as HTMLElement).offsetWidth;
    trackRef.current.scrollBy({
      left: direction * (cardWidth + 12),
      behavior: "smooth",
    });
  }, []);

  function handleScroll() {
    if (!trackRef.current || !trackRef.current.firstElementChild) return;
    const t = trackRef.current;
    const cardWidth = (t.firstElementChild as HTMLElement).offsetWidth;
    const maxScroll = t.scrollWidth - t.clientWidth;

    // Boundary state — used directly by button disabled logic.
    // 1px of slack on each side absorbs sub-pixel rounding from
    // smooth-scroll endings.
    setAtStart(t.scrollLeft <= 1);
    setAtEnd(maxScroll <= 0 || t.scrollLeft >= maxScroll - 1);

    // For dots indicator + active card: the leftmost visible card.
    // When at end, snap currentIndex to the last group so the dot
    // and "Location N of M" label match what the user actually sees.
    let idx: number;
    if (maxScroll > 0 && t.scrollLeft >= maxScroll - 4) {
      idx = groups.length - 1;
    } else {
      idx = Math.round(t.scrollLeft / (cardWidth + 12));
    }
    const clamped = Math.max(0, Math.min(idx, groups.length - 1));
    if (clamped !== currentIndex) setCurrentIndex(clamped);
  }

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === "ArrowLeft" && !atStart) {
      e.preventDefault();
      scrollByCards(-1);
    } else if (e.key === "ArrowRight" && !atEnd) {
      e.preventDefault();
      scrollByCards(1);
    }
  }

  // Compute initial boundary state on mount. handleScroll only fires
  // on scroll events, so without this the default `atEnd: false`
  // sticks when all cards fit in the viewport (maxScroll = 0, no
  // scroll ever happens). The Next button would render enabled and
  // click as a no-op. This effect closes that gap.
  useEffect(() => {
    if (!trackRef.current) return;
    const t = trackRef.current;
    const maxScroll = t.scrollWidth - t.clientWidth;
    setAtStart(t.scrollLeft <= 1);
    setAtEnd(maxScroll <= 0 || t.scrollLeft >= maxScroll - 1);
    // Empty deps: services prop is stable per carousel instance
    // (each bot message renders its own ServiceCarousel), so a
    // run-once-on-mount effect is what's wanted here.
  }, []);

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
        {/* Header.
            When no grouping happened (one card per service) the count
            is simple: "Result N of M". When services got combined into
            location cards (groups.length < services.length) the older
            text — "Location N of M (S services)" — was ambiguous: does
            "S services" describe THIS location or the whole result
            set? We now spell it out: "N of M locations · S services
            total", and the active card's own header still surfaces
            "X services here" so the user gets both numbers in
            their natural place. */}
        <span
          aria-live="polite"
          aria-atomic="true"
          className="text-xs text-neutral-400 font-medium dark:text-neutral-500"
        >
          {groups.length === services.length
            ? `Result ${currentIndex + 1} of ${groups.length}`
            : `${currentIndex + 1} of ${groups.length} locations · ${services.length} services total`}
        </span>
        <div className="flex gap-1" role="group" aria-label="Carousel navigation">
          <button
            type="button"
            disabled={atStart}
            onClick={() => scrollByCards(-1)}
            aria-label="Previous result"
            className="w-11 h-11 sm:w-8 sm:h-8 rounded-full border border-neutral-200 bg-white text-neutral-500 flex items-center justify-center transition hover:bg-neutral-50 hover:border-neutral-300 disabled:opacity-30 disabled:cursor-default dark:bg-neutral-900 dark:border-neutral-700 dark:text-neutral-400 dark:hover:bg-neutral-800 dark:hover:border-neutral-600"
          >
            <ChevronLeft size={16} />
          </button>
          <button
            type="button"
            disabled={atEnd}
            onClick={() => scrollByCards(1)}
            aria-label="Next result"
            className="w-11 h-11 sm:w-8 sm:h-8 rounded-full border border-neutral-200 bg-white text-neutral-500 flex items-center justify-center transition hover:bg-neutral-50 hover:border-neutral-300 disabled:opacity-30 disabled:cursor-default dark:bg-neutral-900 dark:border-neutral-700 dark:text-neutral-400 dark:hover:bg-neutral-800 dark:hover:border-neutral-600"
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
              reviewTruncate={reviewTruncate}
            />
          ) : (
            <LocationCard
              key={group.key}
              services={group.services}
              isActive={i === currentIndex}
              index={i}
              total={groups.length}
              reviewTruncate={reviewTruncate}
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
