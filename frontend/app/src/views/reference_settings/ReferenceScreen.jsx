// Searchable, paged lists of vendors, device types, and signatures. Editors can retire and reactivate records.
import { useEffect, useState } from "react";
import { Link, NavLink, useParams, useSearchParams } from "react-router-dom";
import { useAuth } from "../../auth/AuthProvider";
import { api } from "../../components/api";
import { useMessages } from "../../components/Messages";

// kind is the API path. The label is what the tab shows.
const TABS = [

    { kind: "vendors", label: "Vendors" },
    { kind: "device-types", label: "Device Types" },
    { kind: "signatures", label: "Signatures" },

];

const PAGE_SIZE = [25, 50, 100];
const SAVED = "Saved. Scans use this from the next scan.";

function readPage(params) {

    const parsed = Number(params.get("page"));
    
    return Number.isInteger(parsed) && parsed >= 1 ? parsed : 1;

}

function readPerPage(params) {

    const parsed = Number(params.get("per_page"));

    return PAGE_SIZE.includes(parsed) ? parsed : 50;

}

// The built-in unknown vendor and device type cannot be edited or retired.
function isUnknown(kind, record) {

    return kind !== "signatures" && record.name?.toLowerCase() === "unknown";

}

export default function ReferenceScreen() {

    const { kind } = useParams();
    const tab = TABS.find((item) => item.kind === kind);
    const { can } = useAuth();
    // Viewers can read the lists. Editors and admins can change them.
    const canEdit = can("EDIT_REFERENCE_DATA");
    const { showMessage } = useMessages();
    const [params, setParams] = useSearchParams();
    const page = readPage(params);
    const perPage = readPerPage(params);
    const q = params.get("q") ?? "";
    const showRetired = params.get("retired") === "1";
    const [draft, setDraft] = useState(q);
    const [seenQ, setSeenQ] = useState(q);
    const [listing, setListing] = useState(null);
    const [reloads, setReloads] = useState(0);
    const [pending, setPending] = useState(null);
    const requestKey = `${kind}|${page}|${perPage}|${q}|${showRetired}|${reloads}`;
    const [loadedKey, setLoadedKey] = useState("");
    const loading = tab != null && loadedKey !== requestKey;

    // Back and reload change q. Copy that into the box without waiting for an effect.
    if (seenQ !== q) {

        setDraft(q);
        setSeenQ(q);

    }

    useEffect(() => {

        // Wait until typing pauses, then store the search in the URL and return to page 1.
        if (draft === q) return undefined;

        const timer = setTimeout(() => {

            setParams((current) => {

                const next = new URLSearchParams(current);
                const text = draft.trim().slice(0, 100);

                if (text === "") next.delete("q");

                else next.set ("q", text);
                
                next.delete("page");

                return next;

            });

        }, 300);

        return () => clearTimeout(timer);

    }, [draft, q, setParams]);

    useEffect(() => {

        if (!tab) return undefined;

        let ignore = false;

        const query = new URLSearchParams({

            status: showRetired ? "both" : "active",
            page: String(page),
            per_page: String(perPage),

        });

        if (q !== "") query.set("q", q);

        api(`/api/reference/${kind}?${query}`)
            .then((data) => {

                if (ignore) return;

                const last = Math.max(1, Math.ceil(data.total / perPage));

                if (data.total > 0 && page > last) {

                    setParams((current) => {

                        const next = new URLSearchParams(current);
                        
                        next.set("page", String(last));

                        return next;

                    });

                    return;

                }

                setListing(data);
                setLoadedKey(requestKey);

            })

            .catch((err) => {

                if (ignore) return;
                showMessage("error", err.message);
                setLoadedKey(requestKey);

            });

            return () => {

                ignore = true;

            };
    }, [tab, kind, page, perPage, q, showRetired, reloads, requestKey, setParams, showMessage]);

    // Retired rows stay hidden until this is checked. The choice is kept in the URL.
    function toggleRetired(checked) {

        setParams((current) => {

            const next = new URLSearchParams(current);

            if (checked) next.set("retired", "1");

            else next.delete("retired");

            next.delete("page");

            return next;

        });

    }

    function goTo(nextPage, nextPerPage = perPage) {

        setParams((current) => {

            const next = new URLSearchParams(current);

            next.set("page", String(nextPage));
            next.set("per_page", String(nextPerPage));

            return next;

        });

    }

    // Load the record first so the confirmation can show who uses it and its loaded_at.
    async function ask(record, action) {

        try {

            const data = await api(`/api/reference/${kind}/${record.id}`);

            setPending({

                id: record.id,
                loadedAt: data.loaded_at,
                usageText: data.usage_text,
                action,

            });

        } catch (err) {

            showMessage("error", err.message);
            
            if (err.status === 404) setReloads((n) => n + 1);

        }

    }

    // Retire or reactivate with the loaded_at from the confirmation. A conflict reloads that time.
    async function confirmPending() {

        try {

            await api(`/api/reference/${kind}/${pending.id}/${pending.action}`, {

                method: "POST",
                body: { loaded_at: pending.loadedAt },

            });

            setPending(null);
            showMessage("success", SAVED);
            setReloads((n) => n + 1);

        } catch (err) {

            showMessage("error", err.message);

            if (err.status === 409) {

                const data = await api(`/api/reference/${kind}/${pending.id}`);

                setPending((current) => ({ ...current, loadedAt: data.loaded_at, usageText: data.usage_text }));

            }

        }

    }

    if (!tab) return <main><h1>Reference</h1><p>That list does not exist.</p></main>;

    const rows = listing?.records ?? [];
    const total = listing?.total ?? 0;
    const start = total === 0 ? 0 : (page - 1) * perPage + 1;
    const end = Math.min(page * perPage, total);
    const signatures = kind === "signatures";

    return (

        <main>

          <h1>Reference</h1>

          <nav>
            {TABS.map((item) => (

              <NavLink key={item.kind} to={`/reference/${item.kind}`}>{item.label}</NavLink>

            ))}

          </nav>

          <label>

            Search

            <input value={draft} onChange={(event) => setDraft(event.target.value)} />

          </label>

          <label>

            <input

              type="checkbox"
              checked={showRetired}
              onChange={(event) => toggleRetired(event.target.checked)}

            />

            Show retired

          </label>

          {/* Viewers do not get Add, Edit, Retire, or Reactivate. */}
          {canEdit && <p><Link to={`/reference/${kind}/new`}>Add</Link></p>}

          {pending && (

            <section>

              <p>{pending.usageText}</p>

              {/* Retiring keeps current devices and stops future scans from matching this record. */}
              {pending.action === "retire" && (

                <p>Existing devices keep it. New scans will not match it.</p>

              )}

              <button type="button" onClick={confirmPending}>

                {pending.action === "retire" ? "Retire" : "Reactivate"}

              </button>

              <button type="button" onClick={() => setPending(null)}>Cancel</button>

            </section>

          )}

          {loading && <p>Loading...</p>}

          {listing && total === 0 && <p>No records match.</p>}

          {total > 0 && (

            <>

              <table>

                <caption>{tab.label}</caption>

                <thead>

                  <tr>

                    {signatures ? (

                      <>

                        <th scope="col">Evidence type</th>
                        <th scope="col">Pattern</th>

                      </>

                    ) : (

                      <th scope="col">Name</th>

                    )}

                    <th scope="col">Status</th>

                    {canEdit && <th scope="col">Actions</th>}

                  </tr>

                </thead>

                <tbody>

                  {rows.map((record) => {
                    
                    const locked = isUnknown(kind, record);

                    return (

                      <tr key={record.id}>

                        {signatures ? (

                          <>

                            <td>{record.evidence_type}</td>
                            <td>{record.pattern}</td>

                          </>

                        ) : (

                          <td>{record.name}</td>

                        )}

                        <td>{record.status === "retired" ? "Retired" : "Active"}</td>

                        {canEdit && (

                          <td>

                            {!locked && (

                              <>

                                <Link to={`/reference/${kind}/${record.id}`}>Edit</Link>

                                {record.status === "retired" ? (

                                  <button type="button" onClick={() => ask(record, "reactivate")}>Reactivate</button>

                                ) : (

                                  <button type="button" onClick={() => ask(record, "retire")}>Retire</button>

                                )}

                              </>

                            )}

                          </td>

                        )}

                      </tr>

                    );

                  })}

                </tbody>

              </table>

              <div className="pager">

                <button type="button" disabled={page <= 1} onClick={() => goTo(page - 1)}>Previous</button>

                <span>Showing {start}–{end} of {total}</span>

                <button type="button" disabled={end >= total} onClick={() => goTo(page + 1)}>Next</button>

                <label>

                  Page size

                  <select value={String(perPage)} onChange={(event) => goTo(1, Number(event.target.value))}>

                    {PAGE_SIZE.map((size) => <option key={size} value={size}>{size}</option>)}

                  </select>

                </label>

              </div>

            </>

          )}

        </main>

    );

}