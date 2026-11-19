// Change report imports
import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useAuth } from "../../auth/AuthProvider";
import { api } from "../../components/api";
import { downloadFile } from "../../components/download";
import { displayName, formatLastSeen } from "../../components/format";
import { useMessages } from "../../components/Messages";

const SCANS_URL = "/api/scans";
const CHANGES_URL = "/api/reports/changes";
const EXPORT_SCAN_URL = "/api/export/scan";

// when the scan starts, its range, and whether it was partial.
function scanLabel(scan) {

    return `${formatLastSeen(scan.started_at)}, ${scan.scope}${scan.outcome === "partial" ? " (partial)" : ""}`;

}

// "old → new"
function arrow(before, after) {

    const from = before || "none";
    const to = after || "none";

    return from.toLowerCase() === to.toLowerCase() ? (after || "") : `${from} → ${to}`;

}

function ipText(kind, device) {

    if (kind === "new") return device.new_ip ?? "";
    if (kind === "missing") return device.old_ip ?? "";

    return arrow(device.old_ip, device.new_ip);

}

function outsideText(count) {

    return count === 1
       ? "1 device wasn't in this scan's range."
       : `${count} devices weren't in this scan's range.`
}

// Device values are text
function DeviceTable({ kind, title, devices }) {

    return (
        <section>
            <h2>{title} ({devices.length})</h2>

            {devices.length === 0 ? (
                <p>None.</p>
            ) : (
                <table>
                    <caption>{title} devices</caption>
                    <thead>
                        <tr>
                            <th scope="col">Name</th>
                            <th scope="col">Type</th>
                            <th scope="col">Vendor</th>
                            <th scope="col">IP</th>
                            <th scope="col">MAC</th>
                            {kind === "new" && <th scope="col">Seen</th>}
                            {kind === "changed" && <th scope="col">Hostname</th>}
                        </tr>
                    </thead>
                    <tbody>
                        {devices.map((device) => (
                            <tr key={device.device_id}>
                                <td>{displayName(device)}</td>
                                <td>{device.type || "unknown"}</td>
                                <td>{device.vendor || "unknown"}</td>
                                <td>{ipText(kind, device)}</td>
                                <td>{device.mac}{device.is_randomized_mac ? " (randomized MAC)" : ""}</td>
                                {kind === "new" && <td>{device.first_seen_in_this_scan ? "first seen" : "back again"}</td>}
                                {kind === "changed" && <td>{arrow(device.old_hostname, device.new_hostname)}</td>}
                            </tr>
                        ))}
                    </tbody>
                </table>
            )}
        </section>
    );
}

export default function ChangeReport() {

    const { can } = useAuth();
    const { showMessage} = useMessages();
    const [params, setParams] = useSearchParams();
    const [scans, setScans] = useState(null);
    const [scansFailed, setScansFailed] = useState(false);
    const [result, setResult] = useState(null);
    const [exporting, setExporting] = useState(false);
    const enough = scans != null && scans.length >= 2;
    const fromId = params.get("from") ?? (enough ? String(scans[1].scan_id) : "");
    const toId = params.get("to") ?? (enough ? String(scans[0].scan_id) : "");
    const key = `${fromId}-${toId}`;
    const report = result?.key === key ? result.report : null;
    const loading = enough && result?.key !== key;

    // Finished scans fill dropdowns
    useEffect(() =>{

        let ignore = false;

        api(SCANS_URL)
          .then((data) => {
            if (!ignore) setScans(data.scans);
          })
          .catch((err) => {

            if (ignore) return;

            showMessage("error", err.message);
            setScansFailed(true);

          });

          return () => { ignore = true };
    }, [showMessage]);

    useEffect(() => {

        if (!enough) return undefined;

        let ignore = false;

        const query = new URLSearchParams({ from: fromId, to: toId});

        api(`${CHANGES_URL}?${query}`)
          .then((data) => {
            if (!ignore) setResult({ key, report: data });
          })
           .catch((err) =>{
            if (ignore) return;

            showMessage("error", err.reasons?.length ? err.reasons.join(" ") : err.message);
            setResult({ key, report: null });
           });
        return () => { ignore = true };
    }, [enough, fromId, toId, key, showMessage]);

    function choose(name, value) {

        setParams((current) => {

            const next = new URLSearchParams(current);

            next.set("from", fromId);
            next.set("to", toId);
            next.set(name, value);

            return next;

        });
    }

    async function onExport() {

        setExporting(true);

        try {
            await downloadFile(`${EXPORT_SCAN_URL}/${encodeURIComponent(toId)}`, `scan-${toId}.json`);
        } catch (err) {
            showMessage("error", err.message);
        } finally {
            setExporting(false);
        }
    }

    if (scansFailed) return <main><h1>Changes</h1><p>The scans could not be loaded.</p></main>;
    if (scans == null) return <main><h1>Changes</h1><p>Loading...</p></main>;

    if (!enough) {
        return (
            <main>
                <h1>Changes</h1>
                <p>Run at least two scans to compare them. <Link to="/scans">Start a scan</Link></p>
            </main>
        );
    }

    const listed = (id) => scans.some((scan) => String(scan.scan_id) === id);

    return (
        <main>
            <h1>Changes</h1>
            <label>
                Compare
                <select value={fromId} onChange={(event) => choose("from", event.target.value)}>
                    {!listed(fromId) && <option value={fromId}>Scan {fromId}</option>}
                    {scans.map((scan) => <option key={scan.scan_id} value={scan.scan_id}>{scanLabel(scan)}</option>)}
                </select>
            </label>

            <label>
                with
                <select value={toId} onChange={(event) => choose("to", event.target.value)}>
                    {!listed(toId) && <option value={toId}>Scan {toId}</option>}
                    {scans.map((scan) => <option key={scan.scan_id} value={scan.scan_id}>{scanLabel(scan)}</option>)}
                </select>
            </label>

            {/* any role can export. Button hides when perms are missing. */}
            {can("EXPORT") && (
                <p><button type="button" onClick={onExport} disabled={exporting}>Export this scan</button></p>
            )}

            {loading && <p>Loading...</p>}

            {report?.partial_warning && (
                <p>At least one scan was partial. Results may be incomplete.</p>
            )}

            {report && report.outside_scope > 0 && <p>{outsideText(report.outside_scope)}</p>}

            {report && (
                <>
                  <DeviceTable kind="new" title="New" devices={report.new} />
                  <DeviceTable kind="missing" title="Missing" devices={report.missing} />
                  <DeviceTable kind="changed" title="Changed" devices={report.changed} />
                </>
            )}
        </main>
    );
}