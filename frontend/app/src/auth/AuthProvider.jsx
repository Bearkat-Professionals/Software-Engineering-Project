import { createContext, useContext, useEffect, useMemo, useState } from "react";
import { api } from "../components/api";
import { useMessages } from "../components/Messages";

const AuthContext = createContext(null);

export function AuthProvider({ children }) {

  const [user, setUser] = useState(undefined);
  const [permissions, setPermissions] = useState([]);
  const { showMessage } = useMessages();

  useEffect(() => {

    let ignore = false;

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

    async login(username, password) {

      const data = await api("/api/login", { method: "POST", body: { username, password } });

      setUser(data.user);
      setPermissions(data.permissions);

    },

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