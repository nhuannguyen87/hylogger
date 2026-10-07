"use client";

// Top bar: brand, the three views, and live counts from the API.

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { getStats } from "@/lib/api";

const VIEWS = [
  { href: "/", label: "Explore" },
  { href: "/compare", label: "Compare" },
  { href: "/viewer3d", label: "3D" },
];

export default function Nav() {
  const pathname = usePathname();
  const [stats, setStats] = useState(null);

  useEffect(() => {
    getStats().then(setStats).catch(() => setStats(null));
  }, []);

  return (
    <header className="topbar">
      <div className="brand">
        GSWA <span>HyLogger</span> Explorer
      </div>

      <nav className="nav">
        {VIEWS.map((view) => (
          <Link
            key={view.href}
            href={view.href}
            className={pathname === view.href ? "active" : ""}
          >
            {view.label}
          </Link>
        ))}
      </nav>

      {stats && (
        <div className="topbar-stats">
          <span>holes <b>{stats.holes.toLocaleString()}</b></span>
          <span>intervals <b>{stats.measurements.toLocaleString()}</b></span>
          <span>flagged <b>{stats.anomalies.toLocaleString()}</b></span>
        </div>
      )}
    </header>
  );
}
