// Starts the product app and provides the shared message area.
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App.jsx";
import { MessageProvider } from "./components/Messages.jsx";
import "./styles/app.css";

// MessageProvider wraps the app so every page can show the same banner.
createRoot(document.getElementById("root")).render(

  <StrictMode>

    <MessageProvider>

      <App />
      
    </MessageProvider>

  </StrictMode>,
  
);