// Calls the local CRUD server. This tester is not part of the product.
// Turn a failed response into an Error whose message is the server's sentence.
async function payloadFrom(response) {

    const payload = await response.json().catch(() => null);

    if (!response.ok) {

        const message = payload && payload.error ? payload.error : response.statusText;
        throw new Error(message);

    }

    return payload;

}

// One row, a create, an update, or a delete. A 204 has no body.
export async function api(path, { method = "GET", body } = {}) {

    const response = await fetch(path, {

        method,
        headers: {

            Accept: "application/json",
            ...(body ? { "Content-Type": "application/json" } : {})

        },

        body: body ? JSON.stringify(body) : undefined

    });

    if (response.status === 204) return null;

    return payloadFrom(response);

}

// A page of rows plus the total count from the X-Total-Count header.
export async function apiPage(path) {

    const response = await fetch(path, {

        headers: { Accept: "application/json" }

    });
    const rows = await payloadFrom(response);

    if (!Array.isArray(rows)) {

        throw new Error("The server returned an invalid page of rows.");

    }

    const totalHeader = response.headers.get("X-Total-Count");
    const total = totalHeader == null ? rows.length : Number(totalHeader);

    if (!Number.isFinite(total)) {

        throw new Error("The server returned an invalid total row count.");

    }

    return { rows, total };

}
