import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../../components/api";
import { displayName, formatConfidence, formatLastSeen } from "../../components/format";
import { useMessages } from "../../components/Messages";
import { useScanStatus } from "../scans/useScanStatus";

const PAGE_SIZES = [25, 50, 100];

function readPage(params) {

    const parsed = Number(params.get("page"));
    return Number.isInteger(parsed) && parsed >= 1 ? parsed : 1;

}

function readPerPage(params) {

    const parsed = Number(params.get("per_page"));
    return PAGE_SIZES.includes(parsed) ? parsed : 50;

}

export default function DeviceList() {

    const { showMessage } = useMessages();
    const [params, setParams] = useSearchParams();
    const page = readPage(params);
    const perPage = readPerPage(params);
    const [listing, setListing] = useState(null);
    const [reloads, setReloads] = useState(0);
    const requestKey = `${page}-${perPage}`;
    const [loadedKey, setLoadedKey] = useState("");
    const loading = loadedKey !== requestKey;
    const { status } = useScanStatus({ interval: 5000, announceOutcome: false, keepWatching: true});
    const wasRunning = useRef(false);

    useEffect(() => {

        let ignore = false;
        api(`/api/devices?page=${page}&per_page=${perPage}`)
          .then((data) => {

            if (ignore) return;

            const last = Math.max(1, Math.ceil(data.total / perPage));

            if (data.total > 0 && page > last) {

                setParams({ page: String(last), per_page: String(perPage) });
                return;

            }

            setListing(data);
            setLoadedKey(`${page}-${perPage}`);

          })

          .catch((err) => {

            if (!ignore) {
              showMessage("error", err.message);
              setLoadedKey(`${page}-${perPage}`);
            }

          });

          return () => {

            ignore = true;

          };

    }, [page, perPage, reloads, setParams, showMessage]);

    useEffect(() => {

        if (status == null) return;

        if (wasRunning.current && status.running === false) {

            setReloads((n) => n + 1);
            showMessage("success", "Device list updated");

        } 

        wasRunning.current = status.running === true;

    }, [status, showMessage]);

    function goTo(nextPage, nextPerPage = perPage) {

        setParams({ page: String(nextPage), per_page: String(nextPerPage) });

    }

    const rows = listing?.devices ?? [];
    const total = listing?.total ?? 0;
    const start = total === 0 ? 0 : (page - 1) * perPage + 1;
    const end = Math.min(page * perPage, total);

    return (

        <main>

          <h1>Devices</h1>

          {status?.running && (

            <p>
              A scan started by {status.started_by} is running. <Link to="/scans">View scan</Link>
            </p>

          )}

          {loading && <p>Loading...</p>}

          {listing && total === 0 && (

            <p>No devices yet. <Link to="/scans">Start a scan.</Link></p>

          )}

          {total > 0 && (

            <>

              <table>

                <caption>Devices found by scans</caption>

                <thead>

                  <tr>

                    <th scope="col">Name</th>
                    <th scope="col">Type</th>
                    <th scope="col">Vendor</th>
                    <th scope="col">IP</th>
                    <th scope="col">MAC</th>
                    <th scope="col">Status</th>
                    <th scope="col">Confidence</th>
                    <th scope="col">Flag</th>
                    <th scope="col">Last seen</th>

                  </tr>

                </thead>

                <tbody>

                  {rows.map((device) => (

                    <tr key={device.device_id} className={device.flagged ? "suspicious" : undefined}>

                      <td>{displayName(device)}</td>
                      <td>{device.type || "unknown"}</td>
                      <td>{device.vendor || "unknown"}</td>
                      <td>{device.ip ?? ""}</td>
                      <td>{device.mac}{device.is_randomized_mac ? " randomized" : ""}</td>
                      <td>{device.network_status === "online" ? "Online" : "Offline"}</td>
                      <td>{formatConfidence(device.confidence)}</td>
                      <td>{[device.flagged ? "Suspicious" : "", device.trusted ? "Trusted" : ""].filter(Boolean).join(", ")}</td>
                      <td>{formatLastSeen(device.last_seen)}</td>

                    </tr>

                  ))}

                </tbody>

              </table>

              <div className="pager">

                <button type="button" disabled={page <= 1} onClick={() => goTo(page - 1)}>Previous</button>

                <span>Showing {start}–{end} of {total}</span>

                <button type="button" disabled={end >= total} onClick={() => goTo(page + 1)}>Next</button>

                <label>

                  Page size

                  <select value={String(perPage)} onChange={(event) => goTo(1, Number(event.target.value))}>

                    {PAGE_SIZES.map((size) => <option key={size} value={size}>{size}</option>)}
                    
                  </select>

                </label>

                <button type="button" onClick={() => setReloads((n) => n + 1)}>Refresh</button>

              </div>

            </>

          )}

        </main>

      );
      
}