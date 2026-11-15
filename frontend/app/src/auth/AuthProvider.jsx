import { createContext, useContext, useEffect, useMemo, useState } from "react";
import { api } from "../components/api";

const AuthContext = createContext(null);

export function AuthProvider({ children }) {

  const [user, setUser] = useState(undefined);
  const [permissions, setPermissions] = useState([]);

  useEffect(() => {

    let ignore = false;

    api("/api/me")

      .then((data) => {

        if (ignore) return;

        setUser(data.user);
        setPermissions(data.permissions);

      })

      .catch(() => {

        if (!ignore) setUser(null);

      });

    return () => {

      ignore = true;

    };

  }, []);

  const value = useMemo(() => ({

    user,

    async login(username, password) {

      const data = await api("/api/login", { method: "POST", body: { username, password } });

      setUser(data.user);
      setPermissions(data.permissions);

    },

    async logout() {

      await api("/api/logout", { method: "POST", body: {} });

      setUser(null);
      setPermissions([]);
      
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