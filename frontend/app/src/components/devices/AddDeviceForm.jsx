
import { useState } from "react";

export default function AddDeviceForm({ onSave, onCancel }) {
  const [device, setDevice] = useState({
    mac: "",
    name: "",
    type: "",
    vendor: "",
    model: "",
    ip: "",
    trusted: false,
  });

  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);

  function handleChange(event) {
    const { name, value, type, checked } = event.target;

    setDevice((previous) => ({
      ...previous,
      [name]: type === "checkbox" ? checked : value,
    }));
  }

  async function handleSubmit(event) {
    event.preventDefault();
    setError("");

    if (!device.mac.trim()) {
      setError("MAC address is required.");
      return;
    }

    if (typeof onSave !== "function") {
      setError("Saving is not connected yet.");
      return;
    }

    setSaving(true);

    try {
      await onSave(device);
    } catch (err) {
      setError(err.message || "Unable to save device.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <form onSubmit={handleSubmit}>
      <h2>Add Device</h2>

      {error && <p role="alert">{error}</p>}

      {["mac", "name", "type", "vendor", "model", "ip"].map(
        (field) => (
          <div key={field}>
            <label htmlFor={`device-${field}`}>
              {field.toUpperCase()}
            </label>
            <input
              id={`device-${field}`}
              name={field}
              value={device[field]}
              onChange={handleChange}
              required={field === "mac"}
            />
          </div>
        )
      )}

      <label>
        <input
          type="checkbox"
          name="trusted"
          checked={device.trusted}
          onChange={handleChange}
        />
        Trusted device
      </label>

      <div>
        <button type="submit" disabled={saving}>
          {saving ? "Saving..." : "Add Device"}
        </button>

        <button type="button" onClick={onCancel} disabled={saving}>
          Cancel
        </button>
      </div>
    </form>
  );
}
