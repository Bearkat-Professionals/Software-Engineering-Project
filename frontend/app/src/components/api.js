import scanStarted from "../samples/scan-started.json";
import scanStatusIdle from "../samples/scan-status-idle.json";
import devices from "../samples/devices.json";

const SAMPLES = {

  "GET /api/scans/status": scanStatusIdle,
  "POST /api/scans": scanStarted,

};

export class ApiError extends Error {

  constructor(message, { status = 0, unreachable = false } = {}) {
    
    super(message);

    this.name = "ApiError";
    this.status = status;
    this.unreachable = unreachable;

  }

}

export async function api(path, { method = "GET", body } = {}) {

  if (import.meta.env.VITE_USE_SAMPLES === "true") {

    if (method === "GET" && path.startWith("/api/devices")) {

      const query = new URLSearchParams(path.split("?")[1] || "");
      const perPage = Number(query.get("per_page")) || 50;
      const page = Number(query.get("page")) || 1;
      const start = (page - 1) * perPage;

      return {

        devices: devices.slive(start, start + perPage),
        page,
        per_page: perPage,
        total: devices.length,

      };

    }

    const sample = SAMPLES[`${method} ${path}`];

    if (!sample) {

      throw new ApiError(`No sample for ${method} ${path}`, { status: 404 });

    }

    return sample;

  }

  let response;

  try {

    response = await fetch(path, {

      method,
      headers: {

        Accept: "application/json",
        ...(body ? { "Content-Type": "application/json" } : {}),

      },

      body: body ? JSON.stringify(body) : undefined,

    });

  } catch {

    throw new ApiError("Can't reach the server", { unreachable: true });

  }

  if (response.status === 204) return null;

  const payload = await response.json().catch(() => null);

  if (!response.ok) {

    if (response.status >= 500 && !(payload && typeof payload.error === "string")) {

      throw new ApiError("Can't reach the server", { status: response.status, unreachable: true });
      
    }

    const message = payload && typeof payload.error === "string"
      ? payload.error
      : response.statusText;
    throw new ApiError(message, { status: response.status });

  }

  return payload;
  
}