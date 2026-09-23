import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { HashRouter } from "react-router-dom";
import { App } from "./App";
import "./styles/global.css";

// HashRouter, not BrowserRouter: a hash route never reaches the server, so a
// deep link works without the server having to know the app's sections (its
// catch-all to index.html is belt and braces, not load-bearing).
const root = document.getElementById("root");
if (!root) throw new Error("#root is missing from index.html");

createRoot(root).render(
  <StrictMode>
    <HashRouter>
      <App />
    </HashRouter>
  </StrictMode>,
);
