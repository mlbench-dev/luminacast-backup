import { create } from "zustand";
import { persist } from "zustand/middleware";
import type { User, UserRole } from "@/lib/types";
import { authApi, setAuthToken } from "@/lib/api";
import axios from "axios";

interface AuthState {
  token: string | null;
  user: User | null;
  isLoading: boolean;
  error: string | null;

  login: (email: string, password: string, rememberMe?: boolean) => Promise<void>;
  register: (email: string, password: string, tiktokHandle?: string) => Promise<void>;
  logout: () => void;
  fetchUser: () => Promise<void>;
  hydrate: () => void;
  isAuthenticated: () => boolean;
  hasRole: (role: UserRole) => boolean;
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set, get) => ({
      token: null,
      user: null,
      isLoading: false,
      error: null,

      login: async (email, password, rememberMe) => {
        set({ isLoading: true, error: null });
        try {
          const res = await authApi.login({ email, password, remember_me: rememberMe });
          setAuthToken(res.access_token);
          set({ token: res.access_token });
          const user = await authApi.me();
          set({ user, isLoading: false });
        } catch (err: unknown) {
          let message = "Invalid credentials";
          if (axios.isAxiosError(err) && err.response?.data?.detail) {
            message = err.response.data.detail;
          } else if (err instanceof Error) {
            message = err.message;
          }
          set({ error: message, isLoading: false });
          throw err;
        }
      },

      register: async (email, password, tiktokHandle) => {
        set({ isLoading: true, error: null });
        try {
          console.log("Registering user:", email, password, tiktokHandle);
          const res = await authApi.register({ email, password, tiktok_handle: tiktokHandle });
          console.log("Registration successful:", res);
          setAuthToken(res.access_token);
          set({ token: res.access_token });
          const user = await authApi.me();
          set({ user, isLoading: false });
        } catch (err: unknown) {
          let message = "Registration failed. Try again.";
          if (axios.isAxiosError(err) && err.response?.data?.detail) {
            console.log("Registration failed:", err.response?.data?.detail);

            message = err.response.data.detail;
          } else if (err instanceof Error) {
            message = err.message;
          }
          set({ error: message, isLoading: false });
          throw err;
        }
      },

      logout: () => {
        setAuthToken(null);
        set({ token: null, user: null, error: null });
      },

      fetchUser: async () => {
        const { token } = get();
        if (!token) return;
        try {
          setAuthToken(token);
          const user = await authApi.me();
          set({ user });
        } catch {
          set({ token: null, user: null });
          setAuthToken(null);
        }
      },

      // Call on app start to restore session from localStorage
      hydrate: () => {
        const { token } = get();
        if (token) {
          setAuthToken(token);
          get().fetchUser();
        }
      },

      isAuthenticated: () => !!get().token,
      hasRole: (role) => get().user?.role === role,
    }),
    {
      name: "luminacast-auth",
      partialize: (state) => ({
        token: state.token,
        user: state.user,
      }),
    }
  )
);
