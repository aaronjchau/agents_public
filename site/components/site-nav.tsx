"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

import { REPO_URL, ROUTES } from "@/lib/site";

const TABS = ROUTES;

export function SiteNav() {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);

  // Close the mobile menu when navigation changes the route. Adjusting
  // state during render avoids a cascading-render route-change effect.
  const [lastPath, setLastPath] = useState(pathname);
  if (pathname !== lastPath) {
    setLastPath(pathname);
    setOpen(false);
  }

  // Close on Escape while the menu is open.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  return (
    <nav className={`site-nav${open ? " menu-open" : ""}`}>
      <div className="wrap nav-row">
        <Link href="/" className="logo">
          agents<b>*</b>
        </Link>
        <div className="tabs">
          {TABS.map((tab) => (
            <Link
              key={tab.href}
              href={tab.href}
              className="tab"
              aria-current={pathname === tab.href ? "page" : undefined}
            >
              {tab.label}
            </Link>
          ))}
        </div>
        <a className="gh" href={REPO_URL} target="_blank" rel="noopener noreferrer">
          source ↗
        </a>
        <button
          type="button"
          className="menu-btn"
          aria-expanded={open}
          aria-controls="mobile-menu"
          aria-label="Menu"
          onClick={() => setOpen((v) => !v)}
        >
          <i />
          <i />
        </button>
      </div>
      <div id="mobile-menu" className="menu-panel">
        {TABS.map((tab, i) => (
          <Link
            key={tab.href}
            href={tab.href}
            className="menu-link"
            style={{ "--i": i }}
            aria-current={pathname === tab.href ? "page" : undefined}
          >
            {tab.label}
          </Link>
        ))}
      </div>
    </nav>
  );
}
