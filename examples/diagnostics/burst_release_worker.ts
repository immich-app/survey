/** Disposable public release barrier; deployment variables are its only controls. */
interface BarrierEnvironment {
  TRIAL_ID: string;
  RELEASED: string;
  EXPIRES_AT: string;
}

export default {
  fetch(request: Request, env: BarrierEnvironment): Response {
    if (request.method !== "GET") {
      return new Response(null, {
        status: 405,
        headers: { Allow: "GET", "Cache-Control": "no-store" },
      });
    }
    if (new URL(request.url).pathname !== "/") {
      return new Response(null, { status: 404 });
    }
    if (
      !/^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/.test(env.TRIAL_ID) ||
      !Number.isFinite(Date.parse(env.EXPIRES_AT)) ||
      !["true", "false"].includes(env.RELEASED)
    ) {
      return Response.json(
        { error: "Barrier configuration invalid" },
        { status: 503, headers: { "Cache-Control": "no-store" } },
      );
    }
    return Response.json(
      {
        trial_id: env.TRIAL_ID,
        released: env.RELEASED === "true",
        expires_at: env.EXPIRES_AT,
      },
      { headers: { "Cache-Control": "no-store, max-age=0" } },
    );
  },
};
