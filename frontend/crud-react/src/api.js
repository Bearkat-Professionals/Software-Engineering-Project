export async function api(path, { method = "GET", body } = {}) {

    const response = await fetch(path, {

        method,
        headers: {

            Accept: "application/json",
            ...(body ? { "Content-Type": "application/json"} : {})
            
        },

        body: body ? JSON.stringify(body) : undefined

    });

    if (response.status === 204) return null;

    const payload = await response.json().catch(() => null);

    if (!response.ok) {
        const message = payload && payload.error ? payload.error : response.statusText;
        throw new Error(message);

    }

    return payload;

}