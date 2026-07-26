// Minimal React mount - the actual dashboard lives in public/index.html as
// plain HTML/JS. See App.js for context.
import React from "react";
import ReactDOM from "react-dom/client";
import App from "@/App";

const rootEl = document.getElementById("root");
if (rootEl) {
  ReactDOM.createRoot(rootEl).render(<App />);
}
