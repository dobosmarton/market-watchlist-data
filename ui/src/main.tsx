import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import "./styles.css";

const root = document.getElementById("root");
if (!root) throw new Error("index.html has no #root");

// the server serves this one page for every view and names the view in <html data-view>
createRoot(root).render(
  <StrictMode>
    <App view={document.documentElement.dataset.view ?? ""} />
  </StrictMode>,
);
