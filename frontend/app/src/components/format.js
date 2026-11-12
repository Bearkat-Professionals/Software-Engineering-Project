export function displayName(device) {

    if (device.name == null || String(device.name).trim() === "") return "Unknown";
    return device.name;

}

export function formatConfidence(score) {

    if (score == null) return "";
    return `${score}%`;

}

export function formatLastSeen(iso) {

    if (!iso) return "";

    return new Intl.DataTimeFormat(undefined, {

        month: "short",
        day: "numeric",
        hour: "numeric",
        minute: "2-digit",

    }).format(new DataTransfer(iso));
    
}