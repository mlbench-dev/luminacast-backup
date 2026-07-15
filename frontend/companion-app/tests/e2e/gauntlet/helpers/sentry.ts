import { APIRequestContext } from "@playwright/test";

const SENTRY_TOKEN = process.env.SENTRY_TOKEN || "";
const SENTRY_ORG = "novalios";

export async function assertNoNewErrors(
  request: APIRequestContext,
  startTime: string,
  endTime: string,
  projects: string[] = ["luminacast-orchestrator", "luminacast-gpu-worker"]
): Promise<{ project: string; count: number; issues: string[] }[]> {
  const results: { project: string; count: number; issues: string[] }[] = [];

  for (const project of projects) {
    try {
      const resp = await request.get(
        `https://sentry.io/api/0/projects/${SENTRY_ORG}/${project}/issues/?statsPeriod=1h&query=is:unresolved&limit=10`,
        { headers: { Authorization: `Bearer ${SENTRY_TOKEN}` } }
      );
      if (resp.ok()) {
        const issues = await resp.json();
        if (Array.isArray(issues)) {
          results.push({
            project,
            count: issues.length,
            issues: issues.map((i: any) => `[${i.shortId}] ${i.title?.slice(0, 80)}`),
          });
        }
      }
    } catch {
      results.push({ project, count: -1, issues: ["Sentry check failed"] });
    }
  }

  return results;
}
