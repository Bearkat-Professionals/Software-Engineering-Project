// Header with the main navigation, the signed-in user, and sign-out.
import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { useAuth } from "../auth/AuthProvider";


export default function Layout() {

  const {user, logout } = useAuth();
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
              <NavLink to="/reference/vendors">Reference</NavLink>

            </nav>

            <p>

              {user.username} ({user.role})

              <button type="button" onClick={onSignOut}>Sign out</button>
              
            </p>

          </header>

          {/* The matched page renders here, under the header. */}
          <Outlet />

        </>

      );

}
