import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { MessageArea } from "./components/Messages";
import DevicesScreen from "./views/devices/DevicesScreen";
import ScanScreen from "./views/scans/ScanScreen";

export default function App()  {

  return (

    <BrowserRouter>

      <header>

        <strong>Network Device Scanner</strong>

      </header>

      <MessageArea />

      <Routes>

        <Route path="/" element={<Navigate to="/scans" replace />} />
        <Route path="/scans" element={<ScanScreen />} />
        <Route path="/devices" element={<DevicesScreen />} />

      </Routes>

    </BrowserRouter>

  );
  
}