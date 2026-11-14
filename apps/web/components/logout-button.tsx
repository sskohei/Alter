"use client";
import { logout } from "../app/login/actions";
export function LogoutButton() {
  return (
    <form
      action={logout}
      onSubmit={() => {
        try {
          for (const key of Object.keys(sessionStorage))
            if (key.startsWith("alter:draft:")) sessionStorage.removeItem(key);
        } catch {
          /* Storage can be disabled. */
        }
      }}
    >
      <button className="secondary">ログアウト</button>
    </form>
  );
}
