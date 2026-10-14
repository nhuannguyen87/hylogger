"use client";

// Top bar: brand, the two views, and live counts from the API.

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { getStats, clearDataCache } from "@/lib/api";

const VIEWS = [
  { href: "/", label: "Explore" },
  { href: "/viewer3d", label: "3D" }, // also where two holes are compared
];

export default function Nav() {
  const pathname = usePathname();
  const [stats, setStats] = useState(null);
  const [refreshing, setRefreshing] = useState(false);
  const [refreshError, setRefreshError] = useState("");

  const refreshData = async () => {
    setRefreshing(true);
    setRefreshError("");
    try {
      await clearDataCache();
      window.location.reload();
    } catch (error) {
      setRefreshError(error.message);
      setRefreshing(false);
    }
  };

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

      <button
        type="button"
        onClick={refreshData}
        disabled={refreshing}
        title="Saved data lasts up to 1 hour. Clear it and fetch the latest data."
        style={{ marginLeft: 12, padding: "5px 10px", color: "var(--text)", background: "var(--panel)", border: "1px solid var(--line)", borderRadius: 5, cursor: refreshing ? "wait" : "pointer", whiteSpace: "nowrap" }}
      >
        {refreshing ? "Refreshing…" : "Refresh data"}
      </button>
      {refreshError && <span role="alert" style={{ marginLeft: 8, fontSize: 11 }}>{refreshError}</span>}

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
