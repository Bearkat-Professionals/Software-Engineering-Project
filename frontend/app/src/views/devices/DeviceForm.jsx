import { useCallback, useEffect, useState } from "react";
import { Link, Navigate, useNavigate, useParams } from "react-router-dom";
import { useAuth } from "../../auth/AuthProvider";
import { api } from "../../components/api";
import { formatConfidence, formatLastSeen } from "../../components/format";
import { useMessages } from "../../components/Messages";

const MARKS = [

    { value: "trusted", text: "Never Flagged" },
    { value: "flagged", text: "Stays flagged whatever scans find."},
    { value: "automatic", text: "The flag follows identification. A device that is not fully identified stays flagged until someone trusts it." },

];

function blankFields() {
    return { name: "", model: "", deviceTypeId: "", vendorId: "" };
}

function fieldsFrom(device) {
    return {
        name: device.name ?? "",
        model: device.model ?? "",
        deviceTypeId: device.device_type_id == null ? "" : String(device.device_type_id),
        vendorId: device.vendor_id == null ? "" : String(device.vendor_id),
    };
}

export default function DeviceForm() {

    const { id } = useParams();
    const navigate = useNavigate();
    const { can } = useAuth();
    const canEdit = can("EDIT_DEVICES");
    const { showMessage } = useMessages();
    const creating = id === undefined;
    const [device, setDevice] = useState(null);
    const [loadedAt, setLoadedAt] = useState(null);
    const [fields, setFields] = useState(blankFields);
    const [opened, setOpened] = useState(blankFields);
    const [types, setTypes] = useState([]);
    const [vendorQuery, setVendorQuery] = useState("");
    const [vendorHits, setVendorHits] = useState([]);
    const [mac, setMac] = useState("");
    const [ip, setIp] = useState("");
    const [trusted, setTrusted] = useState(false);
    const [reasons, setReasons] = useState([]);
    const [duplicateId, setDuplicateId] = useState(null);
    const [conflict, setConflict] = useState(null);
    const [confirming, setConfirming] = useState(false);
    const [waiting, setWaiting] = useState(false);

    const clearProblems = useCallback(() => {
        setReasons([]);
        setDuplicateId(null);
        setConflict(null);
    }, []);

    const showDevice = useCallback((data) => {
        const next = fieldsFrom(data.device);

        setDevice(data.device);
        setLoadedAt(data.loaded_at);
        setFields(next);
        setOpened(next);
        setVendorQuery(data.device.vendor ?? "");
        clearProblems();
    }, [clearProblems]);

    const showProblem = useCallback((err) => {
        if (err.status === 404) {
            
            showMessage("error", err.message);
            navigate("/devices");
            return;

        }

        if (err.status === 400) {

            setReasons(err.reasons?.length ? err.reasons : [err.message]);
            return;

        }

        if (err.status === 409 && err.deviceId != null) {

            setDuplicateId(err.deviceId);
            showMessage("error", err.message);
            return;

        }

        if (err.status === 409) {

            setConflict(err.message);
            return;

        }

        showMessage("error", err.message);

    }, [navigate, showMessage]);

    useEffect(() => {
        if (creating) return undefined;
        let ignore = false;

        api(`/api/devices/${id}`)
           .then((data) => {
                if (!ignore) showDevice(data);
           })
           .catch((err) => {
                if (!ignore) showProblem(err);
           });
        return () => { ignore = true };
    }, [creating, id, showDevice, showProblem]);

    useEffect(() => {
        let ignore = false;
        api("/api/device-types")
            .then((data) => {
                if (!ignore) setTypes(data.device_types);
            })
            .catch((err) => {
                if (!ignore) showMessage("error", err.message);
            });
        return () => { ignore = true };
    }, [showMessage]);

    useEffect(() => {
        const text = vendorQuery.trim();
        if (text.length < 2) return undefined;
        const timer = setTimeout(() => {
            const query = new URLSearchParams({ search: text.slice(0, 100), limit: "20" });
            api(`/api/vendors?${query}`)
                .then((data) => setVendorHits(data.vendors))
                .catch((err) => showMessage("error", err.message));
        }, 300);
        return () => clearTimeout(timer);
    }, [vendorQuery, showMessage]);

    function setField(key, value) {
        setFields((current) => ({ ...current, [key]: value }));
    }

    function editBody() {
        const body = { loaded_at: loadedAt };

        if (fields.name !== opened.name) body.name = fields.name;
        if (fields.model !== opened.model) body.model = fields.model;
        if (fields.deviceTypeId !== opened.deviceTypeId && fields.deviceTypeId !== "") {
            body.device_type_id = Number(fields.deviceTypeId);
        }
        if (fields.vendorId !== opened.vendorId && fields.vendorId !== "") {
            body.vendor_id = Number(fields.vendorId);
        }
        return body;
    }

    async function onSave(event) {
        event.preventDefault();
        clearProblems();
        setWaiting(true);

        try {
            if (creating) {
                const body = { mac: mac.trim(), trusted };

                if (fields.name.trim() !== "") body.name = fields.name;
                if (fields.model.trim() !== "") body.model = fields.model;
                if (ip.trim() !== "") body.ip = ip.trim();
                if (fields.deviceTypeId !== "") body.device_type_id = Number(fields.deviceTypeId);
                if (fields.vendorId !== "") body.vendor_id = Number(fields.vendorId);
                const data = await api("/api/devices", { method: "POST", body });
                showMessage("success", "Saved.");
                navigate(`/devices/${data.device.device_id}`, { replace: true });
                return;
            }
            const body = editBody();

            if (Object.keys(body).length === 1) return;
            const data = await api(`/api/devices/${id}`, { method: "PUT", body });
            showDevice(data);
            showMessage("success", "Saved.");

        } catch (err) {
            showProblem(err);
        } finally {
            setWaiting(false);
        }
    }

    async function onMark(mark) {
        if (device?.mark === mark) return;

        clearProblems();
        setWaiting(true);

        try {
            const data = await api(`/api/devices/${id}/mark`, {
                method: "PUT",
                body: { mark, loaded_at: loadedAt },
            });
            showDevice(data);
            showMessage("success", "Saved.");
        } catch (err) {
            showProblem(err);
        } finally {
            setWaiting(false);
        }
    }

    async function reload() {
        const data = await api(`/api/devices/${id}`);
        showDevice(data);
    }

    async function onDelete() {
        setWaiting(true);
        
        try {
            await api(`/api/devices/${id}?loaded_at=${encodeURIComponent(loadedAt)}`, {
                method: "DELETE",
                body: {},
              });
              showMessage("success", "Deleted.");
              navigate("/devices");
        } catch (err) {
            showProblem(err);
        } finally {
            setWaiting(false);
        }
    }

    if (!canEdit && creating) return <Navigate to="/devices" replace />;

    const typeOptions = types.slice();
    if (device?.device_type_id != null && !typeOptions.some((type) => type.device_type_id === device.device_type_id)) {
        typeOptions.push({ device_type_id: device.device_type_id, name: device.type || "Current type" });
      }
      const shownName = device?.name || "This device";

      return (

        <main>

          <p><Link to="/devices">Back to the list</Link></p>
          <h1>{creating ? "Add device" : shownName}</h1>

          {!creating && device == null && <p>Loading...</p>}
          {(creating || device) && (
            
            <form onSubmit={onSave}>
              {creating && (
                <label>
                  MAC
                  <input value={mac} onChange={(event) => setMac(event.target.value)} />
                </label>
              )}
              {canEdit ? (
                <>
                  <label>
                    Name
                    <input value={fields.name} onChange={(event) => setField("name", event.target.value)} />
                  </label>
                  <label>
                    Model
                    <input value={fields.model} onChange={(event) => setField("model", event.target.value)} />
                  </label>
                  <label>
                    Device type
                    <select value={fields.deviceTypeId} onChange={(event) => setField("deviceTypeId", event.target.value)}>
                      <option value="">Choose a device type</option>
                      {typeOptions.map((type) => (
                        <option key={type.device_type_id} value={type.device_type_id}>{type.name}</option>
                      ))}
                    </select>
                  </label>
                  <label>
                    Vendor
                    <input value={vendorQuery} onChange={(event) => setVendorQuery(event.target.value)} />
                  </label>
                  <ul>
                    {vendorHits.map((hit) => (
                      <li key={hit.vendor_id}>
                        <button type="button" onClick={() => { setField("vendorId", String(hit.vendor_id)); setVendorQuery(hit.name); }}>
                          {hit.name}
                        </button>
                      </li>
                    ))}
                  </ul>
                  {creating && (
                    <>
                      <label>
                        IP
                        <input value={ip} onChange={(event) => setIp(event.target.value)} />
                      </label>
                      <label>
                        <input type="checkbox" checked={trusted} onChange={(event) => setTrusted(event.target.checked)} />
                        Trusted
                      </label>
                    </>
                  )}
                </>
              ) : (
                <>
                  <p>{device.name ?? ""}</p>
                  <p>{device.model ?? ""}</p>
                  <p>{device.type}</p>
                  <p>{device.vendor}</p>
                  <p>{device.mark}</p>
                </>
              )}
              {reasons.length > 0 && (
                <ul>
                  {reasons.map((reason) => <li key={reason}>{reason}</li>)}
                </ul>
              )}
              {duplicateId != null && (
                <p><Link to={`/devices/${duplicateId}`}>Open the existing device</Link></p>
              )}
              {conflict && (
                <p>
                  {conflict}
                  <button type="button" onClick={reload}>Reload</button>
                </p>
              )}
              {canEdit && <button type="submit" disabled={waiting}>Save</button>}
            </form>
          )}
          {!creating && device && (
            <section>
              <p>Scans fill these in.</p>
              <p>MAC {device.mac}{device.is_randomized_mac ? " randomized" : ""}</p>
              <p>IP {device.ip ?? ""}</p>
              <p>Hostname {device.hostname ?? ""}</p>
              <p>First seen {formatLastSeen(device.first_seen)}</p>
              <p>Last seen {formatLastSeen(device.last_seen)}</p>
              <p>Confidence {formatConfidence(device.confidence)}</p>
            </section>
          )}
          {!creating && device && canEdit && (
            <section>
              <h2>Trust and flag</h2>
              {MARKS.map((item) => (
                <label key={item.value}>
                  <input
                    type="radio"
                    name="mark"
                    checked={device.mark === item.value}
                    onChange={() => onMark(item.value)}
                  />
                  {item.value}. {item.text}
                </label>
              ))}
            </section>
          )}
          {!creating && device && canEdit && (
            <section>
              {confirming ? (
                <>
                  <p>
                    Delete {shownName}? Its scan history and evidence go with it.
                    A scan may add it again if it is still on the network.
                  </p>
                  <button type="button" onClick={onDelete} disabled={waiting}>Delete</button>
                  <button type="button" onClick={() => setConfirming(false)}>Cancel</button>
                </>
              ) : (
                <button type="button" onClick={() => setConfirming(true)}>Delete</button>
              )}
            </section>
          )}
        </main>

      );
}