// Adds or edits one reference record. Edits send loaded_at so a conflicting change is refused.
import { useCallback, useEffect, useState } from "react";
import { Link, Navigate, useNavigate, useParams } from "react-router-dom";
import { useAuth } from "../../auth/AuthProvider";
import { api } from "../../components/api";
import { useMessages } from "../../components/Messages";
import { AWAY_MESSAGE, holdNotice, useEditHold } from "../../components/useEditHold";

// oui is not a signature type. Prefixes belong to a vendor.
const EVIDENCE_TYPES = ["mdns", "ssdp", "netbios_llmnr", "reverse_dns"];
const SAVED = "Saved. Scans use this from the next scan.";
const HOLD_TYPES = { vendors: "vendor", "device-types": "device_type", signatures: "device_signature" };

// Empty values for an add form.
function blankFields() {

  return {

    name: "",
    evidenceType: "mdns",
    pattern: "",
    deviceTypeId: "",
    resultingName: "",
    resultingModel: "",

  };

}

// Copy a loaded record into the form. Empty optional text stays an empty string.
function fieldsFrom(record) {

  return {

    name: record.name ?? "",
    evidenceType: record.evidence_type ?? "mdns",
    pattern: record.pattern ?? "",
    deviceTypeId: record.device_type_id == null ? "" : String(record.device_type_id),
    resultingName: record.resulting_device_name ?? "",
    resultingModel: record.resulting_model ?? "",

  };

}

