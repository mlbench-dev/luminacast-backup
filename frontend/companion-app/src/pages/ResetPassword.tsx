import { useState } from "react";
import { useNavigate, useSearchParams, Link } from "react-router-dom";
import { Lock, CheckCircle, Eye, EyeOff } from "lucide-react";
import axios from "axios";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { authApi } from "@/lib/api";
import { toast } from "@/hooks/useToast";
import { validatePassword, PASSWORD_REQUIREMENTS_TEXT } from "@/lib/passwordValidation";

export function ResetPasswordPage() {
  const [searchParams] = useSearchParams();
  const token = searchParams.get("token") || "";
  const navigate = useNavigate();

  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [done, setDone] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (password !== confirmPassword) {
      toast({ title: "Error", description: "Passwords don't match.", variant: "destructive" });
      return;
    }
    const passwordError = validatePassword(password);
    if (passwordError) {
      toast({ title: "Weak password", description: passwordError, variant: "destructive" });
      return;
    }
    setIsLoading(true);
    try {
      await authApi.resetPassword({ token, password });
      setDone(true);
      toast({ title: "Password reset", variant: "success" });
    } catch (err: unknown) {
      let errorMessage = "This reset link is invalid or has expired.";
      if (axios.isAxiosError(err) && err.response?.data?.detail) {
        errorMessage = err.response.data.detail;
      }
      toast({ title: "Error", description: errorMessage, variant: "destructive" });
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-bg">
      <div className="w-full max-w-md px-4">
        <div className="mb-8 flex flex-col items-center">
          <img src="/logo/luminacast-wordmark.svg" alt="Luminacast" height={40} className="mb-3 h-10" />
          <p className="text-sm text-text-dim">AI Avatar Live Selling Platform</p>
        </div>

        <Card className="border-border/50 bg-card/80 backdrop-blur-xs">
          <CardHeader className="text-center">
            <CardTitle>Reset Password</CardTitle>
            <CardDescription>
              {done ? "Your password has been updated" : "Choose a new password for your account"}
            </CardDescription>
          </CardHeader>
          <CardContent>
            {!token ? (
              <div className="py-4 text-center text-sm text-text-dim">
                This reset link is missing its token.{" "}
                <Link to="/forgot-password" className="text-accent hover:underline">
                  Request a new one
                </Link>
                .
              </div>
            ) : done ? (
              <div className="flex flex-col items-center gap-3 py-4 text-center">
                <CheckCircle className="h-10 w-10 text-green-500" />
                <Button onClick={() => navigate("/login")} className="w-full" data-testid="reset-password-go-login">
                  Sign In
                </Button>
              </div>
            ) : (
              <form onSubmit={handleSubmit} className="space-y-4">
                <div className="space-y-2">
                  <div className="relative">
                    <Lock className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-text-muted" />
                    <Input
                      type={showPassword ? "text" : "password"}
                      placeholder="New password"
                      value={password}
                      onChange={(e) => setPassword(e.target.value)}
                      required
                      minLength={8}
                      data-testid="reset-password-new"
                      className="pl-10 pr-10"
                    />
                    <button
                      type="button"
                      onClick={() => setShowPassword((v) => !v)}
                      className="absolute right-3 top-1/2 -translate-y-1/2 text-text-muted hover:text-text-dim"
                      aria-label={showPassword ? "Hide password" : "Show password"}
                      data-testid="reset-password-toggle"
                    >
                      {showPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                    </button>
                  </div>
                  <p className="text-xs text-text-muted">{PASSWORD_REQUIREMENTS_TEXT}</p>
                  <div className="relative">
                    <Lock className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-text-muted" />
                    <Input
                      type={showPassword ? "text" : "password"}
                      placeholder="Confirm new password"
                      value={confirmPassword}
                      onChange={(e) => setConfirmPassword(e.target.value)}
                      required
                      minLength={8}
                      data-testid="reset-password-confirm"
                      className="pl-10 pr-10"
                    />
                  </div>
                </div>

                <Button
                  type="submit"
                  className="w-full"
                  disabled={isLoading}
                  data-testid="reset-password-submit"
                >
                  {isLoading ? (
                    <div className="h-4 w-4 animate-spin rounded-full border-2 border-white border-t-transparent" />
                  ) : (
                    "Reset Password"
                  )}
                </Button>
              </form>
            )}

            <div className="mt-4 text-center">
              <Link to="/login" className="text-sm text-accent hover:underline">
                Back to sign in
              </Link>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}