// One shared message for success, warning, and error text across every page.
import { createContext, useCallback, useContext, useMemo, useState } from "react";

const MessagesContext = createContext();

export function MessageProvider({ children }) {

    // One message at a time. A new one replaces the previous one.
    const [message, setMessage] = useState(null);
    const showMessage = useCallback((kind, text) => setMessage({ kind, text }), []);
    const clearMessage = useCallback(() => setMessage(null), []);
    const value = useMemo(() => ({ message, showMessage, clearMessage }),
    
        [message, showMessage, clearMessage]);

    return (

        <MessagesContext.Provider value={value}>

            {children}

        </MessagesContext.Provider>
    
    );

}

// Pages call this to show or clear the banner. It only works under MessageProvider.
export function useMessages() {

    const value = useContext(MessagesContext);

    if (value == null) {

        throw new Error("useMessages must be used inside MessageProvider");

    }

    return value;

}

// Errors interrupt a screen reader. Other messages are announced politely.
export function MessageArea() {

    const { message } = useMessages();

    if (message == null) return null;

    if(message.kind === "error") {

        return <p className="message error" role="alert">{message.text}</p>;

    }

    return (

        <p className={`message ${message.kind}`} aria-live="polite">

          {message.text}

        </p>

      );
      
}