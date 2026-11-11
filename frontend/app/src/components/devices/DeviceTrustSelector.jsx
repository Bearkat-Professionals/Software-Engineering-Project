
import { useState } from "react";

export default function DeviceTrustSelector({
  device,
  onSave,
}) {
  const initialStatus = device.trusted
    ? "trusted"
    : device.flagged
      ? "flagged"
      : "automatic";

  const [status, setStatus] = useState(initialStatus);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  async function handleSave() {
    setError("");

    if (typeof onSave !== "function") {
      setError("Saving is not connected yet.");
      return;
    }

    if (!device.loaded_at) {
      setError("Reload the device before changing its status.");
      return;
    }

    setSaving(true);

    try {
      await onSave(device.device_id, {
        status,
        loaded_at: device.loaded_at,
      });
    } catch (err) {
      setError(err.message || "Unable to update device status.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <section aria-label="Device trust status">
      <h3>Device Status</h3>

      <label htmlFor="trust-status">Choose status</label>
      <select
        id="trust-status"
        value={status}
        onChange={(event) => setStatus(event.target.value)}
        disabled={saving}
      >
        <option value="trusted">Trusted</option>
        <option value="flagged">Flagged</option>
        <option value="automatic">Automatic</option>
      </select>

      <p>
        Trusted: You recognize this device.
        Flagged: This device needs attention.
        Automatic: Let the scanner determine its status.
      </p>

      {error && <p role="alert">{error}</p>}

      <button
        type="button"
        onClick={handleSave}
        disabled={saving}
      >
        {saving ? "Saving..." : "Save Status"}
      </button>
    </section>
  );
}
