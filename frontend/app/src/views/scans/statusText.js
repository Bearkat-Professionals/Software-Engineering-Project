const PHASE_TEXT = {

    discovering_hosts: "Finding devices",
    saving_results: "Saving results",
    closing: "Closing",

};

export function phaseText(phase) {

    if (phase == null) return "Starting";

    return PHASE_TEXT[phase] ?? phase;

}

export function progressText(done, total) {

    if (done == null || total == null) return "";

    return `${done} of ${total}`;

}

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