import { useEffect, useState } from "react";
import { api } from "../../components/api";
import { useMessages } from "../../components/Messages";
import { outcomeMessage } from "./statusText";

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
    
    follow() {

      setWatching(true);

    },

  };

}