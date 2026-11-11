
import { useState } from "react";

export default function DeleteDeviceConfirm({
  device,
  onDelete,
  onCancel,
}) {
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState("");

  async function handleDelete() {
    setError("");

    if (typeof onDelete !== "function") {
      setError("Deleting is not connected yet.");
      return;
    }

    setDeleting(true);

    try {
      await onDelete(device.device_id, device.loaded_at);
    } catch (err) {
      setError(err.message || "Unable to delete device.");
    } finally {
      setDeleting(false);
    }
  }

  return (
    <section aria-labelledby="delete-device-title">
      <h2 id="delete-device-title">Delete Device?</h2>

      <p>
        Are you sure you want to delete{" "}
        <strong>{device.name || device.mac || "this device"}</strong>?
      </p>

      <p>
        Its scan history and evidence will also be deleted.
        If the device is still on the network, a future scan
        may add it again.
      </p>

      {error && <p role="alert">{error}</p>}

      <button
        type="button"
        onClick={handleDelete}
        disabled={deleting}
      >
        {deleting ? "Deleting..." : "Yes, Delete Device"}
      </button>

      <button
        type="button"
        onClick={onCancel}
        disabled={deleting}
      >
        Cancel
      </button>
    </section>
  );
}
