// Sends a signed-out visitor to the login page and remembers where they were going.
import { Navigate, Outlet, useLocation } from "react-router-dom";
import { useAuth } from "./AuthProvider";

export default function RequireAuth() {

    const { user } = useAuth();
    const location = useLocation();

    // Wait until /api/me finishes. undefined is not the same as signed out.
    if (user === undefined) return <p>Loading...</p>

    if (user == null) {

        // next brings the visitor back here after a successful sign-in.
        const next = location.pathname + location.search;

        return <Navigate to={`/login?next=${encodeURIComponent(next)}`} replace />;

    }

    return <Outlet />;
    
}