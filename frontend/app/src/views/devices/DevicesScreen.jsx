
import { useState } from "react";
import AddDeviceForm from "../../components/devices/AddDeviceForm";
import EditDeviceForm from "../../components/devices/EditDeviceForm";
import DeleteDeviceConfirm from "../../components/devices/DeleteDeviceConfirm";
import DeviceTrustSelector from "../../components/devices/DeviceTrustSelector";

export default function DevicesScreen() {
  const [view, setView] = useState("list");
 
const [devices, setDevices] = useState([
  {
    device_id: 1,
    name: "Test Laptop",
    model: "Latitude",
    type: "Laptop",
    vendor: "Dell",
    mac: "AA:BB:CC:DD:EE:FF",
    loaded_at: "2026-11-11T00:00:00",
    trusted: true,
  },
  {
    device_id: 2,
    name: "Office Printer",
    model: "LaserJet",
    type: "Printer",
    vendor: "HP",
    mac: "11:22:33:44:55:66",
    loaded_at: "2026-11-11T00:00:00",
    flagged: false,
  },
]);

const [selectedDevice, setSelectedDevice] = useState(null);


 

  function closeForm() {
    setView("list");
  }

  
async function handleTestAdd(device) {
  setDevices((currentDevices) => [
    ...currentDevices,
    {
      ...device,
      device_id: Date.now(),
      loaded_at: new Date().toISOString(),
    },
  ]);

  alert("Device added successfully (simulation only).");
  closeForm();
}



async function handleTestEdit(deviceId, changes) {
  setDevices((currentDevices) =>
    currentDevices.map((device) =>
      device.device_id === deviceId
        ? { ...device, ...changes }
        : device
    )
  );

  alert("Device updated successfully (simulation only).");
  closeForm();
}


async function handleTestDelete(deviceId, loadedAt) {
  setDevices((currentDevices) =>
    currentDevices.filter(
      (device) => device.device_id !== deviceId
    )
  );

  alert("Device deleted successfully (simulation only).");
  closeForm();
}



async function handleTestTrust(deviceId, changes) {
  setDevices((currentDevices) =>
    currentDevices.map((device) => {
      if (device.device_id !== deviceId) {
        return device;
      }

      return {
        ...device,
        trusted: changes.status === "trusted",
        flagged: changes.status === "flagged",
      };
    })
  );

  alert("Device trust/flag status updated (simulation only).");
  closeForm();
}

  return (
    <main>
      <h1>Devices</h1>

      
{view === "list" && (
  <>
    <button type="button" onClick={() => setView("add")}>
      Add Device
    </button>

    <h2>Device List</h2>

    {devices.map((device) => (
      <div key={device.device_id}>
        <p>
          <strong>{device.name || device.mac}</strong>
          {" — "}
          {device.vendor}
          {" — "}
          {device.type}
          {" — "}
          {device.trusted
           ? "Trusted"
           : device.flagged
             ? "Flagged"
            : "Automatic"}

        </p>

        <button
          type="button"
          onClick={() => {
            setSelectedDevice(device);
            setView("edit");
          }}
        >
          Edit
        </button>

        <button
          type="button"
          onClick={() => {
            setSelectedDevice(device);
            setView("delete");
          }}
        >
          Delete
        </button>

        <button
          type="button"
          onClick={() => {
            setSelectedDevice(device);
            setView("trust");
          }}
        >
          Trust / Flag
        </button>
      </div>
    ))}
  </>
)}

      {view === "add" && (
       
<AddDeviceForm
  onSave={handleTestAdd}
  onCancel={closeForm}
/>

      )}
      {view === "edit" && selectedDevice && (
  
<EditDeviceForm
  device={selectedDevice}
  onSave={handleTestEdit}
  onCancel={closeForm}
 />
)}
{view === "delete" && selectedDevice && (
 
<DeleteDeviceConfirm
  device={selectedDevice}
  onDelete={handleTestDelete}
  onCancel={closeForm}
/>

)}
{view === "trust" && selectedDevice && (
  <>
   
<DeviceTrustSelector
  device={selectedDevice}
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
