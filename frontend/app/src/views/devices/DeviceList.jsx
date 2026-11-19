// Paged device list. The page and page size stay in the URL, and the list reloads when a scan finishes.
import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../../components/api";
import { displayName, formatConfidence, formatLastSeen } from "../../components/format";
import { useMessages } from "../../components/Messages";
import { useScanStatus } from "../scans/useScanStatus";
import { useAuth } from "../../auth/AuthProvider";
import { downloadFile } from "../../components/download";

const PAGE_SIZES = [25, 50, 100];

// The URL uses the API's own names, so the filters pass straight through to the request.
const FILTER_KEYS = ["type", "vendor", "status", "flagged", "trusted", "confidence_min", "confidence_max", "seen_from", "seen_to", "q"];

// Boxes that are typed into. They wait for typing to pause before the URL changes.
const TYPED_KEYS = ["q", "confidence_min", "confidence_max", "seen_from", "seen_to"];
const BLANK_DRAFTS = { q: "", confidence_min: "", confidence_max: "", seen_from: "", seen_to: "" };

// Choices that include "any". An empty value means the filter is off.
const CHOICES = [

    { key: "status", label: "Status", options: [["online", "Online"], ["offline", "Offline"]] },
    { key: "flagged", label: "Flagged", options: [["true", "Flagged"], ["false", "Not flagged"]] },
    { key: "trusted", label: "Trusted", options: [["true", "Trusted"], ["false", "Not trusted"]] },

];

// A missing or invalid page in the URL is treated as page 1.
function readPage(params) {

    const parsed = Number(params.get("page"));
    return Number.isInteger(parsed) && parsed >= 1 ? parsed : 1;

}

// Only the three offered sizes are accepted. Anything else falls back to 50.
function readPerPage(params) {

    const parsed = Number(params.get("per_page"));
    return PAGE_SIZES.includes(parsed) ? parsed : 50;

}

// The typed boxes as the URL has them. A missing one is an empty box.
function readTyped(params) {

    return Object.fromEntries(TYPED_KEYS.map((key) => [key, params.get(key) ?? ""]));

}

// The request's query: paging, then every filter and the sort that the URL has.
function apiQuery(params, page, perPage) {

    const query = new URLSearchParams({ page: String(page), per_page: String(perPage) });

    for (const key of [...FILTER_KEYS, "sort", "order"]) {

        const value = (params.get(key) ?? "").trim();
        if (value !== "") query.set(key, value);

    }

    return query.toString();

}

// A column header that sorts. The arrow shows the current sort and its direction.
function SortHeader({ label, column, sort, order, onSort }) {

    const active = sort === column;

    return (

        <th scope="col" aria-sort={active ? (order === "asc" ? "ascending" : "descending") : undefined}>
          <button type="button" onClick={() => onSort(column)}>
            {label}{active ? (order === "asc" ? " ▲" : " ▼") : ""}
          </button>
        </th>

    );

}

