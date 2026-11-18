// Labels for a scan's phase, progress, and outcome.
const PHASE_TEXT = {

    discovering_hosts: "Finding devices",
    collecting_evidence: "Collecting evidence",
    saving_results: "Saving results",
    closing: "Closing",

};

// A missing phase means the scan has been accepted but has not reported a phase yet.
export function phaseText(phase) {

    if (phase == null) return "Starting";

    return PHASE_TEXT[phase] ?? phase;

}

// Blank until the server knows how many addresses it will check.
export function progressText(done, total) {

    if (done == null || total == null) return "";

    return `${done} of ${total}`;

}

// The banner kind matches the scan outcome. Partial and failed include the server's reasons.
export function outcomeMessage(lastScan) {

    const reasons = (lastScan.errors ?? []).join(" ");

    if (lastScan.outcome === "success") {

        return { kind: "success", text: "Scan Finished." };

    }

    if (lastScan.outcome === "partial") {

        return { kind: "warning", text: reasons || "Scan finished with problems." };

    }

    return { kind: "error", text: reasons || "Scan Failed." };
    
}