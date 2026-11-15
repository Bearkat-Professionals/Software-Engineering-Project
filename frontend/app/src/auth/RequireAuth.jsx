import { Navigate, Outlet, useLocation } from "react-router-dom";
import { useAuth } from "./AuthProvider";

export default function RequireAuth() {

    const { user } = useAuth();
    const location = useLocation();

    if (user === undefined) return <p>Loading...</p>

    if (user == null) {

        const next = location.pathname + location.search;

        return <Navigate to={`/login?next=${encodeURIComponent(next)}`} replace />;

    }

    return <Outlet />;
    
}