import { useState } from "react";
import { api } from "./api";
import { useMessages } from "./Messages";


export default function ChangePasswordForm() {

  const { showMessage, clearMessage } = useMessages();
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [waiting, setWaiting] = useState(false);

  async function onSubmit(event) {

    event.preventDefault();
    clearMessage();
    setWaiting(true);

    try {

      await api("/api/me/password", {
        method: "PUT",
        body: { current_password: currentPassword, new_password: newPassword },
      });
      setCurrentPassword("");
      setNewPassword("");
      showMessage("success", "Password changed.");

    } catch (err) {

      showMessage("error", err.message);

    } finally {

      setWaiting(false);

    }

  }

  return (

    <details className="account-menu">

      <summary>Change password</summary>

      <form onSubmit={onSubmit} className="compact-form">

        <label>
          Current password
          <input type="password" autoComplete="current-password" required
            value={currentPassword} onChange={(event) => setCurrentPassword(event.target.value)} />
        </label>

        <label>
          New password
          <input type="password" autoComplete="new-password" required minLength="12" maxLength="128"
            value={newPassword} onChange={(event) => setNewPassword(event.target.value)} />
        </label>

        <button type="submit" disabled={waiting}>Change password</button>

      </form>

    </details>

  );

}
