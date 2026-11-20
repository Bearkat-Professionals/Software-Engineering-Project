// Calls the product API. Sample mode returns local JSON. A 401 sends the user back to sign in.
import scanStarted from "../samples/scan-started.json";
import scanStatusIdle from "../samples/scan-status-idle.json";
import devices from "../samples/devices.json";
import scans from "../samples/scans.json";
import changeReport from "../samples/change-report.json";
import exportSample from "../samples/export.json";

// Local stand-ins used only when VITE_USE_SAMPLES is true.
const SAMPLES = {

  "GET /api/scans/status": scanStatusIdle,
  "POST /api/scans": scanStarted,

};

// A failed request. reasons, existingId, and changedAt come from reference-data errors.
export class ApiError extends Error {

  constructor(message, { status = 0, unreachable = false, reasons = null, existingId = null, changedAt = null, deviceId = null, heldBy = null } = {}) {
    
    super(message);
  
    this.name = "ApiError";
    this.status = status;
    this.unreachable = unreachable;
    this.reasons = reasons;
    this.existingId = existingId;
    this.changedAt = changedAt;
    this.deviceId = deviceId;
    this.heldBy = heldBy;
  
  }
}


export async function api(path, { method = "GET", body, blob = false } = {}) {

  // Sample mode never calls the server. The signed-in user is a fixed editor.
  if (import.meta.env.VITE_USE_SAMPLES === "true") {

    if (path === "/api/me" || path === "/api/login") {

      return {

        user: { username: "sam", role: "editor" },
        permissions: ["VIEW_DATA", "EXPORT", "START_SCAN", "EDIT_DEVICES", "EDIT_REFERENCE_DATA"],

      }

    }

    if (method === "POST" && path === "/api/logout") return null;

    if (method === "GET" && path.startsWith("/api/devices")) {

      const query = new URLSearchParams(path.split("?")[1] || "");
      const perPage = Number(query.get("per_page")) || 50;
      const page = Number(query.get("page")) || 1;
      const start = (page - 1) * perPage;

      return {

        devices: devices.slice(start, start + perPage),
        page,
        per_page: perPage,
        total: devices.length,

      };

    }

    // A downlaod gets the export sample as a file with a timestamp
    if (blob) {

      const day = new Date().toISOString().slice(0, 10);

      return new Response(JSON.stringify(exportSample, null, 2), {

        headers: {

          "Content-Type": "application/json",
          "Content-Disposition": `attachment; filename="network-devices-${day}.json"`,
        }
      })
    }

    if (method === "GET" && path.split("?")[0] === "/api/scans") return scans;

    if (method === "GET" && path.startsWith("/api/reports/changes")) return changeReport;

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

  // Dowloading wants an ACTUAL file. A failed download goes through error checks
  if (blob && response.ok) return response; 

  // Logout and other empty successes have no JSON body.
  if (response.status === 204) return null;

  const payload = await response.json().catch(() => null);

  if (!response.ok) {

    // A wrong password stays on the form. Any other 401 means the session is gone.
    if (response.status === 401 && path !== "/api/login") {

      const message = path.startsWith("/api/me")

        ? "Sign in to continue."
        : "Your session ended. Sign in again to continue.";

      if (!path.startsWith("/api/me") && !window.location.pathname.startsWith("/login")) {

        const next = window.location.pathname + window.location.search;

        window.location.assign(`/login?next=${encodeURIComponent(next)}&ended=1`);

      }

      throw new ApiError(message, { status: 401 });
      
    }

    // A proxy or server failure often has no JSON error sentence.
    if (response.status >= 500 && !(payload && typeof payload.error === "string")) {

      throw new ApiError("Can't reach the server", { status: response.status, unreachable: true });
      
    }

    const message = payload && typeof payload.error === "string"

      ? payload.error
      : response.statusText;

    throw new ApiError(message, {

      status: response.status,
      reasons: Array.isArray(payload?.reasons) ? payload.reasons : null,
      existingId: payload?.existing_id ?? null,
      changedAt: payload?.changed_at ?? null,
      deviceId: payload?.device_id ?? null,
      heldBy: Array.isArray(payload?.held_by) ? payload.held_by : null,
      
    });

  }

  return payload;
  
}