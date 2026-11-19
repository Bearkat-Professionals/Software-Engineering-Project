// Device: fields, how its score is made up, the evidence that lost a conflict, and its scan history

import { useEffect, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { api } from "../../components/api";
import { displayName, formatConfidence, formatLastSeen } from "../../components/format";
import { useMessages } from "../../components/Messages";
import { useAuth } from "../../auth/AuthProvider";

const HISTORY_PER_PAGE = 20;

// Api evidence
const EVIDENCE_TEXT = {

    oui: "MAC prefix (OUI)",
    mdns: "mDNS",
    ssdp: "SSDP",
    netbios_llmnr: "NetBIOS / LLMNR",
    reverse_dns: "Reverse DNS",

};

// A missing or invalid history page in the URL is treated as page 1
function readPage(params) {

    const parsed = Number(params.get("page"));
    return Number.isInteger(parsed) && parsed > 0 ? parsed : 1;

}

// What was seen, and it's signature match
function evidenceText(item) {

    const matched = [item.signature?.device_name, item.signature?.model].filter(Boolean).join(", ");
    return matched === "" ? item.raw_value : `${item.raw_value} (matches ${matched})`;

}

// Only a value evidence worked out gets a tag
function Field({ label, inferred = false, children }) {

    return (
        <>
          <dt>{label}</dt>
          <dd>
            {children}
            {inferred && <span className="tag" title="worked out from scan evidence">inferred</span>}
          </dd>
        </>
    );
}

export default function DeviceDetail() {

    const { id } = useParams();
    const { showMessage } = useMessages();
    const { can } = useAuth();
    const [params, setParams] = useSearchParams();
    const page = readPage(params);
    // Each result remembers the id it belongs to
    const [loaded, setLoaded] = useState(null);
    const [history, setHistory] = useState(null);
    const current = loaded?.id === id ? loaded : null;
    const data = current?.data ?? null;
    const observations = history?.id === id ? history.data : null;

    useEffect(() => {

        let ignore = false;

        api(`/api/devices/${encodeURIComponent(id)}`)
          .then((found) => { if (!ignore) setLoaded({ id, data: found }); })
          .catch((err) => {

            if (ignore) return;

            // a 404 is shown on page. Anything else goes to the message area.
            if (err.status !== 404) showMessage("error", err.message);

            setLoaded({ id, missing: err.status === 404 });
          });
          return () => { ignore = true; };

    }, [id, showMessage]);

    useEffect(() => {

        let ignore = false;

        api(`/api/devices/${encodeURIComponent(id)}/history?page=${page}&per_page=${HISTORY_PER_PAGE}`)
          .then((found) => {

            if (ignore) return;

            const last = Math.max(1, Math.ceil(found.total / HISTORY_PER_PAGE));

            // A page past the end is rewritten to the last real page.
            if (found.total > 0 && page > last) {

                setParams({ page: String(last) }, { replace: true });
                return;

            }

            setHistory({ id, data: found });

          })
          .catch((err) => {
            // Device request already reports 404, so it's not repeated
            if (!ignore && err.status !== 404) showMessage("error", err.message);
          });

          return () => { ignore = true; };
    }, [id, page, setParams, showMessage]);

    if (current == null) return <main><p>Loading...</p></main>;

    if(data == null) {

        return (
            <main>
                <h1>Device</h1>
                <p>
                    {current.missing
                        ? "This device no longer exists. It may have been deleted."
                        : "This device could not be loaded."
                    }
                </p>
                <p><Link to="/devices">Back to device list</Link></p>
            </main>
        );
    }

    const { device, inferred, score, conflicting } = data;
    const total = observations?.total ?? 0;
    const start = total === 0 ? 0 : (page - 1) * HISTORY_PER_PAGE + 1;
    const end = Math.min(page * HISTORY_PER_PAGE, total);

    return (

        <main>

          <p><Link to="/devices">Back to the device list</Link></p>

          {/* The name is text. */}
          <h1>{displayName(device)}</h1>

          {can("EDIT_DEVICES") && <p><Link to={`/devices/${id}/edit`}>Edit</Link></p>}

          <section>

            <h2>Details</h2>

            <dl>
              <Field label="Name" inferred={inferred.includes("device_name")}>{displayName(device)}</Field>
              <Field label="Type" inferred={inferred.includes("device_type")}>{device.type || "unknown"}</Field>
              <Field label="Vendor" inferred={inferred.includes("vendor")}>{device.vendor || "unknown"}</Field>
              <Field label="Model" inferred={inferred.includes("model")}>{device.model ?? ""}</Field>
              <Field label="IP">{device.ip ?? ""}</Field>
              <Field label="Hostname">{device.hostname ?? ""}</Field>
              <Field label="MAC">{device.mac}{device.is_randomized_mac ? " randomized" : ""}</Field>
              <Field label="Status">{device.network_status === "online" ? "Online" : "Offline"}</Field>
              <Field label="Flag">
                {[device.flagged ? "Suspicious" : "", device.trusted ? "Trusted" : ""].filter(Boolean).join(", ")}
              </Field>
              <Field label="First seen">{formatLastSeen(device.first_seen)}</Field>
              <Field label="Last seen">{formatLastSeen(device.last_seen)}</Field>
            </dl>

          </section>

          <section>

            <h2>Confidence score</h2>

            <p>Stored score: {formatConfidence(score.stored)}</p>

            {score.outdated && (
              <p>
                Today's evidence gives {formatConfidence(score.calculated)}.
                The stored score will update on the next scan.
              </p>
            )}

            {score.contributions.length === 0 ? (

              <p>No evidence counts toward the score.</p>

            ) : (

              <table>

                <caption>Evidence that counts toward the score</caption>

                <thead>
                  <tr>
                    <th scope="col">Evidence type</th>
                    <th scope="col">Evidence</th>
                    <th scope="col">Points</th>
                  </tr>
                </thead>

                <tbody>
                  {/* Each evidence type counts once. */}
                  {score.contributions.map((part) => (
                    <tr key={part.evidence_type}>
                      <td>{EVIDENCE_TEXT[part.evidence_type] ?? part.evidence_type}</td>
                      <td>
                        <ul>
                          {part.evidence.map((item, index) => <li key={index}>{evidenceText(item)}</li>)}
                        </ul>
                      </td>
                      <td>{part.points}</td>
                    </tr>
                  ))}
                </tbody>

                <tfoot>
                  <tr>
                    <th scope="row" colSpan={2}>Total{score.capped ? " (capped at 100)" : ""}</th>
                    <td>{score.calculated}</td>
                  </tr>
                </tfoot>

              </table>

            )}

          </section>

          <section>

            <h2>Conflicting evidence</h2>

            {conflicting.length === 0 ? (

              <p>None.</p>

            ) : (

              <>
                <p>This evidence was not counted because it pointed to a different device type.</p>
                <ul>
                  {conflicting.map((item, index) => (
                    <li key={index}>
                      {EVIDENCE_TEXT[item.evidence_type] ?? item.evidence_type}: {evidenceText(item)}
                    </li>
                  ))}
                </ul>
              </>

            )}

          </section>

          <section>

            <h2>Scan history</h2>

            {observations == null && <p>Loading...</p>}

            {observations != null && total === 0 && <p>No scan has seen this device yet.</p>}

            {total > 0 && (

              <>

                <table>

                  <caption>Where and when scans saw this device, newest first</caption>

                  <thead>
                    <tr>
                      <th scope="col">Scan started</th>
                      <th scope="col">Seen at</th>
                      <th scope="col">IP</th>
                      <th scope="col">Status</th>
                    </tr>
                  </thead>

                  <tbody>
                    {observations.observations.map((seen) => (
                      <tr key={`${seen.scan_id}-${seen.observed_at}`}>
                        <td>{formatLastSeen(seen.scan_started_at)}</td>
                        <td>{formatLastSeen(seen.observed_at)}</td>
                        <td>{seen.ip ?? ""}</td>
                        <td>{seen.status === "online" ? "Online" : "Offline"}</td>
                      </tr>
                    ))}
                  </tbody>

                </table>

                <div className="pager">
                  <button type="button" disabled={page <= 1} onClick={() => setParams({ page: String(page - 1) })}>Previous</button>
                  <span>Showing {start}–{end} of {total}</span>
                  <button type="button" disabled={end >= total} onClick={() => setParams({ page: String(page + 1) })}>Next</button>
                </div>

              </>

            )}

          </section>

        </main>

    );

}

