// Downloads a file from the API. If it fails it's thrown as an ApiError
import { api } from "./api";

// The server names the file
function fileNameFrom(header) {

    const match = /filename="?([^";]+)"?/i.exec(header ?? "");
    return match ? match[1].trim() : null;

  }

export async function downloadFile(path, fallbackName) {

    const response = await api(path, { blob: true });
    const file = await response.blob();
    const url = URL.createObjectURL(file);

    // tempory link with a download
    const link = document.createElement("a");

    link.href = url;
    link.download = fileNameFrom(response.headers.get("Content-Disposition")) ?? fallbackName;
    document.body.appendChild(link);
    link.click();
    link.remove();

    // Giving the browser time to start the download
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    
}