/**
 * Calls Clynotion's weekly-hours tick once a minute.
 *
 * Secrets (wrangler secret put), never committed:
 *   CRON_SECRET          same value as the app's CRON_SECRET
 *   CLYNOTION_TICK_URL   https://clynotion.com/internal/time/tick
 *
 * The Worker does not store hours. A non-2xx response throws so the platform
 * can retry this invocation. That is not a promise of inbox delivery.
 * See docs/modules/ops6-weekly-hours.md.
 */
export default {
  async scheduled(_event, env, ctx) {
    ctx.waitUntil(postTick(env));
  },
};

async function postTick(env) {
  const url = env.CLYNOTION_TICK_URL;
  const secret = env.CRON_SECRET;
  if (!url || !secret) {
    throw new Error("tick_not_configured");
  }
  const response = await fetch(url, {
    method: "POST",
    headers: { authorization: `Bearer ${secret}` },
  });
  if (!response.ok) {
    throw new Error(`tick_http_${response.status}`);
  }
}
