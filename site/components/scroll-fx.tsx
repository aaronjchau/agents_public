"use client";

import { usePathname } from "next/navigation";
import { useEffect } from "react";

/**
 * Page-wide scroll effects. One-shot reveals gain .in when scrolled into view;
 * looping animations run only while their .anim-scope is on screen and only for
 * LIVE_MS per entry, so they rest instead of re-compositing every vsync. Hidden
 * initial states apply only under html.js, so a no-JS visit renders everything.
 * One root-layout instance; observers are rebuilt per route and torn down on
 * cleanup, so nothing observes stale nodes.
 */

// loop budget per viewport entry (two 14s sweep cycles, so the playhead
// rests near the left edge); re-crossing the visibility threshold re-arms it
const LIVE_MS = 28000;

export function ScrollFx() {
  const pathname = usePathname();

  useEffect(() => {
    const revealIO = new IntersectionObserver(
      (entries) => {
        for (const e of entries) {
          if (e.isIntersecting) {
            e.target.classList.add("in");
            revealIO.unobserve(e.target);
          }
        }
      },
      { rootMargin: "0px 0px 18% 0px" },
    );
    document.querySelectorAll(".reveal, .figure, .panel").forEach((el) => {
      revealIO.observe(el);
      // already in view on mount: reveal now instead of waiting on the observer
      const r = el.getBoundingClientRect();
      if (r.top < window.innerHeight && r.bottom > 0) {
        el.classList.add("in");
        revealIO.unobserve(el);
      }
    });
    // one-shot fallback: anything above the fold still hidden gets revealed
    const revealAboveFold = () => {
      document
        .querySelectorAll(".reveal:not(.in), .figure:not(.in), .panel:not(.in)")
        .forEach((el) => {
          if (el.getBoundingClientRect().top < window.innerHeight) {
            el.classList.add("in");
            revealIO.unobserve(el);
          }
        });
    };
    const revealTimer = window.setTimeout(revealAboveFold, 2500);
    window.addEventListener("load", revealAboveFold, { once: true });

    const liveTimers = new Map<Element, number>();
    const liveIO = new IntersectionObserver(
      (entries) => {
        for (const e of entries) {
          window.clearTimeout(liveTimers.get(e.target));
          liveTimers.delete(e.target);
          e.target.classList.toggle("live", e.isIntersecting);
          if (e.isIntersecting) {
            const timer = window.setTimeout(() => {
              liveTimers.delete(e.target);
              e.target.classList.remove("live");
            }, LIVE_MS);
            liveTimers.set(e.target, timer);
          }
        }
      },
      { threshold: 0.4 },
    );
    document.querySelectorAll(".anim-scope").forEach((el) => liveIO.observe(el));

    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

    // packet overlays: scale each 940-unit layer to its rendered svg width
    const pkLayers = Array.from(document.querySelectorAll<HTMLElement>(".fig-canvas"));
    const sizePk = (canvas: Element) => {
      // the svg can overflow the canvas div inside the .schematic scroller,
      // so the rendered svg width is the source of truth for the scale
      const svg = canvas.querySelector("svg");
      const layer = canvas.querySelector<HTMLElement>(".pk-layer");
      if (svg && layer) layer.style.transform = `scale(${svg.getBoundingClientRect().width / 940})`;
    };
    const pkRO = new ResizeObserver(() => pkLayers.forEach(sizePk));
    pkLayers.forEach((c) => {
      sizePk(c);
      const svg = c.querySelector("svg");
      if (svg) pkRO.observe(svg);
    });

    // playhead sweep: the keyframes need a concrete px distance, since a
    // percentage resolves against the 1.5px line itself, and var() inside
    // keyframes forces main-thread recalc; so the measured strip width is
    // written into a style tag that overrides the stylesheet fallback
    let sweepStyle: HTMLStyleElement | null = null;
    let stripRO: ResizeObserver | null = null;
    const day = document.querySelector<HTMLElement>(".day");
    const phLine = day ? day.querySelector<HTMLElement>(".ph-line") : null;
    const strip = phLine ? phLine.parentElement : null;
    if (strip) {
      sweepStyle = document.createElement("style");
      document.head.append(sweepStyle);
      stripRO = new ResizeObserver(() => {
        const w = strip.getBoundingClientRect().width;
        sweepStyle!.textContent = `@keyframes sweep { to { transform: translateX(${w}px) } }`;
      });
      stripRO.observe(strip);
    }

    // tick/marker flashes: a 10Hz interval reads the playhead sweep's own
    // animation clock and toggles .lit. Chrome would not composite the 36
    // per-element flash animations, so one driver replaces them; the sweep
    // itself is a compositor animation on a promoted layer.
    let flashTimer = 0;
    if (day && phLine && !reduceMotion.matches) {
      const SWEEP_MS = 14000;
      const firing = Array.from(day.querySelectorAll<HTMLElement>(".tick-ev, .marker")).map(
        (el) => ({
          el,
          at: parseFloat(getComputedStyle(el).getPropertyValue("--pd")) * 1000 || 0,
          ms: el.classList.contains("marker") ? 1100 : 420,
        }),
      );
      let resting = false;
      flashTimer = window.setInterval(() => {
        if (document.hidden) return;
        const sweep = phLine.getAnimations()[0];
        if (!day.classList.contains("live") || !sweep) {
          // the sweep can rest mid-flash; clear stragglers once
          if (!resting) for (const { el } of firing) el.classList.remove("lit");
          resting = true;
          return;
        }
        resting = false;
        const t = Number(sweep.currentTime ?? 0) % SWEEP_MS;
        for (const { el, at, ms } of firing) {
          el.classList.toggle("lit", t >= at && t < at + ms);
        }
      }, 100);
    }

    // pause looping animations during active scroll: each composites the whole
    // window every vsync, starving the scroll on Chrome. html.scrolling clears
    // ~150ms after the last scroll event, covering trackpad momentum
    const root = document.documentElement;
    let scrollIdle = 0;
    const onScroll = () => {
      root.classList.add("scrolling");
      window.clearTimeout(scrollIdle);
      scrollIdle = window.setTimeout(() => root.classList.remove("scrolling"), 150);
    };
    window.addEventListener("scroll", onScroll, { passive: true });

    return () => {
      revealIO.disconnect();
      liveIO.disconnect();
      liveTimers.forEach((t) => window.clearTimeout(t));
      pkRO.disconnect();
      stripRO?.disconnect();
      sweepStyle?.remove();
      if (flashTimer) window.clearInterval(flashTimer);
      window.clearTimeout(revealTimer);
      window.removeEventListener("load", revealAboveFold);
      window.removeEventListener("scroll", onScroll);
      window.clearTimeout(scrollIdle);
      root.classList.remove("scrolling");
    };
  }, [pathname]);

  return null;
}
