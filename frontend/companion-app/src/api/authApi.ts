// ── authApi ──

import type {
  ForgotPasswordRequest,
  LoginRequest,
  MessageResponse,
  RegisterRequest,
  ResetPasswordRequest,
  TokenResponse,
  User,
} from "@/lib/types";
import { api } from "@/lib/apiClient";

export const authApi = {
  login: (data: LoginRequest) =>
    api.post<TokenResponse>("/auth/login", data).then((r) => r.data),
  register: (data: RegisterRequest) =>
    api.post<TokenResponse>("/auth/register", data).then((r) => r.data),
  me: () => api.get<User>("/auth/me").then((r) => r.data),
  forgotPassword: (data: ForgotPasswordRequest) =>
    api.post<MessageResponse>("/auth/forgot-password", data).then((r) => r.data),
  resetPassword: (data: ResetPasswordRequest) =>
    api.post<MessageResponse>("/auth/reset-password", data).then((r) => r.data),
  changePassword: (data: { current_password: string; new_password: string }) =>
    api.post<MessageResponse>("/auth/change-password", data).then((r) => r.data),
  deleteAccount: (data: { current_password: string }) =>
    api.delete<MessageResponse>("/auth/me", { data }).then((r) => r.data),
};
