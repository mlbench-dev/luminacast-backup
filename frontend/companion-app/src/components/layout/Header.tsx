import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ChevronsUpDown, Check, LogOut } from "lucide-react";
import { useAuthStore } from "@/stores/authStore";
import { confirmAction } from "@/lib/swal";
import { extractErrorMessage, teamsApi } from "@/lib/api"; // reuse whatever fetches my-workspaces in Sidebar.tsx
import { useNavigate } from "react-router-dom";
import { toast } from "@/hooks/useToast";
import { TeamRole } from "@/lib/types";

export function Header() {
    const user = useAuthStore((s) => s.user);
    const logout = useAuthStore((s) => s.logout);
    const [switcherOpen, setSwitcherOpen] = useState(false);
    const navigate = useNavigate();

    const switchWorkspace = useAuthStore((s) => s.switchWorkspace);
    const { data: workspaces = [] } = useQuery({
        queryKey: ["my-workspaces"],
        queryFn: () => teamsApi.myWorkspaces(),
    });


    const handleSwitch = async (ownerId: string) => {
        if (ownerId === user?.workspace?.owner_id) {
            setSwitcherOpen(false);
            return;
        }
        try {
            await switchWorkspace(ownerId);
            setSwitcherOpen(false);
            navigate("/dashboard");
        } catch (err: unknown) {
            toast({
                title: "Could not switch workspace",
                description: extractErrorMessage(err, "Something went wrong."),
                variant: "destructive",
            });
        }
    };

    const initial = (user?.email?.[0] || "?").toUpperCase();
    const roleLabel = user?.workspace?.is_own
        ? "Owner"
        : user?.workspace?.role === TeamRole.PUBLISHER ? "Publisher"
        : user?.workspace?.role === TeamRole.CREATOR ? "Creator"
        : user?.workspace?.role === TeamRole.VIEWER ? "Viewer"
        : "Member";

    return (
        <header className="flex items-center justify-end gap-3 border-b border-border bg-surface px-6 h-14 shrink-0">
            <div className="relative">
                <button
                    onClick={() => setSwitcherOpen((v) => !v)}
                    className="flex items-center gap-2.5 rounded-md py-1.5 pl-1.5 pr-2.5 text-sm hover:bg-card transition-colors"
                >
                    <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-accent/15 text-[11px] font-semibold text-accent">
                        {initial}
                    </span>
                    <span className="flex flex-col items-start leading-tight">
                        <span className="text-text truncate max-w-[180px]">
                            {user?.workspace?.is_own ? "My workspace" : user?.workspace?.owner_label || user?.email}
                        </span>
                        <span className="text-[11px] text-text-muted truncate max-w-[180px]">
                            {roleLabel}
                        </span>
                    </span>
                    <ChevronsUpDown className="h-3.5 w-3.5 shrink-0 text-text-muted" />
                </button>

                {switcherOpen && (
                    <>
                        <div className="fixed inset-0 z-40" onClick={() => setSwitcherOpen(false)} />
                        <div className="absolute right-0 top-full z-50 mt-1.5 w-full min-w-[220px] rounded-md border border-border bg-card shadow-xl py-1">
                            {workspaces.map((w) => {
                                const active = w.owner_id === (user?.workspace?.owner_id ?? user?.id);
                                return (
                                    <button
                                        key={w.owner_id}
                                        onClick={() => handleSwitch(w.owner_id)}
                                        className="flex w-full items-center justify-between gap-2 px-3 py-2 text-left text-sm text-text-dim hover:bg-surface hover:text-text transition-colors"
                                    >
                                        <span className="truncate">
                                            {w.is_own ? "My workspace" : w.owner_label}
                                            {!w.is_own && (
                                                <span className="ml-1.5 text-[10px] uppercase text-text-muted">{w.role}</span>
                                            )}
                                        </span>
                                        {active && <Check className="h-3.5 w-3.5 text-accent shrink-0" />}
                                    </button>
                                );
                            })}
                        </div>
                    </>
                )}
            </div>

            <div className="h-6 w-px bg-border" />

            <button
                onClick={async () => {
                    if (await confirmAction({
                        title: "Sign out?",
                        text: "You'll need to sign in again to access your dashboard.",
                        confirmButtonText: "Sign Out",
                    })) logout();
                }}
                className="flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm text-text-muted hover:bg-card hover:text-danger transition-colors"
            >
                <LogOut className="h-4 w-4" />
                Sign Out
            </button>
        </header>
    );
}