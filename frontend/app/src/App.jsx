import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import RequireAuth from "./auth/RequireAuth";
import RequirePermission from "./auth/RequirePermission";
import { AuthProvider } from "./auth/AuthProvider";
import Layout from "./components/Layout";
import { MessageArea } from "./components/Messages";
import LoginPage from "./pages/LoginPage";
import UsersPage from "./pages/UsersPage";
import DeviceList from "./views/devices/DeviceList";
import ScanScreen from "./views/scans/ScanScreen";

export default function App() {

  return (

    <BrowserRouter>

      <AuthProvider>

        <MessageArea />

        <Routes>

          <Route path="/login" element={<LoginPage />} />

          <Route element={<RequireAuth />}>

            <Route element={<Layout />}>

              <Route path="/" element={<Navigate to="/scans" replace />} />

              <Route path="/scans" element={<ScanScreen />} />

              <Route path="/devices" element={<DeviceList />} />

              <Route path="/users" element={
                <RequirePermission permission="MANAGE_USERS">
                  <UsersPage />
                </RequirePermission>
              } />

            </Route>
            
          </Route>

        </Routes>

      </AuthProvider>

    </BrowserRouter>

  );
  
}
