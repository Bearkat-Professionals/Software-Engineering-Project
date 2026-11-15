import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useAuth } from "../auth/AuthProvider";
import { useMessages } from "../components/Messages";

export default function LoginPage() {

    const { login } = useAuth();
    const { showMessage, clearMessage } = useMessages();
    const navigate = useNavigate();
    const [params] = useSearchParams();
    const [username, setUsername] = useState("");
    const [password, setPassword] = useState("");
    const [waiting, setWaiting] = useState(false);
    const ended = params.get("ended");

    useEffect(() => {

        if (ended) showMessage("error", "Your session ended. Sign in again to continue.");
    
    }, [ended, showMessage]);

    async function onSubmit(event) {

        event.preventDefault();
        clearMessage();
        setWaiting(true);

        try {

            await login(username, password);
            const next = params.get("next");
            const dest = next && next.startsWith("/") && !next.startsWith("//") ? next: "/scans";

            navigate(dest, { replace: true });

        } catch (err) {

            showMessage("error", err.message);

        } finally {

            setWaiting(false);

        }

    }

    return (

        <main>

            <h1>Sign in</h1>

            <form onSubmit={onSubmit} autoComplete="off">

                <label>

                    Username

                    <input value={username} onChange={(event) => setUsername(event.target.value)} autoComplete="username" />

                </label>

                <label>

                    Password

                    <input type="password" autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} />
                    
                </label>

                <button type="submit" disabled={waiting}>Sign in</button>

            </form>

        </main>

    );

}