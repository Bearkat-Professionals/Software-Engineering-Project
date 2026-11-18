import { useCallback, useEffect, useState } from "react";
import { useAuth } from "../auth/AuthProvider";
import { api } from "../components/api";
import { useMessages } from "../components/Messages";

const EMPTY_USER = { username: "", password: "", role: "viewer" };


export default function UsersPage() {

  const { user: signedInUser } = useAuth();
  const { showMessage, clearMessage } = useMessages();
  const [users, setUsers] = useState([]);
  const [newUser, setNewUser] = useState(EMPTY_USER);
  const [loading, setLoading] = useState(true);
  const [waiting, setWaiting] = useState(false);

  const loadUsers = useCallback(async () => {

    try {

      const data = await api("/api/users");
      setUsers(data.users);

    } catch (err) {

      showMessage("error", err.message);

    } finally {

      setLoading(false);

    }

  }, [showMessage]);

  useEffect(() => {

    let ignore = false;

    api("/api/users")
      .then((data) => {

        if (!ignore) setUsers(data.users);

      })
      .catch((err) => {

        if (!ignore) showMessage("error", err.message);

      })
      .finally(() => {

        if (!ignore) setLoading(false);

      });

    return () => {

      ignore = true;

    };

  }, [showMessage]);

  async function addUser(event) {

    event.preventDefault();
    clearMessage();
    setWaiting(true);

    try {

      await api("/api/users", { method: "POST", body: newUser });
      setNewUser(EMPTY_USER);
      await loadUsers();
      showMessage("success", "User added.");

    } catch (err) {

      showMessage("error", err.message);

    } finally {

      setWaiting(false);

    }

  }

  async function change(path, body, message) {

    clearMessage();

    try {

      await api(path, { method: "PUT", body });
      await loadUsers();
      showMessage("success", message);

    } catch (err) {

      showMessage("error", err.message);

    }

  }

  return (

    <main>

      <h1>Users</h1>

      <section aria-labelledby="add-user-heading">

        <h2 id="add-user-heading">Add user</h2>

        <form onSubmit={addUser} className="user-form" autoComplete="off">

          <label>
            Username
            <input required maxLength="100" value={newUser.username}
              onChange={(event) => setNewUser({ ...newUser, username: event.target.value })} />
          </label>

          <label>
            Temporary password
            <input type="password" required minLength="12" maxLength="128" autoComplete="new-password"
              value={newUser.password}
              onChange={(event) => setNewUser({ ...newUser, password: event.target.value })} />
          </label>

          <label>
            Role
            <select value={newUser.role}
              onChange={(event) => setNewUser({ ...newUser, role: event.target.value })}>
              <option value="viewer">Viewer</option>
              <option value="editor">Editor</option>
              <option value="admin">Admin</option>
            </select>
          </label>

          <button type="submit" disabled={waiting}>Add user</button>

        </form>

      </section>

      <section aria-labelledby="accounts-heading">

        <h2 id="accounts-heading">Accounts</h2>

        {loading ? <p>Loading users...</p> : (

          <table>

            <thead>
              <tr>
                <th scope="col">Username</th>
                <th scope="col">Role</th>
                <th scope="col">Status</th>
                <th scope="col">Actions</th>
              </tr>
            </thead>

            <tbody>
              {users.map((user) => (
                <UserRow key={user.user_id} user={user}
                  isSelf={user.user_id === signedInUser.user_id} change={change} />
              ))}
            </tbody>

          </table>

        )}

      </section>

    </main>

  );

}


function UserRow({ user, isSelf, change }) {

  const [password, setPassword] = useState("");

  async function resetPassword(event) {

    event.preventDefault();
    await change(`/api/users/${user.user_id}/password`, { password },
      `Password reset for ${user.username}.`);
    setPassword("");

  }

  return (

    <tr>

      <th scope="row">{user.username}{isSelf ? " (you)" : ""}</th>

      <td>
        <label className="visually-hidden" htmlFor={`role-${user.user_id}`}>Role for {user.username}</label>
        <select id={`role-${user.user_id}`} value={user.role} disabled={isSelf}
          onChange={(event) => change(`/api/users/${user.user_id}/role`,
            { role: event.target.value }, `Role updated for ${user.username}.`)}>
          <option value="viewer">Viewer</option>
          <option value="editor">Editor</option>
          <option value="admin">Admin</option>
        </select>
      </td>

      <td><span className={`status status-${user.account_status}`}>{user.account_status}</span></td>

      <td className="user-actions">

        <button type="button" disabled={isSelf}
          onClick={() => change(`/api/users/${user.user_id}/status`,
            { status: user.account_status === "active" ? "disabled" : "active" },
            `${user.username} ${user.account_status === "active" ? "disabled" : "enabled"}.`)}>
          {user.account_status === "active" ? "Disable" : "Enable"}
        </button>

        <form onSubmit={resetPassword} className="reset-password-form">
          <label className="visually-hidden" htmlFor={`password-${user.user_id}`}>
            New password for {user.username}
          </label>
          <input id={`password-${user.user_id}`} type="password" required minLength="12" maxLength="128"
            autoComplete="new-password" placeholder="New password" value={password}
            onChange={(event) => setPassword(event.target.value)} />
          <button type="submit">Reset password</button>
        </form>

      </td>

    </tr>

  );

}
