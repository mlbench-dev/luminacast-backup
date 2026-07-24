import { NavLink, useLocation, useNavigate } from "react-router-dom";
import {
  Clapperboard,
  Radio,
  BarChart3,
  ShieldCheck,
  Settings2,
  LogOut,
  UserCircle,
  Package,
  Users,
  CreditCard,
  Music,
  ImagePlay,
  Send,
  DollarSign,
} from "lucide-react";
import { cn } from "@/lib/cn";
import { confirmAction } from "@/lib/swal";
import { useAuthStore } from "@/stores/authStore";
import { UserRole } from "@/lib/types";

const mainNav = [
  { to: "/my-avatar", label: "My Avatar", icon: UserCircle },
  { to: "/products", label: "My Products", icon: Package },
  { to: "/cast-builder", label: "Cast Builder", icon: Clapperboard },
  // Publish is the daily workspace for scheduling, monitoring, and
  // replying — replaces the standalone Published + Comments items.
  { to: "/publish", label: "Publish", icon: Send },
  { to: "/go-live", label: "Go Live", icon: Radio },
  { to: "/music", label: "Music", icon: Music },
  { to: "/my-videos", label: "Media", icon: ImagePlay },
];

const analyticsNav = [
  { to: "/analytics", label: "Analytics", icon: BarChart3 },
];

const settingsNav = [
  // Unified My Channels (social accounts + seller/affiliate storefronts).
  // Per spec CHANGE 2 — Seller Channels item removed; the unified page
  // surfaces every connected platform with a "Seller / Affiliate" badge.
  { to: "/channels", label: "My Channels", icon: Send },
  { to: "/settings/team", label: "Team", icon: Users },
  { to: "/settings/billing", label: "Billing", icon: CreditCard },
];

const adminItems = [
  { to: "/admin", label: "Admin Panel", icon: ShieldCheck },
  { to: "/control", label: "Control Panel", icon: Settings2 },
];

function NavItem({ to, label, icon: Icon }: { to: string; label: string; icon: React.ComponentType<{ className?: string }> }) {
  const location = useLocation();
  // Avatar sub-pages (/my-avatar/clone, etc.) should highlight "My Avatar"
  const isActive = to === "/my-avatar"
    ? location.pathname === "/my-avatar" || location.pathname.startsWith("/my-avatar/")
    : location.pathname === to || location.pathname.startsWith(to + "/");

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
  const user = useAuthStore((s) => s.user);
  const logout = useAuthStore((s) => s.logout);
  const isAdmin = user?.role === UserRole.ADMIN;
  // The cost dashboard is keyed on operator email (matches the backend
  // allow-list in `utils/admin.py`) rather than the broad ADMIN role.
  const isCostOperator = user?.email === "3gorka72@gmail.com";

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
          <NavItem key={item.to} {...item} />
        ))}

        {/* Analytics section */}
        <div className="my-3 border-t border-border" />
        <div className="px-3 py-1 text-[10px] font-semibold uppercase tracking-wider text-text-muted">
          Analytics
        </div>
        {analyticsNav.map((item) => (
          <NavItem key={item.to} {...item} />
        ))}

        {/* Settings section */}
        <div className="my-3 border-t border-border" />
        <div className="px-3 py-1 text-[10px] font-semibold uppercase tracking-wider text-text-muted">
          Settings
        </div>
        {settingsNav.map((item) => (
          <NavItem key={item.to} {...item} />
        ))}

        {isCostOperator && (
          <NavItem to="/admin/costs" label="Cost Dashboard" icon={DollarSign} />
        )}

        {isAdmin && (
          <>
            <div className="my-3 border-t border-border" />
            {adminItems.map((item) => (
              <NavItem key={item.to} {...item} />
            ))}
          </>
        )}
      </nav>

      {/* User section */}
      <div className="border-t border-border p-4">
        <div className="mb-3 truncate text-sm text-text-dim">
          {user?.email}
        </div>
        <button
          onClick={async () => {
            if (await confirmAction({
              title: "Sign out?",
              text: "You'll need to sign in again to access your dashboard.",
              confirmButtonText: "Sign Out",
            })) logout();
          }}
          data-testid="logout-button"
          className="flex w-full items-center gap-2 rounded-md px-3 py-2 text-sm text-text-muted transition-colors hover:bg-card hover:text-danger"
        >
          <LogOut className="h-4 w-4" />
          Sign Out
        </button>
      </div>
    </aside>
  );
}
