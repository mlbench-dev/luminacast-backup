import { useState } from "react";
import { NavLink, useLocation, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import {
  Clapperboard,
  Radio,
  BarChart3,
  ShieldCheck,
  Settings,
  Settings2,
  UserCircle,
  Package,
  Music,
  ImagePlay,
  Send,
  Share2,
  Users,
  DollarSign,
  ClipboardCheck,
} from "lucide-react";
import { cn } from "@/lib/cn";
import { useAuthStore } from "@/stores/authStore";
import { UserRole, TeamRole } from "@/lib/types";

const mainNav = [
  { to: "/my-avatar", label: "My Avatar", icon: UserCircle },
  { to: "/products", label: "My Products", icon: Package },
  { to: "/cast-builder", label: "Cast Builder", icon: Clapperboard },
  // Publish is the daily workspace for scheduling, monitoring, and
  // replying — replaces the standalone Published + Comments items.
  { to: "/publish", label: "Publish", icon: Send },
  // Connected social accounts + seller/affiliate storefronts. Lives in the
  // main nav (not Settings) since it's a daily-use destination, not an
  // account setting.
  { to: "/channels", label: "My Channels", icon: Share2 },
  { to: "/settings/team", label: "Team", icon: Users },
  { to: "/go-live", label: "Go Live", icon: Radio },
  { to: "/music", label: "Music", icon: Music },
  { to: "/my-videos", label: "Media", icon: ImagePlay },
];

const analyticsNav = [
  { to: "/analytics", label: "Analytics", icon: BarChart3 },
];

// Publisher-only — hidden from Viewers/Creators. Cosmetic; the backend
// (routers/casts.py's review-queue/approve endpoints) enforces this
// independently regardless of what the sidebar shows.
const publisherNav = [
  { to: "/review", label: "Review Queue", icon: ClipboardCheck },
];

const adminItems = [
  { to: "/admin", label: "Admin Panel", icon: ShieldCheck },
  { to: "/control", label: "Control Panel", icon: Settings2 },
];

function NavItem({
  to,
  label,
  icon: Icon,
  isActive,
}: {
  to: string;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
  isActive: boolean;
}) {
  return (
    <NavLink
      to={to}
      data-testid={`nav-${label.toLowerCase().replace(/\s+/g, "-")}`}
      className={cn(
        "flex items-center gap-3 rounded-md px-3 py-2.5 text-sm font-medium transition-colors",
        isActive
          ? "bg-accent/10 text-accent"
          : "text-text-dim hover:bg-card hover:text-text"
      )}
    >
      <Icon className="h-4 w-4" />
      {label}
    </NavLink>
  );
}

export function Sidebar() {
  const navigate = useNavigate();
  const location = useLocation();
  const user = useAuthStore((s) => s.user);
  const hasTeamRole = useAuthStore((s) => s.hasTeamRole);
  const isAdmin = user?.role === UserRole.ADMIN;
  // The cost dashboard is keyed on operator email (matches the backend
  // allow-list in `utils/admin.py`) rather than the broad ADMIN role.
  const isCostOperator = user?.email === "3gorka72@gmail.com";
  const canReview = hasTeamRole(TeamRole.PUBLISHER);

  // The active nav item is whichever visible route is the longest matching
  // prefix of the current path — e.g. "/admin/costs" must win over "/admin"
  // so Cost Dashboard doesn't also light up Admin Panel.
  const visibleTos = [
    ...mainNav.map((i) => i.to),
    ...analyticsNav.map((i) => i.to),
    ...(canReview ? publisherNav.map((i) => i.to) : []),
    "/settings",
    ...(isCostOperator ? ["/admin/costs"] : []),
    ...(isAdmin ? adminItems.map((i) => i.to) : []),
  ];
  const activeTo = visibleTos
    .filter((to) => location.pathname === to || location.pathname.startsWith(to + "/"))
    .sort((a, b) => b.length - a.length)[0];

  return (
    <aside
      className="fixed left-0 top-0 z-40 flex h-screen w-60 flex-col border-r border-border bg-surface"
      data-testid="sidebar"
    >
      {/* Logo — click navigates to dashboard */}
      <div className="px-5 py-5">
        <button onClick={() => navigate("/dashboard")} className="block">
          <img src="/logo/luminacast-wordmark.svg" alt="Luminacast" height={32} className="h-8" />
        </button>
      </div>

      {/* Main Navigation */}
      <nav className="min-h-0 flex-1 space-y-1 overflow-y-auto px-3 py-2">
        {mainNav.map((item) => (
          <NavItem key={item.to} {...item} isActive={item.to === activeTo} />
        ))}

        {/* Analytics section */}
        <div className="my-3 border-t border-border" />
        <div className="px-3 py-1 text-[10px] font-semibold uppercase tracking-wider text-text-muted">
          Analytics
        </div>
        {analyticsNav.map((item) => (
          <NavItem key={item.to} {...item} isActive={item.to === activeTo} />
        ))}

        {canReview && (
          <>
            <div className="my-3 border-t border-border" />
            {publisherNav.map((item) => (
              <NavItem key={item.to} {...item} isActive={item.to === activeTo} />
            ))}
          </>
        )}

        {/* Settings — a single entry point; Account/Team/Billing/Pricing/
            My Channels all live inside the Settings page's own left rail
            (see pages/Settings.tsx) instead of being listed flat here. */}
        <div className="my-3 border-t border-border" />
        <NavItem to="/settings" label="Settings" icon={Settings} isActive={activeTo === "/settings"} />

        {isCostOperator && (
          <NavItem to="/admin/costs" label="Cost Dashboard" icon={DollarSign} isActive={activeTo === "/admin/costs"} />
        )}

        {isAdmin && (
          <>
            <div className="my-3 border-t border-border" />
            {adminItems.map((item) => (
              <NavItem key={item.to} {...item} isActive={item.to === activeTo} />
            ))}
          </>
        )}
      </nav>
    </aside>
  );
}
