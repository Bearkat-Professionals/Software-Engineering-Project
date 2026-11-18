import { Navigate } from "react-router-dom";
import { useAuth } from "./AuthProvider";


export default function RequirePermission({ permission, children }) {

  const { can } = useAuth();

  if (!can(permission)) return <Navigate to="/scans" replace />;

  return children;

}
