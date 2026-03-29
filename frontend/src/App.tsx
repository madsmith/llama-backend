import { BrowserRouter, Routes, Route } from "react-router-dom";
import Layout from "./components/Layout";
import Dashboard from "./pages/Dashboard";
import Logs from "./pages/Logs";
import Properties from "./pages/Properties";
import Slots from "./pages/Slots";
import Settings from "./pages/Settings";

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<Dashboard />} />
          <Route path=":modelSuid/properties" element={<Properties />} />
          <Route path=":modelSuid/slots" element={<Slots />} />
          <Route path="logs" element={<Logs />} />
          <Route path="logs/:modelSuid" element={<Logs />} />
          <Route path="settings" element={<Settings />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
