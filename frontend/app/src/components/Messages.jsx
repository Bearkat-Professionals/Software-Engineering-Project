import { createContext, useCallback, useContext, useMemo, useState } from "react";

const MessagesContext = createContext();

export function MessageProvider({ children }) {

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

export function useMessages() {

    const value = useContext(MessagesContext);

    if (value == null) {

        throw new Error("useMessages must be used inside MessageProvider");

    }

    return value;

}

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