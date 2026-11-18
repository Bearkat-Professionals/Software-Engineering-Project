// Loads the signed-in user and exposes login, logout, and permission checks.
import { createContext, useContext, useEffect, useMemo, useState } from "react";
import { api } from "../components/api";
import { useMessages } from "../components/Messages";

const AuthContext = createContext(null);

export function AuthProvider({ children }) {

  // undefined means "still loading". null means signed out.
  const [user, setUser] = useState(undefined);
  const [permissions, setPermissions] = useState([]);
  const { showMessage } = useMessages();

  useEffect(() => {

    let ignore = false;

    // Ask the server who the session belongs to. A 401 is a normal signed-out start.
    api("/api/me")

      .then((data) => {

        if (ignore) return;

        setUser(data.user);
        setPermissions(data.permissions);

      })

      .catch((err) => {

        if (ignore) return;

        setUser(null);

        if (err.status !== 401) showMessage("error", err.message);

      });

    return () => {

      ignore = true;

    };

  }, [showMessage]);

  const value = useMemo(() => ({

    user,

    // Store the user and permissions from a successful sign-in.
    async login(username, password) {

      const data = await api("/api/login", { method: "POST", body: { username, password } });

      setUser(data.user);
      setPermissions(data.permissions);

    },

    // Clear the local user even when the server says the session is already gone.
    async logout() {

      try {

        await api("/api/logout", { method: "POST", body: {} });

      } catch {

        // The session may already be gone. Sign out on this side anyway.

      } finally {

        setUser(null);
        setPermissions([]);

      }

    },

    // True when the signed-in role includes this permission name.
    can(permission) {

      return permissions.includes(permission);

    },

  }), [user, permissions]);


  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;

}

export function useAuth() {

  const value = useContext(AuthContext);

  if (value == null) throw new Error("useAuth must be used inside AuthProvider");

  return value;
  
}