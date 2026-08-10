import { Outlet } from "react-router-dom";
import { Sidebar } from "./Sidebar";
import { Header } from "./Header";

export function AppLayout() {
  return (
    <div className="flex h-screen overflow-hidden">
      <Sidebar />
      <div className="ml-60 flex-1 flex flex-col min-w-0 min-h-0">
        <Header />
        <main className="flex-1 p-6 overflow-y-auto overflow-x-hidden min-w-0 min-h-0">
          <Outlet />
        </main>
      </div>
    </div>
  );
}