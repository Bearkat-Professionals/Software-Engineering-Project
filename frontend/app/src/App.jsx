// Routes for sign-in, scans, devices, and reference data. Signed-out visitors stop at the login page.
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import RequireAuth from "./auth/RequireAuth";
import { AuthProvider } from "./auth/AuthProvider";
import Layout from "./components/Layout";
import { MessageArea } from "./components/Messages";
import LoginPage from "./pages/LoginPage";
import DeviceList from "./views/devices/DeviceList";
import ScanScreen from "./views/scans/ScanScreen";
import ReferenceScreen from "./views/reference_settings/ReferenceScreen";
import ReferenceForm from "./views/reference_settings/ReferenceForm";

export default function App() {

  return (

    <BrowserRouter>

      {/* AuthProvider loads the session before any page asks who is signed in. */}
      <AuthProvider>

        {/* The banner sits above the routes so a message survives a page change. */}
        <MessageArea />

        <Routes>

          {/* Sign-in stays outside RequireAuth so a signed-out visitor can reach it. */}
          <Route path="/login" element={<LoginPage />} />

          {/* Everything below requires a signed-in user. */}
          <Route element={<RequireAuth />}>

            <Route element={<Layout />}>

              <Route path="/" element={<Navigate to="/scans" replace />} />

              <Route path="/scans" element={<ScanScreen />} />

              <Route path="/devices" element={<DeviceList />} />

              {/* /reference opens the vendor tab. :id is "new" or a record id. */}
              <Route path="/reference" element={<Navigate to="/reference/vendors" replace />} />

              <Route path="/reference/:kind" element={<ReferenceScreen />} />

              <Route path="/reference/:kind/:id" element={<ReferenceForm />} />

            </Route>
            
          </Route>

        </Routes>

      </AuthProvider>

    </BrowserRouter>

  );
  
}