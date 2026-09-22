import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { HashRouter } from "react-router-dom";
import { App } from "./App";
import "./styles/global.css";

// HashRouter, not BrowserRouter: serve.py is a stdlib handler with an explicit
// route table, and a hash route never reaches it — so a deep link works without
// the server needing a catch-all rewrite for every future section.
const root = document.getElementById("root");
if (!root) throw new Error("#root is missing from index.html");

createRoot(root).render(
  <StrictMode>
    <HashRouter>
      <App />
    </HashRouter>
  </StrictMode>,
);
