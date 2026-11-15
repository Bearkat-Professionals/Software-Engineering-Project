import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { useAuth } from "../auth/AuthProvider";


export default function Layout() {

  const {user, logout } = useAuth();
  const navigate = useNavigate();

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

            </nav>

            <p>

              {user.username} ({user.role})

              <button type="button" onClick={onSignOut}>Sign out</button>
              
            </p>

          </header>

          <Outlet />

        </>

      );

}
