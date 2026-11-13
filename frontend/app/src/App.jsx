import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import DeviceList from "./views/devices/DeviceList";
import ScanScreen from "./views/scans/ScanScreen";

export default function App() {

  return (

    <BrowserRouter>

      <Routes>

        <Route element={<Layout />}>

          <Route path="/" element={<Navigate to="/scans" replace />} />

          <Route path="/scans" element={<ScanScreen />} />

          <Route path="/devices" element={<DeviceList />} />

        </Route>

      </Routes>

    </BrowserRouter>
    
  );

}