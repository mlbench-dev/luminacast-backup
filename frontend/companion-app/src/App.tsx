import { useEffect, lazy, Suspense } from "react";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { QueryClientProvider } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { queryClient } from "@/lib/api";
import { Toaster } from "@/components/ui/toaster";
import { ErrorBoundary } from "@/components/common/ErrorBoundary";
import { AppLayout } from "@/components/layout/AppLayout";
import { ProtectedRoute } from "@/components/layout/ProtectedRoute";
import { PublicRoute } from "@/components/layout/PublicRoute";
import { SettingsLayout } from "@/components/layout/SettingsLayout";
import { useAuthStore } from "@/stores/authStore";
import { UserRole } from "@/lib/types";

// Route pages are code-split so a given screen only downloads/parses/keeps in
// memory the JS it actually needs. The Cast Builder in particular drags in the
// whole Remotion editor (~90k LOC + @remotion/*); before this it was parsed
// and resident on every page, including login. Suspense (below) covers the
// brief async load. `.then(m => ({ default: ... }))` adapts our named exports
// to what React.lazy expects.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const lz = (f: () => Promise<{ default: any }>) => lazy(f);
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const named = (imp: () => Promise<any>, key: string) =>
  lazy(() => imp().then((m) => ({ default: m[key] })));

const LoginPage = named(() => import("@/pages/auth/Login"), "LoginPage");
const SignupPage = named(() => import("@/pages/auth/Signup"), "SignupPage");
const ForgotPasswordPage = named(() => import("@/pages/auth/ForgotPassword"), "ForgotPasswordPage");
const ResetPasswordPage = named(() => import("@/pages/auth/ResetPassword"), "ResetPasswordPage");
const ReactivateAccountPage = named(() => import("@/pages/auth/ReactivateAccount"), "ReactivateAccountPage");
const AcceptInvitePage = named(() => import("@/pages/teams/AcceptInvite"), "AcceptInvitePage");
const CastBuilderPage = named(() => import("@/pages/cast-builder/CastBuilder"), "CastBuilderPage");
const MyCastsPage = named(() => import("@/pages/cast-builder/MyCasts"), "MyCastsPage");
const LiveControlPage = named(() => import("@/pages/live-stream/LiveControl"), "LiveControlPage");
const SetupPage = named(() => import("@/pages/avatar/Setup"), "SetupPage");
const AnalyticsPage = named(() => import("@/pages/analytics/Analytics"), "AnalyticsPage");
const ReviewQueuePage = named(() => import("@/pages/review/ReviewQueue"), "ReviewQueuePage");
const AdminPage = named(() => import("@/pages/admin/Admin"), "AdminPage");
const StreamKeysPage = named(() => import("@/pages/settings/StreamKeys"), "StreamKeysPage");
const MyChannelsPage = named(() => import("@/pages/settings/MyChannels"), "MyChannelsPage");
const TeamPage = named(() => import("@/pages/teams/Team"), "TeamPage");
const BillingPage = named(() => import("@/pages/settings/Billing"), "BillingPage");
const PricingPage = named(() => import("@/pages/settings/Pricing"), "PricingPage");
const SettingsProfilePage = named(() => import("@/pages/settings/Settings"), "SettingsProfilePage");
const SettingsPasswordPage = named(() => import("@/pages/settings/Settings"), "SettingsPasswordPage");
const SettingsDeleteAccountPage = named(() => import("@/pages/settings/Settings"), "SettingsDeleteAccountPage");
const ProductLibraryPage = named(() => import("@/pages/cast-builder/ProductLibrary"), "ProductLibraryPage");
const AIAvatarSetupPage = named(() => import("@/pages/avatar/AIAvatarSetup"), "AIAvatarSetupPage");
const EditAvatarPage = named(() => import("@/pages/avatar/EditAvatarPage"), "EditAvatarPage");
const AdminCostsPage = named(() => import("@/pages/admin/AdminCosts"), "AdminCostsPage");
const ControlPanelPage = named(() => import("@/pages/admin/ControlPanel"), "ControlPanelPage");
const PlaygroundPage = named(() => import("@/pages/admin/Playground"), "PlaygroundPage");
const BrollPlaygroundPage = named(() => import("@/pages/admin/BrollPlayground"), "BrollPlaygroundPage");
const MusicPage = named(() => import("@/pages/music/Music"), "MusicPage");
const MyVideosPage = named(() => import("@/pages/media-library/MyVideos"), "MyVideosPage");
const DashboardPage = named(() => import("@/pages/dashboard/Dashboard"), "DashboardPage");
const GoLive = lz(() => import("@/pages/live-stream/GoLive"));
const LiveMonitor = lz(() => import("@/pages/live-stream/LiveMonitor"));
const PublishCast = lz(() => import("@/pages/publish/PublishCast"));
const Comments = lz(() => import("@/pages/publish/Comments"));
const PublishHub = lz(() => import("@/pages/publish/PublishHub"));
const SocialChannelsPage = lz(() => import("@/pages/channels/MyChannels"));
const ZernioCallback = lz(() => import("@/pages/publish/ZernioCallback"));

function RouteFallback() {
  return (
    <div className="flex h-screen w-full items-center justify-center bg-[#0a0a0a]">
      <Loader2 className="h-6 w-6 animate-spin text-white/40" />
    </div>
  );
}

export default function App() {
  const hydrate = useAuthStore((s) => s.hydrate);
  useEffect(() => { hydrate(); }, [hydrate]);
  return (
    <QueryClientProvider client={queryClient}>
      <ErrorBoundary>
        <BrowserRouter>
          <Suspense fallback={<RouteFallback />}>
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
                <Route path="/playground" element={<PlaygroundPage />} />
                <Route path="/broll-playground" element={<BrollPlaygroundPage />} />
                {/* Legacy route redirect */}
                <Route path="/preadmin" element={<Navigate to="/control" replace />} />
              </Route>
            </Route>

            {/* Catch-all redirect */}
            <Route path="*" element={<Navigate to="/dashboard" replace />} />
          </Routes>
          </Suspense>
        </BrowserRouter>
      </ErrorBoundary>
      <Toaster />
    </QueryClientProvider>
  );
}
