// Imports
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../../auth/AuthProvider";
import { api } from "../../components/api";
import { formatLastSeen } from "../../components/format";
import { useMessages } from "../../components/Messages";

function formatSize(bytes) {

    if (bytes == null) return "";

    if (bytes < 1_000_000) return `${Math.round(bytes / 1000)} KB`;

    return `${(bytes / 1_000_000).toFixed(1)} MB`;

}

function attemptLine(attempt) {

    if (attempt == null) return null;

    const action = attempt.action === "restore" ? "Restore" : "Backup";
    const when = formatLastSeen(attempt.at);
    const head = `${action} by ${attempt.by} at ${when}`;

    if (attempt.status === "done") return `${head}: done`;

    if (attempt.status === "failed") {
        return attempt.reason ? `${head}: failed: ${attempt.reason}` : `${head}: failed`;
    }

    return `${head}: ${attempt.status}`;

}

export default function BackupScreen() {

    const { clearSession } = useAuth();
    const { showMessage } = useMessages();
    const navigate = useNavigate();
    const [listing, setListing] = useState(null);
    const [reloads, setReloads] = useState(0);
    const [waiting, setWaiting] = useState(false);
    const [confirming, setConfirming] = useState(null);
    const [typed, setTyped] = useState("");
    const [problems, setProblems] = useState(null);
    const [passphrase, setPassphrase] = useState("");
    const privateKeyInput = useRef(null);

    useEffect(() => {

        let ignore = false;

        api("/api/backups")
          .then((data) => {
            if (!ignore) setListing(data);
          })
          .catch((err) => {
            if (!ignore) showMessage("error", err.message);
          });

        return () => {

            ignore = true;

        };

    }, [reloads, showMessage]);

    const busy = listing?.busy != null || waiting;

    function showFailure(err) {

        if (err.reasons?.length) {
            showMessage("error", err.reasons.join(" "));
            return;
        }

        showMessage("error", err.message);

    }

    function backupNamed(name) {

        return listing?.backups?.find((backup) => backup.name === name);

    }

    function clearKeyMaterial() {

        setPassphrase("");

        if (privateKeyInput.current) privateKeyInput.current.value = "";

    }

    async function oneUseKeyFor(name) {

        if (!backupNamed(name)?.encrypted) return {};

        const file = privateKeyInput.current?.files?.[0];

        if (!file) throw new Error("Choose the private key for this encrypted backup.");

        return { private_key: await file.text(), passphrase };

    }

    async function onBackup() {

        setWaiting(true);

        try {

            const data = await api("/api/backups", { method: "POST", body: {} });

            showMessage("success", `Backup ${data.backup.name} saved.`);
            setReloads((n) => n + 1);

        } catch (err) {

            showFailure(err);

        } finally {

            setWaiting(false);

        }

    }

    async function onVerify(name) {

        setWaiting(true);
        setProblems(null);

        try {

            const keyMaterial = await oneUseKeyFor(name);

            const data = await api(`/api/backups/${encodeURIComponent(name)}/verify`, {
                method: "POST",
                body: keyMaterial,
            });

            if (data.ok && (!data.problems || data.problems.length === 0)) {
                showMessage("success", "No problems found");
                setProblems(null);
            } else {
                setProblems(data.problems ?? []);
                showMessage("error", "This backup has problems.");
            }

        } catch (err) {

            showFailure(err);

        } finally {

            clearKeyMaterial();
            setWaiting(false);

        }

    }

    function startRestore(name) {

        setConfirming(name);
        setTyped("");
        setProblems(null);

    }

    function cancelRestore() {

        setConfirming(null);
        setTyped("");

    }

    async function onRestore(event) {

        event.preventDefault();

        if (typed !== confirming) return;

        setWaiting(true);

        try {

            const keyMaterial = await oneUseKeyFor(confirming);

            await api(`/api/backups/${encodeURIComponent(confirming)}/restore`, {
                method: "POST",
                body: { confirm: typed, ...keyMaterial },
            });

            const name = confirming;

            clearSession();
            navigate(`/login?restored=${encodeURIComponent(name)}`, { replace: true });

        } catch (err) {

            showFailure(err);

        } finally {

            clearKeyMaterial();
            setWaiting(false);

        }

    }

    const rows = listing?.backups ?? [];
    const lastLine = attemptLine(listing?.last_attempt);

    return (

        <main>

          <h1>Backups</h1>

          <p>
            <button type="button" onClick={onBackup} disabled={busy}>
              {busy ? "Working..." : "Back up now"}
            </button>
          </p>

          {listing?.warning && <p role="alert">{listing.warning}</p>}

          {listing?.backups?.some((backup) => backup.encrypted) && (
            <section>
              <h2>Encrypted backup key</h2>
              <p>The key and passphrase are used for one verify or restore request, then cleared.</p>
              <label>
                Private key (PEM)
                <input ref={privateKeyInput} type="file" accept=".pem,.key,text/plain" />
              </label>
              <label>
                Private key passphrase
                <input
                  type="password"
                  value={passphrase}
                  onChange={(event) => setPassphrase(event.target.value)}
                  autoComplete="off"
                />
              </label>
            </section>
          )}

          {listing?.busy && (
            <p>
              A {listing.busy.action} by {listing.busy.by} is running
              (since {formatLastSeen(listing.busy.since)}).
            </p>
          )}

          {lastLine && <p>{lastLine}</p>}

          {problems && problems.length > 0 && (
            <ul>
              {problems.map((problem) => <li key={problem}>{problem}</li>)}
            </ul>
          )}

          {confirming && (
            <section>
              <p>This replaces all data (devices, scans, reference data, accounts and the audit log) with the backup's copy.</p>
              <p>A safety backup is taken first.</p>
              <p>Everyone, including you, is signed out.</p>
              <form onSubmit={onRestore}>
                <label>
                  Type {confirming} to confirm
                  <input
                    value={typed}
                    onChange={(event) => setTyped(event.target.value)}
                    autoComplete="off"
                  />
                </label>
                <button type="submit" disabled={typed !== confirming || busy}>Restore</button>
                <button type="button" onClick={cancelRestore}>Cancel</button>
              </form>
            </section>
          )}

          {listing == null && <p>Loading...</p>}

          {listing && rows.length === 0 && <p>No backups yet.</p>}

          {rows.length > 0 && (
            <table>
              <caption>Stored backups</caption>
              <thead>
                <tr>
                  <th scope="col">Name</th>
                  <th scope="col">Kind</th>
                  <th scope="col">Created</th>
                  <th scope="col">Size</th>
                  <th scope="col">Protection</th>
                  <th scope="col">Actions</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((backup) => (
                  <tr key={backup.name}>
                    <td>{backup.name}</td>
                    <td>{backup.kind}</td>
                    <td>{formatLastSeen(backup.created_at)}</td>
                    <td>{formatSize(backup.size_bytes)}</td>
                    <td>{backup.encrypted ? "Encrypted" : "Unencrypted"}</td>
                    <td>
                      <button type="button" onClick={() => onVerify(backup.name)} disabled={busy}>
                        Verify
                      </button>
                      <button type="button" onClick={() => startRestore(backup.name)} disabled={busy}>
                        Restore
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

        </main>

    );

}
