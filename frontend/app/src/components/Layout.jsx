import { NavLink, Outlet } from "react-router-dom";
import { MessageArea } from "./Messages";

export default function Layout() {

    return (

        <>

          <header>

            <strong>Network Device Scanner</strong>

            <nav>

              <NavLink to="/scans">Scan</NavLink>
              <NavLink to="/devices">Devices</NavLink>

            </nav>

          </header>

          <MessageArea />
          <Outlet />

        </>

      );

}
