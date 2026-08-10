// Password strength rules enforced on signup and password reset.
// Keep in sync with backend/orchestrator/schemas/auth.py's password validator.
//
// Only the characters that are actually regex-syntax characters get escaped
// (rather than hand-written inside `[...]`) because the browser compiles the
// HTML `pattern` attribute using the stricter `v` regex flag: unescaped
// `( ) [ ] { } / - \ |` inside a class are syntax errors under `v`, but so is
// escaping anything else (e.g. `\!`) — `v` rejects "useless" escapes that
// classic regex mode silently allows.
const SPECIAL_CHARS = [
  "!", "@", "#", "$", "%", "^", "&", "*", "(", ")", ",", ".", "?",
  "\"", ":", "{", "}", "|", "<", ">", "_", "-", "+", "=", "[", "]",
  "\\", "/", ";", "'", "`", "~",
];
const CLASS_SYNTAX_CHARS = new Set(["(", ")", "[", "]", "{", "}", "/", "-", "\\", "|", "&"]);
const SPECIAL_CHAR_REGEX = new RegExp(
  `[${SPECIAL_CHARS.map((c) => (CLASS_SYNTAX_CHARS.has(c) ? `\\${c}` : c)).join("")}]`
);

export const PASSWORD_REQUIREMENTS_TEXT =
  "At least 8 characters, with an uppercase letter, a lowercase letter, and a special character.";

// Same rules as validatePassword() below, expressed as an HTML `pattern` source
// (no ^/$ — the browser wraps those) so the browser's native constraint-validation
// popup can enforce password strength itself, instead of routing it through a toast.
export const PASSWORD_PATTERN = `(?=.*[A-Z])(?=.*[a-z])(?=.*${SPECIAL_CHAR_REGEX.source}).{8,}`;

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