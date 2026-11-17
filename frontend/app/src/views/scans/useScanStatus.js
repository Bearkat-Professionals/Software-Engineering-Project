// Polls scan status while a scan is running and reports the outcome once.
import { useEffect, useState } from "react";
import { api } from "../../components/api";
import { useMessages } from "../../components/Messages";
import { outcomeMessage } from "./statusText";

// interval is how often to ask. announceOutcome shows the finished banner.
// keepWatching stays on after the scan ends, which the device list uses.
export function useScanStatus({

  interval = 2000,
  announceOutcome = true,
  keepWatching = false,

} = {}) {

  const { showMessage, clearMessage } = useMessages();
  const [status, setStatus] = useState(null);
  const [watching, setWatching] = useState(true);

  useEffect(() => {

    if (!watching) return undefined;

    let ignore = false;
    let timer = 0;
    let unreachableShown = false;

    function poll(delay) {

      timer = window.setTimeout(async () => {

        try {

          const next = await api("/api/scans/status");

          if (ignore) return;

          // The server is back. Remove the "can't reach the server" banner.
          if (unreachableShown) {

            clearMessage();
            unreachableShown = false;

          }

          setStatus(next);

          if (!next.running) {

            if (announceOutcome && next.last_scan) {

              const outcome = outcomeMessage(next.last_scan);
              showMessage(outcome.kind, outcome.text);

            }

            if (!keepWatching) {

              setWatching(false);
              return;

            }
          }

          poll (interval);

        } catch (err) {

          if (ignore) return;

          // Show the connection error once, then retry more slowly.
          if (err.unreachable) {

            if (!unreachableShown) {

              showMessage("error", err.message);
              unreachableShown = true;

            }

            poll (5000)
            return;

          }

          showMessage("error", err.message);
          setWatching(false);
        }

      }, delay);

    }

    poll (0);
    return () => {

      ignore = true;
      window.clearTimeout(timer);

    };

  }, [watching, showMessage, clearMessage, interval, announceOutcome, keepWatching]);

  return {

    status,
    
    // Call this after starting a scan so polling begins again.
    follow() {

      setWatching(true);

    },

  };

}