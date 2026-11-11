import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App.jsx";
import { MessageProvider } from "./components/Messages.jsx";
import "./styles/app.css";

createRoot(document.getElementById("root")).render(

  <StrictMode>

    <MessageProvider>

      <App />
      
    </MessageProvider>

  </StrictMode>,
  
);