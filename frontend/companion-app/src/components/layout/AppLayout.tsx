import { Outlet } from "react-router-dom";
import { Sidebar } from "./Sidebar";

export function AppLayout() {
  return (
    <div className="flex h-screen overflow-hidden">
      <Sidebar />
      <main className="ml-60 flex-1 p-6 overflow-y-auto overflow-x-hidden min-w-0 min-h-0">
        <Outlet />
      </main>
    </div>
  );
}
