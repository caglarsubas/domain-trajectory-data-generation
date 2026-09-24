const DROP = ["connection", "keep-alive", "transfer-encoding", "host"];

function targetBase() {
  return (process.env.API_PROXY_TARGET || "http://127.0.0.1:8000").replace(/\/$/, "");
}

async function proxy(request, context) {
  const { path } = await context.params;
  const incoming = new URL(request.url);
  const dest = `${targetBase()}/${path.join("/")}${incoming.search}`;
  const headers = new Headers(request.headers);
  for (const name of DROP) headers.delete(name);
  const init = { method: request.method, headers, redirect: "manual" };
  if (request.method !== "GET" && request.method !== "HEAD") {
    init.body = Buffer.from(await request.arrayBuffer());
  }
  let upstream;
  try {
    upstream = await fetch(dest, init);
  } catch {
    return Response.json({ detail: "API is unreachable" }, { status: 502 });
  }
  const outHeaders = new Headers(upstream.headers);
  for (const name of DROP) outHeaders.delete(name);
  return new Response(upstream.body, { status: upstream.status, headers: outHeaders });
}

export const dynamic = "force-dynamic";

export const GET = proxy;
export const POST = proxy;
export const PUT = proxy;
export const PATCH = proxy;
export const DELETE = proxy;
