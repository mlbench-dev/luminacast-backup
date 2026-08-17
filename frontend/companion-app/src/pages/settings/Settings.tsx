import { useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Camera, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { SettingsLayout } from "@/components/layout/SettingsLayout";
import { toast } from "@/hooks/useToast";
import { confirmAction } from "@/lib/swal";
import { extractErrorMessage, authApi, userApi } from "@/lib/api";
import { useAuthStore } from "@/stores/authStore";
import { UserRole } from "@/lib/types";
import { cn } from "@/lib/cn";

function initials(label: string): string {
  return label.trim().slice(0, 2).toUpperCase() || "?";
}

function LargeAvatar({ url, label }: { url?: string | null; label: string }) {
  if (url) {
    return <img src={url} alt="Profile" className="h-24 w-24 rounded-full object-cover border border-border" />;
  }
  return (
    <div className="h-24 w-24 rounded-full bg-accent/10 text-accent flex items-center justify-center text-2xl font-semibold border border-border">
      {initials(label)}
    </div>
  );
}

function useAfterProfileChange() {
  const fetchUser = useAuthStore((s) => s.fetchUser);
  const queryClient = useQueryClient();
  return async () => {
    await fetchUser();
    queryClient.invalidateQueries({ queryKey: ["billing-dashboard"] });
  };
}

export function SettingsProfilePage() {
  const user = useAuthStore((s) => s.user);
  const afterChange = useAfterProfileChange();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [displayName, setDisplayName] = useState(user?.display_name || "");

  const avatarMutation = useMutation({
    mutationFn: (file: File) => userApi.uploadAvatar(file),
    onSuccess: async () => {
      toast({ title: "Profile picture updated" });
      await afterChange();
    },
    onError: (err: unknown) =>
      toast({
        title: "Could not upload image",
        description: extractErrorMessage(err, "Please try a different file."),
        variant: "destructive",
      }),
  });

  const nameMutation = useMutation({
    mutationFn: () => userApi.updateProfile(displayName.trim() || null),
    onSuccess: async () => {
      toast({ title: "Profile updated" });
      await afterChange();
    },
    onError: (err: unknown) =>
      toast({
        title: "Could not update profile",
        description: extractErrorMessage(err, "Please try again."),
        variant: "destructive",
      }),
  });

  const handleAvatarPick = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) avatarMutation.mutate(file);
    e.target.value = "";
  };

  const nameUnchanged = displayName.trim() === (user?.display_name || "");

  return (
    <SettingsLayout>
      <div className="max-w-md space-y-6">
        <h2 className="text-lg font-semibold text-text">Edit Personal Information</h2>

        <div className="relative w-fit">
          <LargeAvatar url={user?.avatar_url} label={user?.display_name || user?.email || "?"} />
          <button
            type="button"
            onClick={() => fileInputRef.current?.click()}
            disabled={avatarMutation.isPending}
            className="absolute -bottom-1 -right-1 flex h-8 w-8 items-center justify-center rounded-full bg-accent text-white hover:bg-accent-hover disabled:opacity-50"
            aria-label="Change profile picture"
          >
            {avatarMutation.isPending ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <Camera className="h-3.5 w-3.5" />
            )}
          </button>
          <input
            ref={fileInputRef}
            type="file"
            accept="image/jpeg,image/png,image/webp"
            className="hidden"
            onChange={handleAvatarPick}
          />
        </div>

        <div className="space-y-2">
          <label className="text-sm font-medium text-text">Email Address</label>
          <Input value={user?.email || ""} disabled className="bg-surface" />
        </div>

        <div className="space-y-2">
          <label className="text-sm font-medium text-text">Full Name</label>
          <Input
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
            placeholder="Enter Your Full Name"
            maxLength={100}
          />
        </div>

        <Button
          className="w-full"
          onClick={() => nameMutation.mutate()}
          disabled={nameMutation.isPending || nameUnchanged}
        >
          {nameMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : "Save Changes"}
        </Button>
      </div>
    </SettingsLayout>
  );
}

