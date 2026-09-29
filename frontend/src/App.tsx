import { Route, Routes } from "react-router-dom";
import { Layout } from "./components/Layout";
import Completed from "./pages/Completed";
import DryScan from "./pages/DryScan";
import Logs from "./pages/Logs";
import Overview from "./pages/Overview";
import ProductDetail from "./pages/ProductDetail";
import ProductIndex from "./pages/ProductIndex";
import Queue from "./pages/Queue";
import References from "./pages/References";
import Review from "./pages/Review";
import Settings from "./pages/Settings";
import Studio from "./pages/Studio";

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<Overview />} />
        <Route path="products" element={<ProductIndex />} />
        <Route path="products/:id" element={<ProductDetail />} />
        <Route path="queue" element={<Queue />} />
        <Route path="studio" element={<Studio />} />
        <Route path="review" element={<Review />} />
        <Route path="completed" element={<Completed />} />
        <Route path="references" element={<References />} />
        <Route path="logs" element={<Logs />} />
        <Route path="settings" element={<Settings />} />
        <Route path="dry-scan" element={<DryScan />} />
        <Route path="*" element={<Overview />} />
      </Route>
    </Routes>
  );
}
