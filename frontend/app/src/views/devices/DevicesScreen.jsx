

import { useEffect, useState } from "react";
import AddDeviceForm from "../../components/devices/AddDeviceForm";
import EditDeviceForm from "../../components/devices/EditDeviceForm";
import DeleteDeviceConfirm from "../../components/devices/DeleteDeviceConfirm";
import DeviceTrustSelector from "../../components/devices/DeviceTrustSelector";
import { api } from "../../components/api";


export default function DevicesScreen() {
  const [view, setView] = useState("list");
 

const [devices, setDevices] = useState([]);


const [selectedDevice, setSelectedDevice] = useState(null);

const [loading, setLoading] = useState(true);
const [loadError, setLoadError] = useState("");

useEffect(() => {
  let active = true;

  async function loadDevices() {
    try {
      const result = await api("/api/devices");

      if (active) {
        setDevices(result.devices);
        setLoadError("");
      }
    } catch (error) {
      if (active) {
        setLoadError(error.message || "Could not load devices.");
      }
    } finally {
      if (active) {
        setLoading(false);
      }
    }
  }

  loadDevices();

  return () => {
    active = false;
  };
}, []);


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

{loading && <p>Loading devices...</p>}

{loadError && (
  <p role="alert">
    Error loading devices: {loadError}
  </p>
)}

      
{view === "list" && (
  <>
    <button type="button" onClick={() => setView("add")}>
      Add Device
    </button>

    <h2>Device List</h2>

{!loading && !loadError && devices.length === 0 && (
  <p>No devices found. Add a device or run a network scan.</p>
)}


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