export function SettingsPasswordPage() {
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");

  const passwordMutation = useMutation({
    mutationFn: () =>
      authApi.changePassword({ current_password: currentPassword, new_password: newPassword }),
    onSuccess: () => {
      toast({ title: "Password updated" });
      setCurrentPassword("");
      setNewPassword("");
      setConfirmPassword("");
    },
    onError: (err: unknown) =>
      toast({
        title: "Could not change password",
        description: extractErrorMessage(err, "Please check your current password and try again."),
        variant: "destructive",
      }),
  });

  const passwordsMismatch = newPassword.length > 0 && newPassword !== confirmPassword;
  const canSubmit =
    currentPassword.length > 0 && newPassword.length >= 8 && newPassword === confirmPassword && !passwordMutation.isPending;

  return (
    <SettingsLayout>
      <div className="max-w-md space-y-5">
        <h2 className="text-lg font-semibold text-text">Change Password</h2>
        <p className="text-sm text-text-dim">Change the password you use to sign in.</p>

        <div className="space-y-2">
          <label className="text-sm font-medium text-text">Current password</label>
          <Input
            type="password"
            value={currentPassword}
            onChange={(e) => setCurrentPassword(e.target.value)}
            autoComplete="current-password"
          />
        </div>
        <div className="space-y-2">
          <label className="text-sm font-medium text-text">New password</label>
          <Input
            type="password"
            value={newPassword}
            onChange={(e) => setNewPassword(e.target.value)}
            autoComplete="new-password"
          />
          <p className="text-xs text-text-muted">
            At least 8 characters, with an uppercase letter, a lowercase letter, and a special character.
          </p>
        </div>
        <div className="space-y-2">
          <label className="text-sm font-medium text-text">Confirm new password</label>
          <Input
            type="password"
            value={confirmPassword}
            onChange={(e) => setConfirmPassword(e.target.value)}
            autoComplete="new-password"
          />
          {passwordsMismatch && <p className="text-xs text-danger">Passwords don't match.</p>}
        </div>
        <Button onClick={() => passwordMutation.mutate()} disabled={!canSubmit}>
          {passwordMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : "Update password"}
        </Button>
      </div>
    </SettingsLayout>
  );
}

export function SettingsDeleteAccountPage() {
  const user = useAuthStore((s) => s.user);
  const logout = useAuthStore((s) => s.logout);
  const navigate = useNavigate();
  const [currentPassword, setCurrentPassword] = useState("");
  const isAdmin = user?.role === UserRole.ADMIN;

  const deleteMutation = useMutation({
    mutationFn: () => authApi.deleteAccount({ current_password: currentPassword }),
    onSuccess: () => {
      toast({ title: "Your account has been deleted." });
      logout();
      navigate("/login");
    },
    onError: (err: unknown) =>
      toast({
        title: "Could not delete account",
        description: extractErrorMessage(err, "Please check your password and try again."),
        variant: "destructive",
      }),
  });

  const handleDeleteAccount = async () => {
    const confirmed = await confirmAction({
      title: "Delete your account?",
      text: "This deactivates your account and signs you out everywhere. This cannot be undone.",
      icon: "warning",
      confirmButtonText: "Delete my account",
    });
    if (confirmed) deleteMutation.mutate();
  };

  if (isAdmin) {
    return (
      <SettingsLayout>
        <div className="max-w-md space-y-4">
          <h2 className="text-lg font-semibold text-text">Delete Account</h2>
          <p className="text-sm text-text-dim">
            Admin accounts can't be deleted from Settings. This platform has a single admin
            account, and removing it would lock everyone out of the Admin and Control panels.
          </p>
        </div>
      </SettingsLayout>
    );
  }

  return (
    <SettingsLayout>
      <div className={cn("max-w-md space-y-4")}>
        <h2 className="text-lg font-semibold text-danger">Delete Account</h2>
        <p className="text-sm text-text-dim">
          Deactivates your account and signs you out everywhere. This cannot be undone.
        </p>
        <div className="space-y-2">
          <label className="text-sm font-medium text-text">Confirm your password</label>
          <Input
            type="password"
            value={currentPassword}
            onChange={(e) => setCurrentPassword(e.target.value)}
            autoComplete="current-password"
          />
        </div>
        <Button
          variant="destructive"
          onClick={handleDeleteAccount}
          disabled={currentPassword.length === 0 || deleteMutation.isPending}
        >
          {deleteMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : "Delete my account"}
        </Button>
      </div>
    </SettingsLayout>
  );
}
