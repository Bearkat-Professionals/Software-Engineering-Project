// Holds a record while edit form is open. This way nobody deletes/retires under the editor
import { useEffect, useState } from "react";
import { api } from "./api";

const RENEW_EVERY_MS = 30 * 1000;
const IDLE_LIMIT_MS = 10 * 60 * 1000;

export const AWAY_MESSAGE = "You've been away, so others can now delete this record. Your changes are still here.";

// Time is the server's clock.
export function holdNotice(other) {
    return `${other.username} is also editing this (since ${other.since.slice(11,16)})`;
}

function release(holdId) {

    fetch(`/api/holds/${encodeURIComponent(holdId)}`, {

        method: "DELETE",
        headers: { "Content-Type": "application/json" },
        body: "{}",
        keepalive: true,

    }) .catch(() => {});

}

// recordId is null when there's nothing to hold
export function useEditHold(recordType, recordId) {

    const [others, setOthers] = useState([]);
    const [lost, setLost] = useState(false);

    useEffect(() => {

        if (recordType == null || !Number.isInteger(recordId)) return undefined;

        let closed = false;
        let holdId = null;
        let taking = false;
        let away = false;
        let lastActive = Date.now();

        async function take() {

            if (taking) return;

            taking = true;

            try {

                const data = await api ("/api/holds", {

                    method: "POST",
                    body: { record_type: recordType, record_id: recordId },
                });

                // The form closed, or the user went idle, while the request was on its way.
                if (closed || away) {
                    release(data.hold_id);
                    return;
                }

                holdId = data.hold_id;
                setOthers(data.others);

            } catch {

            } finally {

                taking = false;

            }
        }

        async function renew() {

            const renewing = holdId;

            if (renewing == null) {

                take();
                return;

            }

            try {

                const data = await api(`/api/holds/${encodeURIComponent(renewing)}`, { method: "PUT", body: {} });

                if (!closed) setOthers(data.others);

            } catch (err) {

                // 404 means the hold ended on the server.
                if (err.status === 404 && !closed && !away && holdId === renewing) {

                    holdId = null;
                    take();

                }
            }
        }

        function tick() {

            if (away) return;

            if (Date.now() - lastActive < IDLE_LIMIT_MS) {
                
                renew();
                return;

            }

            // Ten idle minutes
            away = true;
            setLost(true);

            if (holdId != null) release(holdId);

            holdId = null;

        }

        function onActivity() {

            lastActive = Date.now();

            if (!away) return;

            away = false;
            setLost(false);
            take();

        }

        function onPageHide() {

            if (holdId != null) release(holdId);

        }

        take();

        const timer = setInterval(tick, RENEW_EVERY_MS);

        window.addEventListener("keydown", onActivity);
        window.addEventListener("click", onActivity);
        window.addEventListener("pagehide", onPageHide);

        return () => {

            closed = true;
            clearInterval(timer);
            window.removeEventListener("keydown", onActivity);
            window.removeEventListener("click", onActivity);
            window.removeEventListener("pagehide", onPageHide);

            if (holdId != null) release(holdId);

            setOthers([]);
            setLost(false);

        };

    }, [recordType, recordId]);

    return { others, lost };
    
}