import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { Check, Loader2 } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { billingApi } from "@/lib/api";
import { useToast } from "@/hooks/useToast";
import type { BillingIntervalId, PlanId } from "@/lib/types";

function formatDollars(cents: number): string {
  return `$${Math.round(cents / 100)}`;
}

const PLAN_ORDER: PlanId[] = ["starter", "pro", "studio"];

export function PricingPage() {
  const [interval, setInterval] = useState<BillingIntervalId>("month");
  const { toast } = useToast();
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const { data, isLoading } = useQuery({
    queryKey: ["billing-plans"],
    queryFn: () => billingApi.getPlans(),
  });

  const checkout = useMutation({
    mutationFn: (plan: PlanId) => billingApi.checkoutSubscription(plan, interval),
    onSuccess: (result) => {
      if ("url" in result) {
        window.location.href = result.url;
        return;
      }
      // Plan change applied in place (no new subscription/checkout needed).
      toast({ title: `Switched to ${result.plan} (${result.interval}ly)` });
      queryClient.invalidateQueries({ queryKey: ["billing-dashboard"] });
      navigate("/settings/billing");
    },
    onError: (err: unknown) => {
      const description =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ||
        "Please try again in a moment.";
      toast({ title: "Could not start checkout", description, variant: "destructive" });
    },
  });

  return (
    <div className="mx-auto w-full max-w-5xl space-y-8" data-testid="pricing-page">
      <div className="text-center space-y-3">
        <h1 className="text-3xl font-bold text-text">Plans & pricing</h1>
        <p className="text-text-dim max-w-xl mx-auto">
          Render minutes and live-streaming hours refresh every billing month. Avatar slots are
          yours to keep.
        </p>

        <div className="inline-flex items-center rounded-full border border-border bg-surface p-1">
          <button
            className={`rounded-full px-4 py-1.5 text-sm font-medium transition-colors ${
              interval === "month" ? "bg-accent text-white" : "text-text-dim"
            }`}
            onClick={() => setInterval("month")}
          >
            Monthly
          </button>
          <button
            className={`rounded-full px-4 py-1.5 text-sm font-medium transition-colors ${
              interval === "year" ? "bg-accent text-white" : "text-text-dim"
            }`}
            onClick={() => setInterval("year")}
          >
            Annual <span className="ml-1 opacity-80">(~20% off)</span>
          </button>
        </div>
      </div>

      {isLoading || !data ? (
        <div className="grid grid-cols-1 @md:grid-cols-2 @3xl:grid-cols-3 gap-8">
          <Skeleton className="h-96" />
          <Skeleton className="h-96" />
          <Skeleton className="h-96" />
        </div>
      ) : (
        <div className="grid grid-cols-1 @md:grid-cols-2 @3xl:grid-cols-3 gap-8 items-stretch">
          {PLAN_ORDER.map((planId) => {
            const plan = data.plans[planId];
            const isAnnual = interval === "year";
            const monthlyEquivalent = isAnnual
              ? plan.annual_monthly_equivalent_cents
              : plan.monthly_price_cents;
            const featured = planId === "pro";

            return (
              <Card
                key={planId}
                className={`flex flex-col ${featured ? "border-accent shadow-lg shadow-accent/10" : ""}`}
              >
                <CardHeader className="space-y-2">
                  <div className="flex items-center justify-between">
                    <CardTitle>{plan.name}</CardTitle>
                    {featured && <Badge>Most popular</Badge>}
                  </div>
                  <div>
                    <span className="text-3xl font-bold text-text">
                      {formatDollars(monthlyEquivalent)}
                    </span>
                    <span className="text-text-dim">/mo</span>
                  </div>
                  {isAnnual ? (
                    <p className="text-xs text-text-muted">
                      Billed annually at {formatDollars(plan.annual_price_cents)}/year
                    </p>
                  ) : (
                    <p className="text-xs text-text-muted">Billed monthly</p>
                  )}
                </CardHeader>
                <CardContent className="flex flex-col flex-1 justify-between gap-6">
                  <ul className="space-y-2 text-sm text-text-dim">
                    <li className="flex gap-2"><Check className="h-4 w-4 text-success shrink-0" />{plan.avatar_slots} avatar slots</li>
                    <li className="flex gap-2"><Check className="h-4 w-4 text-success shrink-0" />{plan.render_minutes_per_month} render minutes/month</li>
                    <li className="flex gap-2"><Check className="h-4 w-4 text-success shrink-0" />{plan.live_stream_hours_per_month} live-streaming hours/month</li>
                    <li className="flex gap-2">
                      <Check className="h-4 w-4 text-success shrink-0" />
                      {planId === "starter"
                        ? `Unlimited social accounts (fair-use ~${data.starter_fair_use_social_accounts} internally)`
                        : "Unlimited social accounts"}
                    </li>
                    <li className="flex gap-2 capitalize"><Check className="h-4 w-4 text-success shrink-0" />{plan.production_level} production</li>
                    {plan.team_seats > 0 && (
                      <li className="flex gap-2"><Check className="h-4 w-4 text-success shrink-0" />{plan.team_seats} team seats</li>
                    )}
                  </ul>
                  <Button
                    className="w-full"
                    variant={featured ? "default" : "outline"}
                    disabled={checkout.isPending}
                    onClick={() => checkout.mutate(planId)}
                  >
                    {checkout.isPending && checkout.variables === planId ? (
                      <Loader2 className="h-4 w-4 animate-spin" />
                    ) : (
                      `Choose ${plan.name}`
                    )}
                  </Button>
                </CardContent>
              </Card>
            );
          })}
        </div>
      )}

      {data && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Free tier</CardTitle>
          </CardHeader>
          <CardContent className="text-sm text-text-dim">
            Every new account starts with {data.free_tier.avatar_slots} avatar and{" "}
            {data.free_tier.render_minutes} render minutes (watermarked output) — no card
            required. Once that's used up, subscribe to a plan above or buy PAYG credits from the
            Billing page to keep rendering.
          </CardContent>
        </Card>
      )}
    </div>
  );
}
