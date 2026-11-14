// Local synthetic data only; never imported by the production app.
import { createServer } from "node:http";
import { createMockFetch, scenarios, type Scenario } from "../src/mock";
const scenario = process.env.MOCK_SCENARIO ?? "normal";
if (!scenarios.includes(scenario as Scenario)) throw new Error("Unknown MOCK_SCENARIO");
const mock = createMockFetch(scenario as Scenario);
createServer(async (req, res) => {
  try {
    const chunks: Buffer[] = [];
    for await (const chunk of req) chunks.push(Buffer.from(chunk));
    const method = req.method ?? "GET";
    const headers = new Headers();
    for (const [name, value] of Object.entries(req.headers)) if (value) headers.set(name, Array.isArray(value) ? value.join(",") : value);
    const response = method === "OPTIONS" ? new Response(null, { status: 204 }) : await mock(new Request(`http://127.0.0.1:8001${req.url}`, { method, headers, ...(chunks.length ? { body: Buffer.concat(chunks) } : {}) }));
    res.writeHead(response.status, { ...Object.fromEntries(response.headers), "access-control-allow-origin": "http://127.0.0.1:3000", "access-control-allow-headers": "Authorization, Content-Type, Idempotency-Key", "access-control-expose-headers": "X-Request-ID, Retry-After", "access-control-allow-methods": "GET, PATCH, POST, OPTIONS", "cache-control": "no-store" });
    res.end(Buffer.from(await response.arrayBuffer()));
  } catch {
    res.writeHead(500); res.end();
  }
}).listen(8001, "127.0.0.1", () => console.log(`Mock API on 127.0.0.1:8001 (${scenario})`));
