// Header with the main navigation, the signed-in user, and sign-out.
import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { useAuth } from "../auth/AuthProvider";
import ChangePasswordForm from "./ChangePasswordForm";


export default function Layout() {

  const { user, logout, can } = useAuth();
  const navigate = useNavigate();

  // Leave the signed-in pages. Logout clears the user even if the server already forgot the session.
  async function onSignOut() {

    await logout();

    navigate("/login", { replace: true });

  }

    return (

        <>

          <header>

            <strong>Network Device Scanner</strong>

            <nav>

              <NavLink to="/scans">Scan</NavLink>
              <NavLink to="/devices">Devices</NavLink>
              {can("MANAGE_USERS") && <NavLink to="/users">Users</NavLink>}
              <NavLink to="/reference/vendors">Reference</NavLink>

            </nav>

            <div className="account-controls">

              <span>{user.username} ({user.role})</span>

              <ChangePasswordForm />

              <button type="button" onClick={onSignOut}>Sign out</button>
              
            </div>

          </header>

          {/* The matched page renders here, under the header. */}
          <Outlet />

        </>

      );

}
