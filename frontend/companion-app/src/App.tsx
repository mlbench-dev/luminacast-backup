import { useEffect } from "react";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { QueryClientProvider } from "@tanstack/react-query";
import { queryClient } from "@/lib/api";
import { Toaster } from "@/components/ui/toaster";
import { ErrorBoundary } from "@/components/ErrorBoundary";
import { AppLayout } from "@/components/layout/AppLayout";
import { ProtectedRoute } from "@/components/layout/ProtectedRoute";
import { useAuthStore } from "@/stores/authStore";
import { LoginPage } from "@/pages/Login";
import { SignupPage } from "@/pages/Signup";
import { ForgotPasswordPage } from "@/pages/ForgotPassword";
import { ResetPasswordPage } from "@/pages/ResetPassword";
import { CastBuilderPage } from "@/pages/CastBuilder";
import { MyCastsPage } from "@/pages/MyCasts";
import { CastDetailPage } from "@/pages/CastDetail";
import { LiveControlPage } from "@/pages/LiveControl";
import { SetupPage } from "@/pages/Setup";
import { AnalyticsPage } from "@/pages/Analytics";
import { AdminPage } from "@/pages/Admin";
import { StreamKeysPage } from "@/pages/settings/StreamKeys";
import { MyChannelsPage } from "@/pages/settings/MyChannels";
import { TeamPage } from "@/pages/settings/Team";
import { BillingPage } from "@/pages/settings/Billing";
import { ProductLibraryPage } from "@/pages/ProductLibrary";
import { AIAvatarSetupPage } from "@/pages/AIAvatarSetup";
import { EditAvatarPage } from "@/pages/EditAvatarPage";
import { PromptAdminPage } from "@/pages/PromptAdmin";
import { AdminCostsPage } from "@/pages/AdminCosts";
import { ControlPanelPage } from "@/pages/ControlPanel";
import { MusicPage } from "@/pages/Music";
import { MyVideosPage } from "@/pages/MyVideos";
import { DashboardPage } from "@/pages/Dashboard";
import GoLive from "@/pages/GoLive";
import LiveMonitor from "@/pages/LiveMonitor";
import PublishCast from "@/pages/PublishCast";
import Published from "@/pages/Published";
import Comments from "@/pages/Comments";
import PublishHub from "@/pages/PublishHub";
import SocialChannelsPage from "@/pages/MyChannels";
import ZernioCallback from "@/pages/ZernioCallback";
import { UserRole } from "@/lib/types";

export default function App() {
  const hydrate = useAuthStore((s) => s.hydrate);
  useEffect(() => { hydrate(); }, [hydrate]);
  return (
    <QueryClientProvider client={queryClient}>
      <ErrorBoundary>
        <BrowserRouter>
          <Routes>
            {/* Public routes */}
            <Route path="/login" element={<LoginPage />} />
            <Route path="/signup" element={<SignupPage />} />
            <Route path="/register" element={<Navigate to="/signup" replace />} />
            <Route path="/forgot-password" element={<ForgotPasswordPage />} />
            <Route path="/reset-password" element={<ResetPasswordPage />} />
            <Route path="/integrations/zernio/callback" element={<ZernioCallback />} />

            {/* Protected routes with sidebar layout */}
            <Route element={<ProtectedRoute />}>
              <Route element={<AppLayout />}>
                <Route path="/dashboard" element={<DashboardPage />} />
                <Route path="/cast-builder" element={<MyCastsPage />} />
                <Route path="/cast-builder/new" element={<CastBuilderPage />} />
                <Route path="/cast-builder/:castId" element={<CastBuilderPage />} />
                <Route path="/cast-builder/:castId/:phase" element={<CastBuilderPage />} />
                <Route path="/live-control" element={<LiveControlPage />} />
                <Route path="/go-live" element={<GoLive />} />
                <Route path="/live/:sessionId/monitor" element={<LiveMonitor />} />
                <Route path="/publish" element={<PublishHub />} />
                <Route path="/publish/:castId" element={<PublishCast />} />
                {/* /distribute → /publish for bookmarks (renamed in CHANGE 1) */}
                <Route path="/distribute" element={<Navigate to="/publish" replace />} />
                <Route path="/published" element={<Published />} />
                <Route path="/comments" element={<Comments />} />
                <Route path="/comments/:postId" element={<Comments />} />
                {/* Unified My Channels (CHANGE 2). Old paths redirect for bookmarks. */}
                <Route path="/channels" element={<SocialChannelsPage />} />
                <Route path="/settings/social-channels" element={<Navigate to="/channels" replace />} />
                <Route path="/seller-channels" element={<Navigate to="/channels" replace />} />
                {/* Keep /live as alias for backwards compat */}
                <Route path="/live" element={<Navigate to="/live-control" replace />} />
                <Route path="/my-avatar" element={<SetupPage />} />
                <Route path="/my-avatar/clone" element={<SetupPage />} />
                <Route path="/my-avatar/clone/:avatarId" element={<SetupPage />} />
                <Route path="/my-avatar/clone/:avatarId/:step" element={<SetupPage />} />
                <Route path="/my-avatar/:avatarId/edit" element={<EditAvatarPage />} />
                <Route path="/my-avatar/ai-avatar" element={<AIAvatarSetupPage />} />
                <Route path="/my-avatar/ai/:avatarId" element={<AIAvatarSetupPage />} />
                <Route path="/my-avatar/ai/:avatarId/:step" element={<AIAvatarSetupPage />} />
                {/* Redirect old /setup URLs for bookmarks */}
                <Route path="/setup/*" element={<Navigate to="/my-avatar" replace />} />
                <Route path="/music" element={<MusicPage />} />
                <Route path="/my-videos" element={<MyVideosPage />} />
                <Route path="/products" element={<ProductLibraryPage />} />
                <Route path="/analytics" element={<AnalyticsPage />} />
                {/* Settings sub-pages */}
                <Route path="/settings/stream-keys" element={<StreamKeysPage />} />
                <Route path="/settings/channels" element={<MyChannelsPage />} />
                <Route path="/settings/team" element={<TeamPage />} />
                <Route path="/settings/billing" element={<BillingPage />} />
                {/* Admin cost dashboard — backend gates by email allow-list
                  (3gorka72@gmail.com), so route protection is the standard
                  authenticated check. Sidebar item is conditional on email. */}
                <Route path="/admin/costs" element={<AdminCostsPage />} />
              </Route>
            </Route>

            {/* Admin-only routes */}
            <Route element={<ProtectedRoute requiredRole={UserRole.ADMIN} />}>
              <Route element={<AppLayout />}>
                <Route path="/admin" element={<AdminPage />} />
                <Route path="/control" element={<ControlPanelPage />} />
                {/* Legacy route redirect */}
                <Route path="/preadmin" element={<Navigate to="/control" replace />} />
              </Route>
            </Route>

            {/* Catch-all redirect */}
            <Route path="*" element={<Navigate to="/dashboard" replace />} />
          </Routes>
        </BrowserRouter>
      </ErrorBoundary>
      <Toaster />
    </QueryClientProvider>
  );
}
