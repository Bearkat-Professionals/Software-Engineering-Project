import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { useAuth } from "../auth/AuthProvider";
import ChangePasswordForm from "./ChangePasswordForm";


export default function Layout() {

  const { user, logout, can } = useAuth();
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
              {can("MANAGE_USERS") && <NavLink to="/users">Users</NavLink>}

            </nav>

            <div className="account-controls">

              <span>{user.username} ({user.role})</span>

              <ChangePasswordForm />

              <button type="button" onClick={onSignOut}>Sign out</button>
              
            </div>

          </header>

          <Outlet />

        </>

      );

}
