// Password strength rules enforced on signup and password reset.
// Keep in sync with backend/orchestrator/schemas/auth.py's password validator.
const SPECIAL_CHAR_REGEX = /[!@#$%^&*(),.?":{}|<>_\-+=[\]\\/;'`~]/;

export const PASSWORD_REQUIREMENTS_TEXT =
  "At least 8 characters, with an uppercase letter, a lowercase letter, and a special character.";

/** Returns an error message if the password is too weak, or null if it's valid. */
export function validatePassword(password: string): string | null {
  if (password.length < 8) {
    return "Password must be at least 8 characters long.";
  }
  if (!/[A-Z]/.test(password)) {
    return "Password must contain at least one uppercase letter.";
  }
  if (!/[a-z]/.test(password)) {
    return "Password must contain at least one lowercase letter.";
  }
  if (!SPECIAL_CHAR_REGEX.test(password)) {
    return "Password must contain at least one special character.";
  }
  return null;
}