export default function DeviceList() {

    const { showMessage } = useMessages();
    const { can } = useAuth();
    const canEdit = can("EDIT_DEVICES");
    const [exporting, setExporting] = useState(false);
    const [params, setParams] = useSearchParams();
    const page = readPage(params);
    const perPage = readPerPage(params);
    const sort = params.get("sort") || "last_seen";
    const order = params.get("order") || "desc";
    const type = params.get("type") ?? "";
    const vendor = params.get("vendor") ?? "";
    // The typed boxes as one string, so a change in the URL is easy to notice.
    const typedInUrl = TYPED_KEYS.map((key) => params.get(key) ?? "").join("\n");
    const filtered = FILTER_KEYS.some((key) => (params.get(key) ?? "").trim() !== "");
    const query = apiQuery(params, page, perPage);
    const [listing, setListing] = useState(null);
    const [reasons, setReasons] = useState([]);
    const [reloads, setReloads] = useState(0);
    const [loadedKey, setLoadedKey] = useState("");
    // Loading is derived from the request, so the effect does not set it directly.
    const loading = loadedKey !== query;
    const [drafts, setDrafts] = useState(() => readTyped(params));
    const [seenTyped, setSeenTyped] = useState(typedInUrl);
    const [types, setTypes] = useState([]);
    const [vendorText, setVendorText] = useState("");
    const [vendorHits, setVendorHits] = useState([]);
    const [vendorLabel, setVendorLabel] = useState(null);
    // Watch scans quietly. This page should not repeat the scan screen's outcome banner.
    const { status } = useScanStatus({ interval: 5000, announceOutcome: false, keepWatching: true});
    const wasRunning = useRef(false);

    // Back, reload, and Clear filters change the URL. Copy that into the boxes,
    // unless the boxes already say the same thing (their own search just landed).
    if (seenTyped !== typedInUrl) {

        const fromUrl = readTyped(params);

        setSeenTyped(typedInUrl);
        if (TYPED_KEYS.some((key) => drafts[key].trim() !== fromUrl[key])) setDrafts(fromUrl);

    }

    // Wait until typing pauses, then store the boxes in the URL and return to page 1.
    useEffect(() => {

        const wanted = TYPED_KEYS.map((key) => drafts[key].trim()).join("\n");

        if (wanted === typedInUrl) return undefined;

        const timer = setTimeout(() => {

            setParams((current) => {

                const next = new URLSearchParams(current);

                for (const key of TYPED_KEYS) {

                    const text = drafts[key].trim();

                    if (text === "") next.delete(key);
                    else next.set(key, text);

                }

                next.delete("page");

                return next;

            });

        }, 300);

        return () => clearTimeout(timer);

    }, [drafts, typedInUrl, setParams]);

    useEffect(() => {

        let ignore = false;
        api(`/api/devices?${query}`)
          .then((data) => {

            if (ignore) return;

            const last = Math.max(1, Math.ceil(data.total / perPage));

            // A page past the end is rewritten to the last real page. The filters stay.
            if (data.total > 0 && page > last) {

                setParams((current) => {

                    const next = new URLSearchParams(current);

                    next.set("page", String(last));

                    return next;

                });
                return;

            }

            setListing(data);
            setReasons([]);
            setLoadedKey(query);

          })

          .catch((err) => {

            if (ignore) return;

            // A refused search lists every reason beside the filters. The last good rows stay.
            if (err.status === 400) setReasons(err.reasons?.length ? err.reasons : [err.message]);
            else showMessage("error", err.message);

            setLoadedKey(query);

          });

          return () => {

            ignore = true;

          };

    }, [query, page, perPage, reloads, setParams, showMessage]);

    // The type picker lists the active device types.
    useEffect(() => {

        let ignore = false;

        api("/api/device-types")
          .then((data) => {
            if (!ignore) setTypes(data.device_types);
          })
          .catch((err) => {
            if (!ignore) showMessage("error", err.message);
          });

        return () => {

            ignore = true;

        };

    }, [showMessage]);

    // There are too many vendors for a dropdown, so the box searches after typing pauses.
    useEffect(() => {

        const text = vendorText.trim();

        if (text.length < 2) return undefined;

        let ignore = false;

        const timer = setTimeout(() => {

            const search = new URLSearchParams({ search: text.slice(0, 100), limit: "20" });

            api(`/api/vendors?${search}`)
              .then((data) => {
                if (!ignore) setVendorHits(data.vendors);
              })
              .catch((err) => {
                if (!ignore) showMessage("error", err.message);
              });

        }, 300);

        return () => {

            ignore = true;
            clearTimeout(timer);

        };

    }, [vendorText, showMessage]);

    // The URL holds only the vendor's id. After a reload, look its name up once.
    useEffect(() => {

        if (vendor === "" || vendorLabel?.id === vendor) return undefined;

        let ignore = false;

        api(`/api/reference/vendors/${encodeURIComponent(vendor)}`)
          .then((data) => {
            if (!ignore) setVendorLabel({ id: vendor, name: data.record.name });
          })
          .catch(() => {
            if (!ignore) setVendorLabel({ id: vendor, name: `Vendor ${vendor}` });
          });

        return () => {

            ignore = true;

        };

    }, [vendor, vendorLabel]);

    useEffect(() => {

        if (status == null) return;

        // A scan that was running and has now stopped means the rows may have changed.
        if (wasRunning.current && status.running === false) {

            setReloads((n) => n + 1);
            showMessage("success", "Device list updated");

        } 

        wasRunning.current = status.running === true;

    }, [status, showMessage]);

    function setDraft(key, value) {

        setDrafts((current) => ({ ...current, [key]: value }));

    }

    // A changed filter goes into the URL and returns to page 1. An empty value removes it.
    function setFilter(key, value) {

        setParams((current) => {

            const next = new URLSearchParams(current);

            if (value === "") next.delete(key);
            else next.set(key, value);

            next.delete("page");

            return next;

        });

    }

    // Clearing keeps the sort and the page size. Only the filters and the page go.
    function clearFilters() {

        setDrafts(BLANK_DRAFTS);
        setVendorText("");
        setVendorHits([]);

        setParams((current) => {

            const next = new URLSearchParams(current);

            for (const key of FILTER_KEYS) next.delete(key);

            next.delete("page");

            return next;

        });

    }

    // A new column starts ascending. Clicking the current column reverses it.
    function sortBy(column) {

        const nextOrder = sort === column && order === "asc" ? "desc" : "asc";

        setParams((current) => {

            const next = new URLSearchParams(current);

            next.set("sort", column);
            next.set("order", nextOrder);
            next.delete("page");

            return next;

        });

    }

    function chooseVendor(hit) {

        setVendorLabel({ id: String(hit.vendor_id), name: hit.name });
        setVendorText("");
        setVendorHits([]);
        setFilter("vendor", String(hit.vendor_id));

    }

    // Page and page size live in the URL so reload and Back keep them. The filters stay.
    function goTo(nextPage, nextPerPage = perPage) {

        setParams((current) => {

            const next = new URLSearchParams(current);

            next.set("page", String(nextPage));
            next.set("per_page", String(nextPerPage));

            return next;

        });

    }

    // Export uses the list's filters and sort without paging.
    // button stays disabled until download has finished or failed
    async function onExport() {

      const filters = new URLSearchParams(query);

      filters.delete("page");
      filters.delete("per_page");

      const text = filters.toString();

      setExporting(true);

      try {
        await downloadFile(text === "" ? "/api/export/devices" : `/api/export/devices?${text}`, "devices.json");
      } catch (err) {
        showMessage("error", err.message);
      } finally {
        setExporting(false);
      }
    }

    const rows = listing?.devices ?? [];
    const total = listing?.total ?? 0;
    const start = total === 0 ? 0 : (page - 1) * perPage + 1;
    const end = Math.min(page * perPage, total);
    // Old results are not shown for a search box that is now too short.
    const shownHits = vendorText.trim().length >= 2 ? vendorHits : [];
    const vendorName = vendorLabel?.id === vendor ? vendorLabel.name : "Loading...";
    const header = { sort, order, onSort: sortBy };

    return (

        <main>

          <h1>Devices</h1>

          {canEdit && <p><Link to="/devices/new">Add Device</Link></p>}

          {status?.running && (

            <p>
              A scan started by {status.started_by} is running. <Link to="/scans">View scan</Link>
            </p>
            
          )}

          {can("EXPORT") && (

            <p><button type="button" onClick={onExport} disabled={exporting}>Export JSON</button></p>

          )}
          
          <div className="filters">

            <label>
              Search
              <input
                type="search"
                value={drafts.q}
                maxLength={100}
                placeholder="Name, hostname, IP or MAC"
                onChange={(event) => setDraft("q", event.target.value)}
              />
            </label>

            <label>
              Type
              <select value={type} onChange={(event) => setFilter("type", event.target.value)}>
                <option value="">Any</option>
                {types.map((item) => (
                  <option key={item.device_type_id} value={item.device_type_id}>{item.name}</option>
                ))}
                {/* A retired type from a bookmark is not in the list, so it gets its own option. */}
                {type !== "" && !types.some((item) => String(item.device_type_id) === type) && (
                  <option value={type}>Type {type}</option>
                )}
              </select>
            </label>

            {vendor === "" ? (
              <label>
                Vendor
                <input
                  value={vendorText}
                  placeholder="Type 2 or more letters"
                  onChange={(event) => setVendorText(event.target.value)}
                />
              </label>
            ) : (
              <div>
                Vendor: {vendorName}
                <button type="button" onClick={() => setFilter("vendor", "")}>Remove</button>
              </div>
            
            )}

            {CHOICES.map((choice) => (
              <label key={choice.key}>
                {choice.label}
                <select
                  value={params.get(choice.key) ?? ""}
                  onChange={(event) => setFilter(choice.key, event.target.value)}
                >
                  <option value="">Any</option>
                  {choice.options.map(([value, text]) => <option key={value} value={value}>{text}</option>)}
                </select>
              </label>
            ))}

            <label>
              Confidence from
              <input
                type="number"
                min="0"
                max="100"
                value={drafts.confidence_min}
                onChange={(event) => setDraft("confidence_min", event.target.value)}
              />
            </label>

            <label>
              Confidence to
              <input
                type="number"
                min="0"
                max="100"
                value={drafts.confidence_max}
                onChange={(event) => setDraft("confidence_max", event.target.value)}
              />
            </label>

            <label>
              Last seen from
              <input type="date" value={drafts.seen_from} onChange={(event) => setDraft("seen_from", event.target.value)} />
            </label>

            <label>
              Last seen to
              <input type="date" value={drafts.seen_to} onChange={(event) => setDraft("seen_to", event.target.value)} />
            </label>

            <button type="button" onClick={clearFilters}>Clear filters</button>

          </div>

          {/* Vendors found by the search. Choosing one filters the list by it. */}
          {vendor === "" && shownHits.length > 0 && (

            <ul>
              {shownHits.map((hit) => (
                <li key={hit.vendor_id}>
                  <button type="button" onClick={() => chooseVendor(hit)}>{hit.name}</button>
                </li>
              ))}
            </ul>

          )}

          {/* The server's reasons repeat what was typed. They are shown as text, never HTML. */}
          {reasons.length > 0 && (

            <ul>
              {reasons.map((reason) => <li key={reason}>{reason}</li>)}
            </ul>

          )}

          {loading && <p>Loading...</p>}

          {listing && total === 0 && !filtered && (

            <p>No devices yet. <Link to="/scans">Start a scan.</Link></p>

          )}

          {listing && total === 0 && filtered && (

            <p>
              No devices match these filters.
              <button type="button" onClick={clearFilters}>Clear filters</button>
            </p>
          )}

          {total > 0 && (

            <>

              <table>

                <caption>Devices found by scans</caption>

                <thead>

                  <tr>

                    <SortHeader label="Name" column="name" {...header} />
                    <SortHeader label="Type" column="type" {...header} />
                    <SortHeader label="Vendor" column="vendor" {...header} />
                    <SortHeader label="IP" column="ip" {...header} />
                    <th scope="col">MAC</th>
                    <th scope="col">Status</th>
                    <SortHeader label="Confidence" column="confidence" {...header} />
                    <th scope="col">Flag</th>
                    <SortHeader label="Last seen" column="last_seen" {...header} />

                  </tr>

                </thead>

                <tbody>

                  {/* A flagged device is highlighted. Its name is text, never HTML. */}
                  {rows.map((device) => (

                    <tr key={device.device_id} className={device.flagged ? "suspicious" : undefined}>

                      <td><Link to={`/devices/${device.device_id}`}>{displayName(device)}</Link></td>
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