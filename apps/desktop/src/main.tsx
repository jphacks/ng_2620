import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./presentation/shared/index.css";
import { AppRoot } from "./presentation/app/AppRoot";
import { createServices } from "./presentation/app/container";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <AppRoot services={createServices()} />
  </StrictMode>,
);