export default function ReferenceForm() {

  const { kind, id } = useParams();
  const navigate = useNavigate();
  const { can } = useAuth();
  const canEdit = can("EDIT_REFERENCE_DATA");
  const { showMessage } = useMessages();
  // The add route uses the id "new". Any other id is an existing record.
  const creating = id === "new";
  const { others, lost } = useEditHold(HOLD_TYPES[kind], canEdit && !creating ? Number(id) : null);
  const [record, setRecord] = useState(null);
  const [loadedAt, setLoadedAt] = useState(null);
  const [usageText, setUsageText] = useState("");
  const [prefixes, setPrefixes] = useState([]);
  const [fields, setFields] = useState(blankFields);
  // opened is the record as it was loaded, so an edit can send only the fields that changed.
  const [opened, setOpened] = useState(blankFields);
  const [types, setTypes] = useState([]);
  const [reasons, setReasons] = useState([]);
  const [duplicateId, setDuplicateId] = useState(null);
  const [conflict, setConflict] = useState(null);
  const [prefixText, setPrefixText] = useState("");
  const [moving, setMoving] = useState(null);
  const [vendorQuery, setVendorQuery] = useState("");
  const [vendorHits, setVendorHits] = useState([]);
  const [waiting, setWaiting] = useState(false);

  // Clear the last 400, duplicate, and conflict before the next attempt.
  const clearProblems = useCallback(() => {

    setReasons([]);
    setDuplicateId(null);
    setConflict(null);

  }, []);

  // Replace the form with the server's reply, including the new loaded_at.
  const showDetail = useCallback((data) => {

    const next = fieldsFrom(data.record);

    setRecord(data.record);
    setLoadedAt(data.loaded_at);
    setUsageText(data.usage_text ?? "");
    setPrefixes(data.prefixes ?? []);
    setFields(next);
    setOpened(next);
    clearProblems();

  }, [clearProblems]);

  // 400 stays on the form. 409 is a duplicate or a conflict. 404 returns to the list.
  const showProblem = useCallback((err) => {
    
    if (err.status === 404) {

      showMessage("error", err.message);
      navigate(`/reference/${kind}`);

      return;

    }

    if (err.status === 400) {

      setReasons(err.reasons?.length ? err.reasons : [err.message]);

      return;

    }

    if (err.status === 409 && err.existingId != null) {

      setDuplicateId(err.existingId);
      showMessage("error", err.message);

      return;

    }

    if (err.status === 409) {

      setConflict(err.message);

      return;

    }

    showMessage("error", err.message);

  }, [kind, navigate, showMessage]);

  useEffect(() => {

    // Load the record being edited. A new form has nothing to load.
    if (creating) return undefined;

    let ignore = false;

    api(`/api/reference/${kind}/${id}`)

      .then((data) => {

        if (!ignore) showDetail(data);

      })

      .catch((err) => {

        if (!ignore) showProblem(err);

      });

    return () => {

      ignore = true;

    };

  }, [creating, kind, id, showDetail, showProblem]);

  useEffect(() => {

    // The device-type picker offers active types only.
    if (kind !== "signatures") return undefined;

    let ignore = false;

    api("/api/reference/device-types?status=active&per_page=200")

      .then((data) => {

        if (!ignore) setTypes(data.records);

      })

      .catch((err) => {

        if (!ignore) showMessage("error", err.message);

      });

    return () => {

      ignore = true;

    };

  }, [kind, showMessage]);

  useEffect(() => {

    // Search vendors after a short pause while moving a prefix.
    if (moving == null || vendorQuery.trim() === "") return undefined;

    const timer = setTimeout(() => {

      const query = new URLSearchParams({

        q: vendorQuery.trim().slice(0, 100),
        status: "active",
        per_page: "20",

      });

      api(`/api/reference/vendors?${query}`)

        .then((data) => setVendorHits(data.records))
        .catch((err) => showMessage("error", err.message));

    }, 300);

    return () => clearTimeout(timer);

  }, [moving, vendorQuery, showMessage]);

  function setField(key, value) {

    setFields((current) => ({ ...current, [key]: value }));

  }

  // Send loaded_at plus only the fields that differ from the opened record.
  function editBody() {

    const body = { loaded_at: loadedAt };

    if (kind === "signatures") {

      if (fields.evidenceType !== opened.evidenceType) body.evidence_type = fields.evidenceType;

      if (fields.pattern !== opened.pattern) body.pattern = fields.pattern;

      if (fields.deviceTypeId !== opened.deviceTypeId) body.device_type_id = Number(fields.deviceTypeId);

      if (fields.resultingName !== opened.resultingName) {

        body.resulting_device_name = fields.resultingName.trim() === "" ? null : fields.resultingName;
      }

      if (fields.resultingModel !== opened.resultingModel) {

        body.resulting_model = fields.resultingModel.trim() === "" ? null : fields.resultingModel;
      }

    } else if (fields.name !== opened.name) {

      body.name = fields.name;

    }

    return body;

  }

  // Create the record, or save the edit and keep the returned loaded_at.
  async function onSave(event) {

    event.preventDefault();
    clearProblems();
    setWaiting(true);

    try {
        
      if (creating) {

        const body = kind === "signatures"

          ? {

            evidence_type: fields.evidenceType,
            pattern: fields.pattern,
            device_type_id: Number(fields.deviceTypeId),
            resulting_device_name: fields.resultingName.trim() === "" ? null : fields.resultingName,
            resulting_model: fields.resultingModel.trim() === "" ? null : fields.resultingModel,

          }

          : { name: fields.name };
          
        const data = await api(`/api/reference/${kind}`, { method: "POST", body });

        showMessage("success", SAVED);
        navigate(`/reference/${kind}/${data.record.id}`, { replace: true });

        return;

      }

      const data = await api(`/api/reference/${kind}/${id}`, { method: "PUT", body: editBody() });

      showDetail(data);
      showMessage("success", SAVED);

    } catch (err) {

      showProblem(err);

    } finally {

      setWaiting(false);

    }

  }

  // Used by conflict Reload and after a prefix change, so the form matches the database.
  async function reload() {

    const data = await api(`/api/reference/${kind}/${id}`);

    showDetail(data);

  }

  // Any common prefix spelling is accepted. The server stores it in one form.
  async function onAddPrefix(event) {

    event.preventDefault();
    clearProblems();
    setWaiting(true);

    try {

      await api(`/api/reference/vendors/${id}/prefixes`, {

        method: "POST",
        body: { prefix: prefixText },

      });

      setPrefixText("");
      await reload();
      showMessage("success", SAVED);

    } catch (err) {

      showProblem(err);

    } finally {

      setWaiting(false);

    }

  }

  // Prefixes are moved, not deleted. The move uses this vendor's loaded_at.
  async function onMove(vendorId) {

    clearProblems();
    setWaiting(true);

    try {

      await api(`/api/reference/prefixes/${encodeURIComponent(moving)}`, {

        method: "PUT",
        body: { vendor_id: vendorId, loaded_at: loadedAt },

      });

      setMoving(null);
      setVendorQuery("");
      setVendorHits([]);
      await reload();
      showMessage("success", SAVED);

    } catch (err) {

      showProblem(err);

    } finally {

      setWaiting(false);

    }

  }

  // A viewer who opens this address goes back to the list.
  if (!canEdit) return <Navigate to={`/reference/${kind}`} replace />;

  // The built-in unknown vendor and type are shown, but they have no save or prefix controls.
  const builtin = !creating && kind !== "signatures" && record?.name?.toLowerCase() === "unknown";
  const typeOptions = types.slice();

  if (record?.device_type_id != null && !typeOptions.some((type) => type.id === record.device_type_id)) {

    typeOptions.push({ id: record.device_type_id, name: record.device_type || "Current type" });

  }

  return (

    <main>

      <p><Link to={`/reference/${kind}`}>Back to the list</Link></p>

      <h1>{creating ? "Add" : "Edit"}</h1>

      {others.map((other) => <p key={`${other.username}-${other.since}`}>{holdNotice(other)}</p>)}

      {lost && <p>{AWAY_MESSAGE}</p>}

      {!creating && record == null && <p>Loading...</p>}

      {builtin && (

        <>

          <p>{record.name}</p>
          <p>{usageText}</p>

        </>

      )}

      {(creating || (record && !builtin)) && (

        <form onSubmit={onSave}>

          {kind === "signatures" ? (

            <>

              <label>

                Evidence type

                <select value={fields.evidenceType} onChange={(event) => setField("evidenceType", event.target.value)}>

                  {EVIDENCE_TYPES.map((type) => <option key={type} value={type}>{type}</option>)}

                </select>

              </label>

              <label>

                Pattern

                <input value={fields.pattern} onChange={(event) => setField("pattern", event.target.value)} />

              </label>

              <p>The pattern is matched anywhere in the value, ignoring case.</p>

              <label>

                Device type

                <select value={fields.deviceTypeId} onChange={(event) => setField("deviceTypeId", event.target.value)}>

                  <option value="">Choose a device type</option>

                  {typeOptions.map((type) => (

                    <option key={type.id} value={type.id}>{type.name}</option>

                  ))}

                </select>

              </label>

              <label>

                Resulting name

                <input value={fields.resultingName} onChange={(event) => setField("resultingName", event.target.value)} />

              </label>

              <label>

                Resulting model

                <input value={fields.resultingModel} onChange={(event) => setField("resultingModel", event.target.value)} />

              </label>

            </>

          ) : (

            <label>

              Name

              <input value={fields.name} onChange={(event) => setField("name", event.target.value)} />

            </label>

          )}

          {/* Every 400 reason is shown beside the form, as text. */}
          {reasons.length > 0 && (

            <ul>

              {reasons.map((reason) => <li key={reason}>{reason}</li>)}

            </ul>

          )}

          {/* A duplicate 409 links to the record that already has this name, pattern, or prefix. */}
          {duplicateId != null && (

            <p><Link to={`/reference/${kind}/${duplicateId}`}>Open the existing record</Link></p>

          )}

          {/* The sentence names who changed the record and when. Reload shows their version. */}
          {conflict && (

            <p>

              {conflict}

              <button type="button" onClick={reload}>Reload</button>

            </p>

          )}

          {!creating && <p>{usageText}</p>}

          <button type="submit" disabled={waiting}>Save</button>

        </form>

      )}

      {!creating && kind === "vendors" && record && !builtin && (

        <section>

          {/* Prefixes are listed only on an existing vendor. */}
          <h2>OUI prefixes</h2>

          <ul>

            {prefixes.map((prefix) => (

              <li key={prefix}>

                {prefix}

                <button type="button" onClick={() => { setMoving(prefix); setVendorQuery(""); setVendorHits([]); }}>

                  Move to another vendor

                </button>

              </li>

            ))}

          </ul>

          {moving && (

            <div>

              <p>Move {moving}</p>

              <label>

                Vendor

                <input value={vendorQuery} onChange={(event) => setVendorQuery(event.target.value)} />

              </label>

              <ul>

                {vendorHits.filter((hit) => hit.id !== record.id).map((hit) => (

                  <li key={hit.id}>

                    <button type="button" onClick={() => onMove(hit.id)}>{hit.name}</button>

                  </li>

                ))}

              </ul>

            </div>

          )}

          <form onSubmit={onAddPrefix}>

            <label>

              Add prefix

              <input value={prefixText} onChange={(event) => setPrefixText(event.target.value)} />

            </label>

            <button type="submit" disabled={waiting}>Add prefix</button>

          </form>

        </section>

      )}

    </main>

  );
  
}