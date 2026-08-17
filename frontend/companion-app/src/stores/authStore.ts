import { create } from "zustand";
import { persist } from "zustand/middleware";
import type { User, UserRole, TeamRole } from "@/lib/types";
import { authApi, teamsApi, setAuthToken, extractErrorMessage } from "@/lib/api";

const TEAM_ROLE_RANK: Record<TeamRole, number> = {
  viewer: 0,
  creator: 1,
  publisher: 2,
} as Record<TeamRole, number>;

interface AuthState {
  token: string | null;
  user: User | null;
  isLoading: boolean;
  error: string | null;

  login: (email: string, password: string, rememberMe?: boolean) => Promise<void>;
  register: (email: string, password: string, tiktokHandle?: string) => Promise<void>;
  reactivateAccount: (token: string, password: string) => Promise<void>;
  logout: () => void;
  fetchUser: () => Promise<void>;
  hydrate: () => void;
  isAuthenticated: () => boolean;
  hasRole: (role: UserRole) => boolean;
  // Cosmetic UI gating only — the backend enforces every one of these
  // independently (routers/auth.py's require_role/require_owner). True
  // when the caller is the workspace owner OR holds at least `min` team
  // role. Never trust this alone for anything that matters.
  hasTeamRole: (min: TeamRole) => boolean;
  switchWorkspace: (ownerId: string) => Promise<void>;
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
          const message = extractErrorMessage(err, err instanceof Error ? err.message : "Invalid credentials");
          set({ error: message, isLoading: false });
          throw err;
        }
      },

      register: async (email, password, tiktokHandle) => {
        set({ isLoading: true, error: null });
        try {
          const res = await authApi.register({ email, password, tiktok_handle: tiktokHandle });
          setAuthToken(res.access_token);
          set({ token: res.access_token });
          const user = await authApi.me();
          set({ user, isLoading: false });
        } catch (err: unknown) {
          const message = extractErrorMessage(
            err,
            err instanceof Error ? err.message : "Registration failed. Try again."
          );
          set({ error: message, isLoading: false });
          throw err;
        }
      },

      reactivateAccount: async (token, password) => {
        set({ isLoading: true, error: null });
        try {
          const res = await authApi.reactivateAccount({ token, password });
          setAuthToken(res.access_token);
          set({ token: res.access_token });
          const user = await authApi.me();
          set({ user, isLoading: false });
        } catch (err: unknown) {
          const message = extractErrorMessage(
            err,
            err instanceof Error ? err.message : "This verification link is invalid or has expired."
          );
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
      hasTeamRole: (min) => {
        const workspace = get().user?.workspace;
        if (!workspace) return false;
        if (workspace.is_own) return true;
        const role = workspace.role;
        if (!role) return false;
        return TEAM_ROLE_RANK[role] >= TEAM_ROLE_RANK[min];
      },
      switchWorkspace: async (ownerId) => {
        const res = await teamsApi.switchWorkspace(ownerId);
        setAuthToken(res.access_token);
        set({ token: res.access_token });
        const user = await authApi.me();
        set({ user });
      },
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
