import scanStarted from "../samples/scan-started.json";
import scanStatusIdle from "../samples/scan-status-idle.json";


const SAMPLES = {
  "GET /api/scans/status": scanStatusIdle,
  "POST /api/scans": scanStarted,

  "GET /api/devices": {
    devices: [
      {
        device_id: 1,
        name: "Test Laptop",
        model: "Latitude",
        type: "Laptop",
        vendor: "Dell",
        mac: "AA:BB:CC:DD:EE:FF",
        trusted: true,
        flagged: false,
        loaded_at: "2026-11-11T00:00:00",
      },
      {
        device_id: 2,
        name: "Office Printer",
        model: "LaserJet",
        type: "Printer",
        vendor: "HP",
        mac: "11:22:33:44:55:66",
        trusted: false,
        flagged: false,
        loaded_at: "2026-11-11T00:00:00",
      },
    ],
    page: 1,
    per_page: 25,
    total: 2,
  },
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