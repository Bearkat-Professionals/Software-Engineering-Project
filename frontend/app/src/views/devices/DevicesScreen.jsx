
import { useState } from "react";
import AddDeviceForm from "../../components/devices/AddDeviceForm";
import EditDeviceForm from "../../components/devices/EditDeviceForm";
import DeleteDeviceConfirm from "../../components/devices/DeleteDeviceConfirm";
import DeviceTrustSelector from "../../components/devices/DeviceTrustSelector";

export default function DevicesScreen() {
  const [view, setView] = useState("list");
  const sampleDevice = {
  device_id: 1,
  name: "Test Laptop",
  model: "Latitude",
  type: "Laptop",
  vendor: "Dell",
  mac: "AA:BB:CC:DD:EE:FF",
  loaded_at: "2026-11-11T00:00:00",
};

 

  function closeForm() {
    setView("list");
  }

  async function handleTestAdd(device) {
    console.log("Test device:", device);
    alert(
      `Test device "${device.name || device.mac}" added successfully (simulation only).`
    );
    closeForm();
  }

async function handleTestEdit(deviceId, changes) {
  console.log("Test edit:", deviceId, changes);
  alert("Device updated successfully (simulation only).");
  closeForm();
}

async function handleTestDelete(deviceId, loadedAt) {
  console.log("Test delete:", deviceId, loadedAt);
  alert("Device deleted successfully (simulation only).");
  closeForm();
}

async function handleTestTrust(deviceId, changes) {
  console.log("Test trust/flag:", deviceId, changes);
  alert("Device trust/flag status updated (simulation only).");
  closeForm();
}

  return (
    <main>
      <h1>Devices</h1>

      {view === "list" && (
        <>
          <p>The device list is filled in by a later task.</p>

          <button type="button" onClick={() => setView("add")}>
            Add Device
          </button>
          <button type="button" onClick={() => setView("edit")}>
  Edit Test Device
        </button>
        <button type="button" onClick={() => setView("delete")}>
  Delete Test Device
</button>
<button type="button" onClick={() => setView("trust")}>
  Trust / Flag Test Device
</button>
        </>
      )}

      {view === "add" && (
       
<AddDeviceForm
  onSave={handleTestAdd}
  onCancel={closeForm}
/>

      )}
      {view === "edit" && (
  
<EditDeviceForm
  device={sampleDevice}
  onSave={handleTestEdit}
  onCancel={closeForm}
 />
)}
{view === "delete" && (
 
<DeleteDeviceConfirm
  device={sampleDevice}
  onDelete={handleTestDelete}
  onCancel={closeForm}
/>

)}
{view === "trust" && (
  <>
   
<DeviceTrustSelector
  device={sampleDevice}
  onSave={handleTestTrust}
/>
    <button type="button" onClick={closeForm}>
      Back to Devices
    </button>
  </>
)}
    </main>
  );
}
