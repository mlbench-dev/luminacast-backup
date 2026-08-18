import { Navigate } from "react-router-dom";
import { useAuthStore } from "@/stores/authStore";

/**
 * PublicRoute component that redirects authenticated users away from auth pages.
 * Ensures logged-in users cannot access login, signup, forgot password, or reset password pages.
 */
export function PublicRoute({ children }: { children: React.ReactNode }) {
  const token = useAuthStore((s) => s.token);

  // If user is logged in, redirect to dashboard
  if (token) {
    return <Navigate to="/dashboard" replace />;
  }

  // If not logged in, render the page
  return children;
}
