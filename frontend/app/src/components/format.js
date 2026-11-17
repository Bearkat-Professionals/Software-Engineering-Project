// Display text for a device name, confidence score, and last-seen time.
// A device with no stored name is shown as Unknown.
export function displayName(device) {

    if (device.name == null || String(device.name).trim() === "") return "Unknown";
    return device.name;

}

// The score is a whole number. An empty score stays blank.
export function formatConfidence(score) {

    if (score == null) return "";
    return `${score}%`;

}

// The server sends a local time with no time zone. Show it in the browser's locale.
export function formatLastSeen(iso) {

    if (!iso) return "";

    return new Intl.DateTimeFormat(undefined, {

        month: "short",
        day: "numeric",
        hour: "numeric",
        minute: "2-digit",

    }).format(new Date(iso));
    
}