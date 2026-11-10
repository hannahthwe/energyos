import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import Layout from "@/components/Layout";
import Queue from "@/pages/Queue";
import Investigation from "@/pages/Investigation";
import HomeApp from "@/pages/HomeApp";
import "./index.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter>
      <Routes>
        <Route path="/app" element={<HomeApp />} />
        <Route element={<Layout />}>
          <Route path="/support" element={<Queue />} />
          <Route path="/support/cases/:caseId" element={<Investigation />} />
          <Route path="*" element={<Navigate to="/support" replace />} />
        </Route>
      </Routes>
    </BrowserRouter>
  </React.StrictMode>,
);
