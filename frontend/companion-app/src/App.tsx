import { useEffect } from "react";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { QueryClientProvider } from "@tanstack/react-query";
import { queryClient } from "@/lib/api";
import { Toaster } from "@/components/ui/toaster";
import { ErrorBoundary } from "@/components/common/ErrorBoundary";
import { AppLayout } from "@/components/layout/AppLayout";
import { ProtectedRoute } from "@/components/layout/ProtectedRoute";
import { PublicRoute } from "@/components/layout/PublicRoute";
import { useAuthStore } from "@/stores/authStore";
import { LoginPage } from "@/pages/auth/Login";
import { SignupPage } from "@/pages/auth/Signup";
import { ForgotPasswordPage } from "@/pages/auth/ForgotPassword";
import { ResetPasswordPage } from "@/pages/auth/ResetPassword";
import { ReactivateAccountPage } from "@/pages/auth/ReactivateAccount";
import { AcceptInvitePage } from "@/pages/teams/AcceptInvite";
import { CastBuilderPage } from "@/pages/cast-builder/CastBuilder";
import { MyCastsPage } from "@/pages/cast-builder/MyCasts";
import { CastDetailPage } from "@/pages/cast-builder/CastDetail";
import { LiveControlPage } from "@/pages/live-stream/LiveControl";
import { SetupPage } from "@/pages/avatar/Setup";
import { AnalyticsPage } from "@/pages/analytics/Analytics";
import { ReviewQueuePage } from "@/pages/review/ReviewQueue";
import { AdminPage } from "@/pages/admin/Admin";
import { StreamKeysPage } from "@/pages/settings/StreamKeys";
import { MyChannelsPage } from "@/pages/settings/MyChannels";
import { TeamPage } from "@/pages/teams/Team";
import { BillingPage } from "@/pages/settings/Billing";
import { PricingPage } from "@/pages/settings/Pricing";
import { SettingsProfilePage, SettingsPasswordPage, SettingsDeleteAccountPage } from "@/pages/settings/Settings";
import { SettingsLayout } from "@/components/layout/SettingsLayout";
import { ProductLibraryPage } from "@/pages/cast-builder/ProductLibrary";
import { AIAvatarSetupPage } from "@/pages/avatar/AIAvatarSetup";
import { EditAvatarPage } from "@/pages/avatar/EditAvatarPage";
import { PromptAdminPage } from "@/pages/admin/PromptAdmin";
import { AdminCostsPage } from "@/pages/admin/AdminCosts";
import { ControlPanelPage } from "@/pages/admin/ControlPanel";
import { MusicPage } from "@/pages/music/Music";
import { MyVideosPage } from "@/pages/media-library/MyVideos";
import { DashboardPage } from "@/pages/dashboard/Dashboard";
import GoLive from "@/pages/live-stream/GoLive";
import LiveMonitor from "@/pages/live-stream/LiveMonitor";
import PublishCast from "@/pages/publish/PublishCast";
import Comments from "@/pages/publish/Comments";
import PublishHub from "@/pages/publish/PublishHub";
import SocialChannelsPage from "@/pages/channels/MyChannels";
import ZernioCallback from "@/pages/publish/ZernioCallback";
import { UserRole } from "@/lib/types";

export default function App() {
  const hydrate = useAuthStore((s) => s.hydrate);
  useEffect(() => { hydrate(); }, [hydrate]);
  return (
    <QueryClientProvider client={queryClient}>
      <ErrorBoundary>
        <BrowserRouter>
          <Routes>
            {/* Public auth routes — protected so logged-in users can't access them */}
            <Route path="/login" element={<PublicRoute><LoginPage /></PublicRoute>} />
            <Route path="/signup" element={<PublicRoute><SignupPage /></PublicRoute>} />
            <Route path="/register" element={<PublicRoute><Navigate to="/signup" replace /></PublicRoute>} />
            <Route path="/forgot-password" element={<PublicRoute><ForgotPasswordPage /></PublicRoute>} />
            <Route path="/reset-password" element={<PublicRoute><ResetPasswordPage /></PublicRoute>} />
            <Route path="/reactivate-account" element={<PublicRoute><ReactivateAccountPage /></PublicRoute>} />

            {/* External flow routes — accessible by anyone */}
            <Route path="/accept-invite" element={<AcceptInvitePage />} />
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
                {/* /published and bare /comments retired — both folded into
                    /publish's tabs. Redirect (not delete) so old bookmarks
                    still land somewhere useful, matching /distribute above. */}
                <Route path="/published" element={<Navigate to="/publish?tab=published" replace />} />
                <Route path="/comments" element={<Navigate to="/publish?tab=comments" replace />} />
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
                <Route path="/review" element={<ReviewQueuePage />} />
                {/* Settings — Billing and Pricing render inside the same
                    SettingsLayout chrome (see SettingsLayout.tsx) so they
                    read as part of Settings rather than separate pages. */}
                <Route path="/settings" element={<SettingsProfilePage />} />
                <Route path="/settings/password" element={<SettingsPasswordPage />} />
                <Route path="/settings/delete-account" element={<SettingsDeleteAccountPage />} />
                <Route
                  path="/settings/billing"
                  element={
                    <SettingsLayout>
                      <BillingPage />
                    </SettingsLayout>
                  }
                />
                <Route
                  path="/settings/pricing"
                  element={
                    <SettingsLayout>
                      <PricingPage />
                    </SettingsLayout>
                  }
                />
                {/* Old bookmark for the standalone account page */}
                <Route path="/settings/account" element={<Navigate to="/settings" replace />} />
                {/* Old bookmark for the pricing page, now under Settings */}
                <Route path="/pricing" element={<Navigate to="/settings/pricing" replace />} />
                {/* Settings sub-pages */}
                <Route path="/settings/stream-keys" element={<StreamKeysPage />} />
                <Route path="/settings/channels" element={<MyChannelsPage />} />
                <Route path="/settings/team" element={<TeamPage />} />
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
