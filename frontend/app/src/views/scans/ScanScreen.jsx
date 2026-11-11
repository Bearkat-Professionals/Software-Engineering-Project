import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../../components/api";
import { useMessages } from "../../components/Messages";
import { phaseText, progressText } from "./statusText";
import { useScanStatus } from "./useScanStatus";

export default function ScanScreen() {

    const { showMessage } = useMessages();
    const { status, follow } = useScanStatus();
    const [scopeDraft, setScopeDraft] = useState(null);
    const running = status?.running === true;
    const scope = scopeDraft ?? status?.last_scan?.scope ?? "";

    async function onStart(event) {

        event.preventDefault();
        const trimmed = scope.trim();

        if (trimmed === "") {

            showMessage("error", "Enter a scan range." );
            return;

        }

        try {

            await api("/api/scans", { method: "POST", body: { scope: trimmed } });
            follow();

        } catch (err) {

            if (err.status === 409) {

                showMessage("warning", err.message);
                follow();
                return;
            }

            showMessage("error", err.message);

        }

    }

    const fraction = progressText(status?.done, status?.total);

    return (

        <main>

            <h1>Scan</h1>

            <form onSubmit={onStart}>

                <label>

                    Scope

                    <input 

                    value={scope} 

                    onChange={(event) => setScopeDraft(event.target.value)} 

                    />

                </label>

                <button type="submit" disabled={running}>Start Scan</button>

            </form>

            {running && (

                <section>

                    <p>Started by {status.started_by} at {status.started_at}</p>
                    <p>Scope {status.scope}</p>
                    <p>{phaseText(status.phase)} {fraction}</p>
                
                    {status.total != null && (

                        <progress value={status.done ?? 0} max={status.total} />

                    )}

                    <ul>

                        {(status.errors ?? []).map((line, index) => <li key={index}>{line}</li>)}

                    </ul>

                </section>

            )}

            {!running && status?.last_scan != null && (

                <p><Link to="/devices">Device list</Link></p>

            )}

        </main>
        
    );

}