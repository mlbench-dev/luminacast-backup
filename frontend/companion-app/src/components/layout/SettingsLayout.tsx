import { useNavigate, useLocation } from "react-router-dom";
import { CreditCard, KeyRound, LogOut, Tag, Trash2, User as UserIcon } from "lucide-react";
import { Card } from "@/components/ui/card";
import { confirmAction } from "@/lib/swal";
import { useAuthStore } from "@/stores/authStore";
import { cn } from "@/lib/cn";

function initials(label: string): string {
  return label.trim().slice(0, 2).toUpperCase() || "?";
}

function ProfileAvatar({ url, label }: { url?: string | null; label: string }) {
  if (url) {
    return <img src={url} alt="Profile" className="h-14 w-14 rounded-full object-cover border border-border" />;
  }
  return (
    <div className="h-14 w-14 rounded-full bg-accent/10 text-accent flex items-center justify-center text-base font-semibold border border-border">
      {initials(label)}
    </div>
  );
}

const NAV_ITEMS = [
  { to: "/settings", label: "Edit Profile", icon: UserIcon },
  { to: "/settings/password", label: "Change Password", icon: KeyRound },
  { to: "/settings/billing", label: "Billing", icon: CreditCard },
  { to: "/settings/pricing", label: "Plans & Pricing", icon: Tag },
  { to: "/settings/delete-account", label: "Delete Account", icon: Trash2, danger: true },
];

export function SettingsLayout({ children }: { children: React.ReactNode }) {
  const user = useAuthStore((s) => s.user);
  const logout = useAuthStore((s) => s.logout);
  const navigate = useNavigate();
  const location = useLocation();

  const handleLogout = async () => {
    if (
      await confirmAction({
        title: "Sign out?",
        text: "You'll need to sign in again.",
        confirmButtonText: "Sign Out",
      })
    ) {
      logout();
      navigate("/login");
    }
  };

  return (
    <div className="space-y-6" data-testid="settings-page">
      <h1 className="text-2xl font-bold text-text">Settings</h1>

      <div className="mx-auto w-full max-w-5xl">
        <Card className="p-0 overflow-hidden">
          <div className="flex flex-col md:flex-row min-h-[560px]">
            {/* Left rail */}
            <div className="w-full md:w-72 shrink-0 border-b md:border-b-0 md:border-r border-border p-6 flex flex-col">
              <div className="flex flex-col items-center text-center pb-5 border-b border-border">
                <ProfileAvatar url={user?.avatar_url} label={user?.display_name || user?.email || "?"} />
                <div className="mt-3 font-semibold text-text">{user?.display_name || "Add your name"}</div>
                <div className="text-xs text-text-muted">{user?.email}</div>
              </div>

              <nav className="mt-4 flex-1 space-y-1">
                {NAV_ITEMS.map((item) => {
                  const isActive = location.pathname === item.to;
                  const Icon = item.icon;
                  return (
                    <button
                      key={item.to}
                      type="button"
                      onClick={() => navigate(item.to)}
                      data-testid={`settings-nav-${item.label.toLowerCase().replace(/\s+/g, "-")}`}
                      className={cn(
                        "w-full flex items-center gap-3 rounded-md px-3 py-2.5 text-sm font-medium transition-colors text-left",
                        isActive && !item.danger && "bg-accent/10 text-accent",
                        !isActive && !item.danger && "text-text-dim hover:bg-card hover:text-text",
                        item.danger && "text-danger hover:bg-danger/10"
                      )}
                    >
                      <Icon className="h-4 w-4 shrink-0" />
                      <span className="flex-1">{item.label}</span>
                    </button>
                  );
                })}
              </nav>

              <button
                type="button"
                onClick={handleLogout}
                data-testid="settings-nav-logout"
                className="mt-4 flex w-full items-center gap-3 rounded-md px-3 py-2.5 text-sm font-medium text-text-muted hover:bg-card hover:text-text transition-colors"
              >
                <LogOut className="h-4 w-4" />
                Logout
              </button>
            </div>

            {/* Right panel — @container establishes a containment context so
                embedded pages (Billing, Pricing) can size their grids off
                this panel's actual width via @sm:/@lg: variants instead of
                the full viewport, which made them look crowded next
                to the left rail. */}
            <div className="flex-1 p-8 min-w-0 @container">{children}</div>
          </div>
        </Card>
      </div>
    </div>
  );
}
