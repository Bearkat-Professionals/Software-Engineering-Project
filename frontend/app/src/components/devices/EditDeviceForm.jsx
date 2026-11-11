
import { useState } from "react";

export default function EditDeviceForm({ device, onSave, onCancel }) {
  const [form, setForm] = useState({
    name: device.name ?? "",
    model: device.model ?? "",
    type: device.type ?? "",
    vendor: device.vendor ?? "",
  });

  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  async function handleSubmit(event) {
    event.preventDefault();
    setError("");

    const changes = {};

    for (const field of ["name", "model", "type", "vendor"]) {
      if (form[field] !== (device[field] ?? "")) {
        changes[field] = form[field] || null;
      }
    }

    if (Object.keys(changes).length === 0) {
      setError("No changes to save.");
      return;
    }

    if (!device.loaded_at) {
      setError("Device version is missing. Reload before editing.");
      return;
    }

    if (typeof onSave !== "function") {
      setError("Saving is not connected yet.");
      return;
    }

    setSaving(true);

    try {
      await onSave(device.device_id, {
        ...changes,
        loaded_at: device.loaded_at,
      });
    } catch (err) {
      setError(err.message || "Unable to update device.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <form onSubmit={handleSubmit}>
      <h2>Edit Device</h2>

      {error && <p role="alert">{error}</p>}

      {["name", "model", "type", "vendor"].map((field) => (
        <div key={field}>
          <label htmlFor={`edit-${field}`}>{field.toUpperCase()}</label>
          <input
            id={`edit-${field}`}
            value={form[field]}
            onChange={(event) =>
              setForm((previous) => ({
                ...previous,
                [field]: event.target.value,
              }))
            }
          />
        </div>
      ))}

      <button type="submit" disabled={saving}>
        {saving ? "Saving..." : "Save Changes"}
      </button>

      <button type="button" onClick={onCancel} disabled={saving}>
        Cancel
      </button>
    </form>
  );
}
